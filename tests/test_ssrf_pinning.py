"""The fetch must go to the address that was checked, not to a fresh lookup.

``app/sources/security.py`` has always described four SSRF guards and called
the fourth — pinning the connection to the validated address — "the one people
skip". LANA skipped it: ``CheckedUrl.ip`` was computed and then thrown away,
and ``httpx`` was handed the original URL, so it performed its own DNS lookup
at connect time. Two resolutions, one checked and one used.

That is a textbook DNS-rebinding window. A hostname with a one second TTL
answers the validation lookup with a public address, passes every check, and
then answers the connection lookup with ``127.0.0.1``. SECURITY.md stated the
protection was in place, which made it worse than a known gap.

These tests assert the mechanism rather than the intent: what URL is actually
opened, what ``Host`` accompanies it, and what name TLS is verified against.
"""

from __future__ import annotations

import httpx
import pytest

from app.sources import SourceSpec, build_source
from app.sources.security import CheckedUrl, check_url

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def _no_private_escape_hatch(monkeypatch):
    monkeypatch.delenv("LANA_ALLOW_PRIVATE_SOURCE_URLS", raising=False)


@pytest.fixture
def resolves_public(monkeypatch):
    """Every lookup during validation returns one public address."""
    monkeypatch.setattr(
        "app.sources.security._resolve_all", lambda host, port: [PUBLIC_IP]
    )


# ── CheckedUrl carries what pinning needs ───────────────────────────────────

def test_check_url_records_every_validated_address(monkeypatch):
    monkeypatch.setattr(
        "app.sources.security._resolve_all",
        lambda host, port: ["93.184.216.34", "93.184.216.35"],
    )
    checked = check_url("https://api.example.com/v1/orders")
    # All of them were checked, so all of them are usable — a host whose first
    # record is unreachable from this network must not become unfetchable.
    assert checked.ips == ("93.184.216.34", "93.184.216.35")


def test_the_pinned_url_replaces_only_the_host():
    checked = CheckedUrl("https://api.example.com/v1/orders?since=2026-01-01",
                         "api.example.com", 443, PUBLIC_IP, "https")
    pinned = checked.pinned_url(PUBLIC_IP)
    assert pinned == f"https://{PUBLIC_IP}:443/v1/orders?since=2026-01-01"


def test_an_ipv6_address_is_bracketed():
    checked = CheckedUrl("https://api.example.com/x", "api.example.com", 443,
                         "2606:2800:220:1:248:1893:25c8:1946", "https")
    assert "[2606:2800:220:1:248:1893:25c8:1946]:443" in checked.pinned_url(
        "2606:2800:220:1:248:1893:25c8:1946"
    )


def test_url_credentials_survive_pinning():
    checked = CheckedUrl("https://user:pw@api.example.com/x", "api.example.com",
                         443, PUBLIC_IP, "https")
    assert checked.pinned_url(PUBLIC_IP).startswith("https://user:pw@")


@pytest.mark.parametrize("scheme,port,expected", [
    ("https", 443, "api.example.com"),
    ("http", 80, "api.example.com"),
    ("https", 8443, "api.example.com:8443"),
])
def test_the_host_header_names_the_site_not_the_address(scheme, port, expected):
    checked = CheckedUrl(f"{scheme}://api.example.com/x", "api.example.com",
                         port, PUBLIC_IP, scheme)
    assert checked.host_header == expected


# ── The connector actually uses it ──────────────────────────────────────────

class _Recorder:
    """A transport that records the request and returns a fixed JSON body."""

    def __init__(self):
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            200, json=[{"a": 1}, {"a": 2}],
            headers={"content-type": "application/json"},
            request=request,
        )


def _source(monkeypatch, recorder, target="https://api.example.com/v1/orders"):
    source = build_source(SourceSpec(kind="url", target=target))
    real_client = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(recorder.handle_request)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("app.sources.rest.httpx.Client", patched)
    return source


