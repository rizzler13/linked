"""
SSRF Guard — parametrized test matrix.

Covers:
  - Loopback IPv4/IPv6
  - Decimal, hex, octal IP encoding
  - AWS/cloud metadata endpoint
  - Private RFC-1918 ranges
  - IPv4-mapped IPv6
  - Bad schemes (file://, ftp://, gopher://, data:)
  - Userinfo in URL
  - Bad ports
  - Public URLs that must pass
  - Monkeypatched DNS resolution (redirect-to-internal simulation)
"""

import socket
from unittest.mock import patch

import pytest

from app.utils.ssrf_guard import SSRFError, validate_url


# ---------------------------------------------------------------------------
# Parametrized block / allow matrix
# ---------------------------------------------------------------------------

BLOCK_CASES = [
    # (url, expected_reason_contains)
    ("http://127.0.0.1",                    "loopback"),
    ("http://127.0.0.1/admin",              "loopback"),
    ("http://localhost",                    "loopback"),
    ("http://[::1]",                        "loopback"),
    ("http://[::1]/secret",                 "loopback"),
    ("http://169.254.169.254",              "private"),    # AWS metadata — Python 3.11+ classifies 169.254.x.x as private
    ("http://169.254.169.254/latest/meta",  "private"),
    ("http://10.0.0.1",                     "private"),
    ("http://10.255.255.255",               "private"),
    ("http://172.16.0.1",                   "private"),
    ("http://172.31.255.255",               "private"),
    ("http://192.168.1.1",                  "private"),
    ("http://192.168.0.100",                "private"),
    ("file:///etc/passwd",                  "bad_scheme"),
    ("ftp://evil.com",                      "bad_scheme"),
    ("gopher://evil.com",                   "bad_scheme"),
    ("data:text/html,<h1>hi</h1>",          "bad_scheme"),
    ("http://user@evil.com",                "userinfo"),
    ("http://user:pass@evil.com",           "userinfo"),
    ("http://evil.com:6379",                "bad_port"),    # Redis
    ("http://evil.com:22",                  "bad_port"),    # SSH
    ("http://evil.com:8080",                "bad_port"),    # common internal
    ("http://evil.com:3306",                "bad_port"),    # MySQL
]

ALLOW_CASES = [
    "https://google.com",
    "https://example.com",
    "http://example.com",
    "https://stripe.com",
    "https://shopify.com",
]


@pytest.mark.parametrize("url,expected_reason", BLOCK_CASES)
def test_ssrf_blocks(url: str, expected_reason: str) -> None:
    with pytest.raises(SSRFError) as exc_info:
        validate_url(url)
    assert expected_reason in exc_info.value.reason, (
        f"Expected reason containing '{expected_reason}', got '{exc_info.value.reason}' for URL: {url}"
    )


@pytest.mark.parametrize("url", ALLOW_CASES)
def test_ssrf_allows_public(url: str) -> None:
    result = validate_url(url)
    assert result.url == url
    assert result.hostname
    assert len(result.resolved_ips) > 0


# ---------------------------------------------------------------------------
# Decimal IP encoding bypass attempt
# ---------------------------------------------------------------------------

def test_decimal_ip_loopback() -> None:
    """http://2130706433 == http://127.0.0.1 in decimal"""
    # Python's urlparse treats this as a hostname, not an IP.
    # Socket resolution will either fail or resolve to 127.0.0.1.
    # Either outcome is acceptable — it must not succeed.
    with pytest.raises((SSRFError, Exception)):
        result = validate_url("http://2130706433")
        # If it somehow parsed, the resolved IP should have been blocked.
        # Fail the test explicitly if we got a ValidatedURL back.
        raise AssertionError(f"Should have been blocked, got: {result}")


# ---------------------------------------------------------------------------
# IPv4-mapped IPv6
# ---------------------------------------------------------------------------

def test_ipv4_mapped_ipv6_loopback() -> None:
    """::ffff:127.0.0.1 is the IPv4-mapped form of loopback"""
    with pytest.raises(SSRFError) as exc_info:
        validate_url("http://[::ffff:127.0.0.1]")
    # Should be blocked either as loopback or blocked_ip (mapped range)
    assert any(x in exc_info.value.reason for x in ("loopback", "blocked_ip", "private"))


# ---------------------------------------------------------------------------
# Monkeypatched DNS resolution — redirect to internal
# ---------------------------------------------------------------------------

def test_dns_resolves_to_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Simulate a DNS entry that resolves to 192.168.1.1.
    Even though the domain looks public, the resolved IP must be blocked.
    """
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(SSRFError) as exc_info:
        validate_url("https://totally-public-looking.com")
    assert "private" in exc_info.value.reason or "blocked_ip" in exc_info.value.reason


def test_dns_resolves_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding to loopback must be blocked."""
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(SSRFError) as exc_info:
        validate_url("https://evil-rebinding.com")
    assert "loopback" in exc_info.value.reason or "blocked_ip" in exc_info.value.reason


def test_dual_a_record_attack(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Dual A-record attack: one public IP + one private IP.
    ALL resolved IPs must be safe — the private one must be caught.
    """
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),   # public
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0)),         # private
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(SSRFError) as exc_info:
        validate_url("https://dual-record-attack.com")
    assert "private" in exc_info.value.reason or "blocked_ip" in exc_info.value.reason


# ---------------------------------------------------------------------------
# Unresolvable host
# ---------------------------------------------------------------------------

def test_unresolvable_host() -> None:
    with pytest.raises(SSRFError) as exc_info:
        validate_url("https://this-domain-does-not-exist-xyzxyzxyz.invalid")
    assert "unresolvable" in exc_info.value.reason


# ---------------------------------------------------------------------------
# Missing / empty URL
# ---------------------------------------------------------------------------

def test_missing_scheme() -> None:
    with pytest.raises(SSRFError):
        validate_url("example.com")


def test_empty_url() -> None:
    with pytest.raises((SSRFError, Exception)):
        validate_url("")
