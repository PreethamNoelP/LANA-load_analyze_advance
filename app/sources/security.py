"""SSRF defence for connectors that fetch a user-supplied URL.

The threat
----------
"Analyse the data at this URL" hands an attacker a HTTP client running inside
the network LANA is deployed in. The classic payloads are not exotic:

* ``http://169.254.169.254/latest/meta-data/iam/security-credentials/`` — the
  cloud instance metadata service, which on a misconfigured host returns
  credentials in plain text.
* ``http://localhost:8000/health`` — LANA reading itself, or any other service
  bound to loopback that assumed only local callers could reach it.
* ``http://10.0.0.5:9200/_search`` — an internal Elasticsearch with no auth
  because "it's not exposed".

LANA is local-first, so for the single-user case this is mostly the user
attacking their own machine. It stops being that the moment anyone runs it on
a shared host — which the project explicitly supports via ``LANA_AUTH_TOKEN``
— and at that point an unguarded URL fetch is the most serious hole in the
application by a wide margin.

The defence
-----------
Resolve first, then decide, then connect to the address that was checked:

1. Scheme must be http or https. This removes ``file://``, ``gopher://``,
   ``ftp://`` and the rest in one move.
2. The hostname is resolved to every address it has, and *all* of them must be
   public. Checking one is not enough — a name with an A record for a public
   IP and another for ``127.0.0.1`` passes a first-match check.
3. Redirects are followed manually, one at a time, with the same check applied
   to each hop. A public URL 302-ing to the metadata service is the standard
   bypass for a check that only looks at the first URL.
4. The connection is pinned to the address that was validated, which closes
   the DNS-rebinding window between the check and the connect.

Guard 4 is the one people skip. Without it, a hostname whose TTL is one second
can answer the validation lookup with a public address and the connection
lookup with ``127.0.0.1``.

``LANA_ALLOW_PRIVATE_SOURCE_URLS=true`` disables 2-4 for the genuine case of
pointing LANA at an internal API on purpose. It is off by default, and
``SECURITY.md`` states what turning it on means.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from .base import SourceRefused

ALLOWED_SCHEMES = ("http", "https")

# Redirect hops followed before giving up. Real APIs need one or two; a long
# chain is either a misconfiguration or an attempt to outlast the checker.
MAX_REDIRECTS = 5

# Ports a fetch may target. Blocks the "use the HTTP client to talk to Redis"
# family of attacks, where a crafted URL path becomes a valid command to a
# non-HTTP service that tolerates garbage before it.
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443, 3000, 5000, 8000, 9000})


def private_urls_allowed() -> bool:
    """Read at call time, not import time, so tests can toggle it."""
    return os.getenv("LANA_ALLOW_PRIVATE_SOURCE_URLS", "false").lower() in (
        "1", "true", "yes",
    )


@dataclass(frozen=True)
class CheckedUrl:
    """A URL that passed every check, plus the addresses it may be sent to.

    ``ips`` holds *every* address the hostname resolved to during validation,
    not just the first. All of them were checked — that is guard 2 — so any is
    safe to connect to, and carrying the whole set means pinning does not
    break a host whose first address happens to be unreachable (an AAAA
    record on an IPv4-only network is the everyday case).
    """

    url: str
    host: str
    port: int
    ip: str
    scheme: str
    ips: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.ips:
            object.__setattr__(self, "ips", (self.ip,))

    @property
    def host_header(self) -> str:
        """What ``Host:`` must say once the connection is made to an address.

        The server is still being asked for the original hostname — pinning
        changes *where* the bytes go, not *what* is requested. Omitting the
        port matches what a client would send by default; including it for a
        non-default port matches what one would send there.
        """
        default_port = 443 if self.scheme == "https" else 80
        return self.host if self.port == default_port else f"{self.host}:{self.port}"

    def pinned_url(self, ip: str) -> str:
        """``url`` with the hostname replaced by a validated address.

        This is the mechanism behind guard 4. The alternative — connecting by
        name and hoping the second lookup returns what the first one did — is
        a check on one resolution and a connection to another, which a one
        second TTL is enough to exploit.
        """
        parsed = urlparse(self.url.strip())
        literal = f"[{ip}]" if ":" in ip else ip
        netloc = f"{literal}:{self.port}"
        # Credentials in the URL are preserved; dropping them would silently
        # turn an authenticated fetch into an anonymous one.
        if parsed.username:
            credentials = parsed.username
            if parsed.password:
                credentials += f":{parsed.password}"
            netloc = f"{credentials}@{netloc}"
        return urlunparse(parsed._replace(netloc=netloc))


def _is_public(address: str) -> bool:
    """Whether an IP is routable on the public internet.

    ``ipaddress`` already knows about loopback, link-local (which covers the
    cloud metadata address), private ranges, multicast and reserved space, so
    this is one library call rather than a hand-maintained CIDR list that
    would inevitably miss a range. IPv6 unique-local and mapped-IPv4 are
    covered by the same properties.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_all(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SourceRefused(
            f"'{host}' could not be resolved. Check the hostname."
        ) from exc
    return sorted({info[4][0] for info in infos})


def check_url(raw_url: str) -> CheckedUrl:
    """Validate one URL and return the address a fetch should be pinned to.

    Raises :class:`SourceRefused` with a message that says what was wrong and
    how to proceed, because the usual cause is someone legitimately pointing
    at an internal service and needing to know the flag exists.
    """
    if not raw_url or not raw_url.strip():
        raise SourceRefused("A URL is required.")

    parsed = urlparse(raw_url.strip())
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise SourceRefused(
            f"Only http and https URLs can be fetched; got "
            f"'{parsed.scheme or 'no scheme'}'."
        )
    host = parsed.hostname
    if not host:
        raise SourceRefused("That URL has no hostname.")

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)

    if private_urls_allowed():
        # Still resolve, so the caller gets a pinned address either way and the
        # fetch path has one code path rather than two.
        addresses = _resolve_all(host, port)
        return CheckedUrl(raw_url, host, port, addresses[0], parsed.scheme.lower(),
                          tuple(addresses))

    if port not in ALLOWED_PORTS:
        raise SourceRefused(
            f"Port {port} is not allowed for URL sources. Permitted ports: "
            f"{', '.join(str(p) for p in sorted(ALLOWED_PORTS))}."
        )

    addresses = _resolve_all(host, port)
    private = [a for a in addresses if not _is_public(a)]
    if private:
        raise SourceRefused(
            f"'{host}' resolves to a private or loopback address "
            f"({private[0]}), which LANA will not fetch. This protects cloud "
            f"metadata endpoints and internal services from being read through "
            f"the app. To connect to an internal source deliberately, set "
            f"LANA_ALLOW_PRIVATE_SOURCE_URLS=true."
        )

    return CheckedUrl(raw_url, host, port, addresses[0], parsed.scheme.lower(),
                      tuple(addresses))