def test_the_request_is_sent_to_the_validated_address(resolves_public, monkeypatch):
    recorder = _Recorder()
    source = _source(monkeypatch, recorder)

    result = source.fetch()

    assert len(result.frame) == 2
    sent = recorder.requests[0]
    # The URL opened carries the address, never the hostname — that is the
    # difference between checking a resolution and using it.
    assert sent.url.host == PUBLIC_IP
    assert sent.url.path == "/v1/orders"


def test_the_host_header_still_names_the_original_site(resolves_public, monkeypatch):
    recorder = _Recorder()
    source = _source(monkeypatch, recorder)
    source.fetch()

    # Without this, pinning would break every virtual host and CDN-backed API.
    assert recorder.requests[0].headers["host"] == "api.example.com"


def test_tls_is_verified_against_the_hostname_not_the_address(
    resolves_public, monkeypatch
):
    recorder = _Recorder()
    source = _source(monkeypatch, recorder)
    source.fetch()

    # The certificate must still be checked against api.example.com. The
    # tempting "fix" for a pinned HTTPS request is to disable verification;
    # this asserts LANA did the other thing.
    assert recorder.requests[0].extensions.get("sni_hostname") == "api.example.com"


def test_plain_http_sets_no_sni(monkeypatch):
    monkeypatch.setattr(
        "app.sources.security._resolve_all", lambda host, port: [PUBLIC_IP]
    )
    recorder = _Recorder()
    source = _source(monkeypatch, recorder, target="http://api.example.com/v1/orders")
    source.fetch()
    assert "sni_hostname" not in recorder.requests[0].extensions


def test_no_hostname_reaches_the_transport(resolves_public, monkeypatch):
    """The rebinding window is closed by there being no second lookup at all.

    The attack needs the client to resolve the name again at connect time and
    get a different answer. It cannot: what reaches the transport is an IP
    literal, so whatever DNS would say next has no way to change where the
    bytes go. Asserting "the destination is not a name" is the property; a
    test that merely watched a resolver could pass while the client quietly
    did its own lookup underneath.
    """
    import ipaddress

    recorder = _Recorder()
    source = _source(monkeypatch, recorder)
    source.fetch()

    destination = str(recorder.requests[0].url.host)
    # Raises for anything that is not a literal address.
    assert ipaddress.ip_address(destination.strip("[]")) == ipaddress.ip_address(
        PUBLIC_IP
    )


def test_every_redirect_hop_is_revalidated_and_pinned(monkeypatch):
    """A public URL 302-ing to the metadata service is refused at the hop."""
    monkeypatch.setattr(
        "app.sources.security._resolve_all",
        lambda host, port: ["127.0.0.1"] if host == "169.254.169.254"
        else [PUBLIC_IP],
    )

    class _Redirector:
        def __init__(self):
            self.requests: list[httpx.Request] = []

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(
                302,
                headers={"location": "http://169.254.169.254/latest/meta-data/"},
                request=request,
            )

    recorder = _Redirector()
    source = _source(monkeypatch, recorder)

    from app.sources import SourceRefused

    with pytest.raises(SourceRefused):
        source.fetch()
    # The first hop went out; the second was refused before a socket opened.
    assert len(recorder.requests) == 1


def test_a_second_address_is_tried_when_the_first_is_unreachable(monkeypatch):
    monkeypatch.setattr(
        "app.sources.security._resolve_all",
        lambda host, port: ["93.184.216.34", "93.184.216.35"],
    )

    class _FirstFails:
        def __init__(self):
            self.requests: list[httpx.Request] = []

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if request.url.host == "93.184.216.34":
                raise httpx.ConnectError("no route to host", request=request)
            return httpx.Response(
                200, json=[{"a": 1}],
                headers={"content-type": "application/json"}, request=request,
            )

    recorder = _FirstFails()
    source = _source(monkeypatch, recorder)

    result = source.fetch()
    assert len(result.frame) == 1
    assert [r.url.host for r in recorder.requests] == [
        "93.184.216.34", "93.184.216.35",
    ]
