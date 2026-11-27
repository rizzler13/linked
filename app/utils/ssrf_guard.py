"""
SSRF guard - validates URLs before any network request.

Blocks: private/loopback IPs, cloud metadata endpoints (169.254.169.254),
non-HTTP schemes, credentials in URLs, non-standard ports, and all resolved
IPs for a hostname (catches dual A-record attacks).

Known gap: there's a small TOCTOU window between DNS resolution and TCP
connection. Full mitigation would require connecting to the pinned IP with
a custom SNI header, which breaks TLS on a lot of real sites. We re-validate
on every redirect hop as a reasonable tradeoff.
"""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


_BLOCKED_NETWORKS = [
    # IPv4
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),    # link-local / AWS metadata
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),     # shared address space RFC 6598
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),     # IPv4-mapped
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("100::/64"),
]

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PORTS = {80, 443, None}  # None = port absent (uses default)


class SSRFError(ValueError):
    def __init__(self, reason: str, url: str = ""):
        self.reason = reason
        self.url = url
        super().__init__(f"SSRF blocked [{reason}]: {url}")


@dataclass
class ValidatedURL:
    url: str
    hostname: str
    resolved_ips: list[str]


def validate_url(raw_url: str) -> ValidatedURL:
    """
    Validate a URL for SSRF safety. Returns ValidatedURL on success, raises SSRFError on failure.
    Synchronous - safe to call from tests or non-async contexts.
    For async callers, use async_validate_url to avoid blocking the event loop.
    """
    raw_url = raw_url.strip()

    try:
        parsed = urlparse(raw_url)
    except Exception:
        raise SSRFError("parse_failed", raw_url)

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise SSRFError("bad_scheme", raw_url)

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("no_hostname", raw_url)

    if parsed.username or parsed.password:
        raise SSRFError("userinfo", raw_url)

    port = parsed.port
    if port not in _ALLOWED_PORTS:
        raise SSRFError("bad_port", raw_url)

    # IDN-encode to block Unicode homograph attacks
    try:
        hostname_encoded = hostname.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        raise SSRFError("bad_hostname", raw_url)

    try:
        addr_infos = socket.getaddrinfo(hostname_encoded, None)
    except socket.gaierror:
        raise SSRFError("unresolvable", raw_url)

    if not addr_infos:
        raise SSRFError("unresolvable", raw_url)

    resolved_ips: list[str] = []
    for info in addr_infos:
        ip_str = info[4][0]
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SSRFError("bad_ip", raw_url)
        _check_ip_blocked(ip_obj, raw_url)
        resolved_ips.append(ip_str)

    return ValidatedURL(
        url=raw_url,
        hostname=hostname_encoded,
        resolved_ips=list(set(resolved_ips)),
    )


async def async_validate_url(raw_url: str) -> ValidatedURL:
    """Async wrapper - runs validate_url in a thread pool so DNS doesn't block the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, validate_url, raw_url)


def _check_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, url: str) -> None:
    """Raise SSRFError if the IP falls in any blocked range."""
    # Stdlib checks first - they produce cleaner reason codes.
    if ip.is_loopback:
        raise SSRFError("loopback", url)
    if ip.is_private:
        raise SSRFError("private", url)
    if ip.is_link_local:
        raise SSRFError("link_local", url)
    if ip.is_multicast:
        raise SSRFError("multicast", url)
    if ip.is_unspecified:
        raise SSRFError("unspecified", url)

    # ip.is_reserved intentionally skipped: Python flags NAT64 prefix 64:ff9b::/96
    # as reserved, which causes false positives on sites like stripe.com and shopify.com
    # when their DNS goes through a NAT64 gateway. The explicit list below covers all
    # the dangerous ranges without that problem.
    for network in _BLOCKED_NETWORKS:
        if ip.version == network.version and ip in network:
            raise SSRFError(f"blocked_ip:{network}", url)
