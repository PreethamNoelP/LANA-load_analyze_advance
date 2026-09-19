"""HTTP sources: a REST endpoint or a file published at a URL.

Covers "point LANA at this API" and "point LANA at this CSV on the web" with
one connector, because the only real difference is how the response body is
parsed and that is decided from the content type.

Safety is the substance here, not the fetching. Every request goes through
``app.sources.security.check_url``, and — critically — every *redirect* does
too, because a public URL that 302s to ``169.254.169.254`` is the standard way
past a checker that only looks at the URL it was given. Redirects are
therefore followed manually rather than by the HTTP client.

Response size is bounded while streaming, not after. ``response.read()`` on a
URL that streams forever is an OOM with a polite name, and a ``Content-Length``
header is a claim by the server, not a fact.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd

from .base import (
    DEFAULT_ROW_LIMIT,
    ConnectionTest,
    DataSource,
    FetchResult,
    SourceCapabilities,
    SourceConfigError,
    SourceConnectionError,
    SourceRefused,
    SourceUnavailable,
    normalize_frame,
    redact,
)
from .security import MAX_REDIRECTS, check_url

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover
    httpx = None
    HTTPX_AVAILABLE = False

# Hard ceiling on a downloaded body. Above this the right answer is "export it
# and upload the file", not a longer timeout.
MAX_RESPONSE_BYTES = 128 * 1024 ** 2

REQUEST_TIMEOUT_SECONDS = 30.0


class RestSource(DataSource):
    kind = "url"
    description = (
        "A REST API or a file published over HTTP(S) — JSON, CSV or NDJSON. "
        "Private and loopback addresses are refused by default."
    )
    capabilities = SourceCapabilities(
        lists_entities=False,
        needs_entity=False,
        accepts_query=False,
        uses_secret=True,
        target_label="URL",
        target_placeholder="https://api.example.com/v1/orders",
    )

    def validate_spec(self) -> None:
        if not HTTPX_AVAILABLE:
            raise SourceUnavailable(
                "httpx is not installed, so URL sources are unavailable. "
                "Install it with `pip install httpx`."
            )
        self._require(self.spec.target, "URL", "e.g. https://api.example.com/orders")
        # Raises SourceRefused for a private address, a bad scheme or a
        # disallowed port — before any socket is opened.
        check_url(self.spec.target)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/csv;q=0.9, */*;q=0.5",
                   "User-Agent": "LANA/2.1 (+local data analysis)"}
        extra = self.spec.options.get("headers")
        if isinstance(extra, dict):
            headers.update({str(k): str(v) for k, v in extra.items()})
        if self.spec.secret:
            scheme = str(self.spec.options.get("auth_scheme", "Bearer"))
            headers["Authorization"] = f"{scheme} {self.spec.secret}".strip()
        return headers

    def _open(self, client: Any, checked: Any):
        """Open a streamed GET against a *validated address*, not a hostname.

        This is where guard 4 in ``app.sources.security`` stops being a
        comment and starts being code. Handing ``httpx`` the original URL
        makes the client do its own DNS lookup — a second resolution, after
        the one that was checked — so a hostname with a one second TTL can
        answer the check with a public address and the connection with
        ``127.0.0.1``. The whole point of validating every address is lost to
        the gap between the two lookups.

        So the request goes to an address that was checked, with:

        * ``Host:`` still naming the original hostname, because the server is
          being asked for that site — pinning changes where the bytes go, not
          what is requested. Virtual hosts and CDNs need this to route.
        * ``sni_hostname`` set to the original hostname on TLS, so the
          handshake offers the right name *and* the certificate is verified
          against it. Without this, pinning would either break every HTTPS
          fetch or — worse — be "fixed" by disabling verification.

        Each validated address is tried in turn: they were all checked, and a
        host whose first record is unreachable from this network (an AAAA on
        an IPv4-only host is routine) must still work.
        """
        headers = {**self._headers(), "Host": checked.host_header}
        extensions = (
            {"sni_hostname": checked.host} if checked.scheme == "https" else None
        )

        last_error: Exception | None = None
        for ip in checked.ips:
            request = client.build_request(
                "GET", checked.pinned_url(ip), headers=headers,
                extensions=extensions,
            )
            try:
                return client.send(request, stream=True)
            except httpx.HTTPError as exc:
                last_error = exc
        raise last_error if last_error is not None else SourceConnectionError(
            f"{redact(checked.url)} resolved to no usable address."
        )

    def _get(self, url: str) -> Any:
        """Fetch with manual redirect handling, re-validating every hop."""
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            checked = check_url(current)
            try:
                with httpx.Client(
                    follow_redirects=False, timeout=REQUEST_TIMEOUT_SECONDS
                ) as client:
                    response = self._open(client, checked)
                    try:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise SourceConnectionError(
                                    "The server sent a redirect with no destination."
                                )
                            # Resolved against the *original* URL, not the
                            # pinned one: a relative Location must rebuild the
                            # hostname, or the next hop would be checked as an
                            # IP literal and lose its own name resolution.
                            current = str(httpx.URL(checked.url).join(location))
                            continue
                        if response.status_code >= 400:
                            raise SourceConnectionError(
                                f"The server returned HTTP {response.status_code} "
                                f"for {redact(checked.url)}."
                            )
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            # Enforced against bytes actually received, not
                            # against the Content-Length the server claimed.
                            if len(body) > MAX_RESPONSE_BYTES:
                                raise SourceRefused(
                                    f"The response exceeded "
                                    f"{MAX_RESPONSE_BYTES // 1024 ** 2} MB and was "
                                    f"stopped. Export the data and upload the file "
                                    f"instead."
                                )
                        return bytes(body), response.headers.get("content-type", "")
                    finally:
                        response.close()
            except httpx.HTTPError as exc:
                raise SourceConnectionError(
                    f"Could not fetch {redact(current)}: {redact(str(exc))[:200]}"
                ) from exc
        raise SourceRefused(
            f"The URL redirected more than {MAX_REDIRECTS} times."
        )

    # ── Parsing ──────────────────────────────────────────────────────────────

    def _to_frame(self, body: bytes, content_type: str) -> tuple[pd.DataFrame, str]:
        """Parse a response body into a frame, and say how it was read."""
        lowered = (content_type or "").lower()
        url_lower = self.spec.target.lower()

        if "csv" in lowered or url_lower.endswith(".csv"):
            return pd.read_csv(io.BytesIO(body)), "CSV"
        if "ndjson" in lowered or "jsonl" in lowered or url_lower.endswith((".ndjson", ".jsonl")):
            return pd.read_json(io.BytesIO(body), lines=True), "NDJSON"

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # Fall back to CSV before giving up: plenty of endpoints serve CSV
            # as application/octet-stream.
            try:
                return pd.read_csv(io.BytesIO(body)), "CSV (content type was not specific)"
            except Exception:
                raise SourceConnectionError(
                    "The response is neither valid JSON nor parseable as CSV. "
                    f"Content-Type was '{content_type or 'unset'}'."
                ) from exc

        return self._frame_from_json(payload)

    def _frame_from_json(self, payload: Any) -> tuple[pd.DataFrame, str]:
        """Find the records in a JSON payload.

        APIs bury the array: ``{"data": [...]}``, ``{"results": [...]}``,
        ``{"items": {"records": [...]}}``. Guessing wrong yields a one-row
        frame of metadata, so the search is explicit and its outcome is
        reported in the notes rather than left for the user to infer.
        """
        if isinstance(payload, list):
            return pd.json_normalize(payload), "a JSON array"

        if isinstance(payload, dict):
            explicit = self.spec.options.get("records_path")
            if explicit:
                node: Any = payload
                for part in str(explicit).split("."):
                    if not isinstance(node, dict) or part not in node:
                        raise SourceConfigError(
                            f"records_path '{explicit}' does not exist in this "
                            f"response."
                        )
                    node = node[part]
                if not isinstance(node, list):
                    raise SourceConfigError(
                        f"records_path '{explicit}' is not a list of records."
                    )
                return pd.json_normalize(node), f"the '{explicit}' field"

            for key in ("data", "results", "records", "items", "rows", "value"):
                value = payload.get(key)
                if isinstance(value, list) and value:
                    return pd.json_normalize(value), f"the '{key}' field"

            # A dict of scalars is a single record, which is legitimate.
            if all(not isinstance(v, (dict, list)) for v in payload.values()):
                return pd.json_normalize([payload]), "a single JSON object"

            raise SourceConfigError(
                "Could not find a list of records in this response. Set the "
                "records_path option to the field holding the array, e.g. "
                "'data' or 'result.items'."
            )

        raise SourceConnectionError(
            "The response was valid JSON but not an object or array."
        )

    # ── Contract ─────────────────────────────────────────────────────────────

    def test_connection(self) -> ConnectionTest:
        try:
            body, content_type = self._get(self.spec.target)
            frame, how = self._to_frame(body, content_type)
        except (SourceRefused, SourceConfigError, SourceConnectionError) as exc:
            return ConnectionTest(False, str(exc))

        return ConnectionTest(
            ok=True,
            detail=(
                f"Fetched {len(body):,} bytes and read {len(frame):,} records "
                f"from {how}."
            ),
            entities=[str(c) for c in frame.columns][:100],
        )

    def fetch(self, *, limit: int = DEFAULT_ROW_LIMIT) -> FetchResult:
        body, content_type = self._get(self.spec.target)
        frame, how = self._to_frame(body, content_type)

        truncated = len(frame) > limit
        if truncated:
            frame = frame.head(limit)

        notes = [f"Read {len(frame):,} records from {how}."]
        if truncated:
            notes.append(f"Row cap of {limit:,} applied after download.")

        from urllib.parse import urlparse

        parsed = urlparse(self.spec.target)
        label = f"{parsed.netloc}{parsed.path}" or self.spec.target

        return FetchResult(
            frame=normalize_frame(frame),
            label=redact(label),
            row_limit_applied=truncated,
            notes=notes,
        )
