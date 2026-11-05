"""URL canonicalisation and hashing (documents are keyed by url_hash; TDD §5.4)."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "mc_", "fbclid", "gclid", "ref", "ref_src", "cmpid", "ocid", "sr_share")


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = f":{parts.port}" if parts.port and parts.port not in (80, 443) else ""
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith(_TRACKING_PREFIXES)
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, host + port, path, query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()[:32]


def hostname(url: str) -> str | None:
    h = urlsplit(url).hostname
    return h[4:] if h and h.startswith("www.") else h


class BlockedURL(ValueError):
    pass


def check_public_http_url(url: str) -> None:
    """Refuse non-http(s) schemes and hosts resolving to private / loopback / link-local addresses
    (fetch is driven by untrusted search results; don't let a page aim it at internal services)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedURL(f"unsupported URL: {url[:100]}")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise BlockedURL(f"cannot resolve {parts.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise BlockedURL(f"{parts.hostname} resolves to a non-public address")
