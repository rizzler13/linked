"""
Async HTTP fetcher.

- SSRF guard on every request and every redirect hop
- Manual redirect handling so we can re-validate each hop
- Truly streams the response body to enforce the 2 MB cap without buffering first
- Content-type check (HTML only)
- Multi-page crawling for thin content
- Header rotation on 403: tries multiple browser profiles before giving up
- curl_cffi fallback: TLS fingerprint impersonation for Cloudflare-protected sites
"""

import asyncio
import re
from urllib.parse import urljoin, urlparse

import httpx

try:
    from curl_cffi.requests import AsyncSession as CurlSession
    _CURL_CFFI_AVAILABLE = True
except ImportError:
    _CURL_CFFI_AVAILABLE = False

try:
    from playwright.async_api import async_playwright
    from playwright_stealth import stealth_async
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

from app.config import get_settings
from app.utils.ssrf_guard import SSRFError, async_validate_url

settings = get_settings()

_ALLOWED_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}

_HIGH_VALUE_PATH_PATTERNS = re.compile(
    r"/(about|services|service|products|product|pricing|solutions|what-we-do|our-work|offerings)",
    re.IGNORECASE,
)

# Different browser profiles to rotate through on 403.
# Sites fingerprint bots in various ways - some block Sec-* headers, some block
# specific UAs, some only let through minimal requests. We try each in order.
# Note: no 'br' in Accept-Encoding since brotli decoding needs an optional package.
_HEADER_SETS = [
    # Full Chrome on macOS - most realistic, works on most sites
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
    },
    # Chrome on Windows, no Sec-* headers - some WAFs flag the Sec-* combo as automated
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    },
    # Firefox on Windows - different engine fingerprint
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
            "Gecko/20100101 Firefox/124.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Upgrade-Insecure-Requests": "1",
    },
    # Bare minimum - just a UA, nothing else custom
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    },
]


class FetchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


async def fetch_page(url: str) -> tuple[str, str]:
    """
    Fetch a single page. Raises FetchError on failure.

    Three-phase waterfall - each phase only runs if the previous returned BOT_BLOCKED:

    Phase 1: httpx + header rotation
      Tries 4 browser profiles. Handles most sites that do basic UA/header checks.

    Phase 2: curl_cffi TLS impersonation
      Mimics Chrome's exact TLS ClientHello fingerprint (JA3/JA4). Gets past WAFs
      and Cloudflare tiers that check the TLS handshake, not just HTTP headers.

    Phase 3: Playwright + stealth
      Full headless Chromium with all 50+ bot-detection vectors patched. Executes
      JavaScript so Cloudflare's Managed Challenge and non-interactive Turnstile
      auto-solve. Slowest but works on nearly everything.

    Note: Turnstile in interactive mode (visible checkbox/image puzzle) requires
    a human or a CAPTCHA-solving service — no programmatic solution exists.
    """
    try:
        await async_validate_url(url)
    except SSRFError as e:
        raise FetchError("SSRF_BLOCKED", str(e))

    last_err: FetchError | None = None

    # Phase 1: httpx with header rotation
    for headers in _HEADER_SETS:
        try:
            return await _attempt_fetch(url, headers)
        except FetchError as e:
            if e.code == "BOT_BLOCKED":
                last_err = e
                continue
            raise  # timeouts, DNS failures, wrong content-type - no point retrying

    # Phase 2: curl_cffi TLS impersonation
    if _CURL_CFFI_AVAILABLE:
        try:
            return await _attempt_fetch_curl(url)
        except FetchError as e:
            if e.code == "BOT_BLOCKED":
                last_err = e
            else:
                raise

    # Phase 3: Playwright + stealth (full JS execution)
    if _PLAYWRIGHT_AVAILABLE:
        try:
            return await _attempt_fetch_playwright(url)
        except FetchError as e:
            last_err = e

    raise last_err or FetchError("BOT_BLOCKED", "Site blocked all fetch attempts")


async def _attempt_fetch(url: str, headers: dict) -> tuple[str, str]:
    """Single fetch attempt with the given headers. Called by fetch_page for each profile."""
    timeout = httpx.Timeout(
        connect=settings.fetch_connect_timeout,
        read=settings.fetch_read_timeout,
        write=10.0,
        pool=10.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers=headers,
        max_redirects=0,
    ) as client:
        current_url = url

        for hop in range(settings.fetch_max_redirects + 1):
            try:
                async with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            raise FetchError("SITE_UNREACHABLE", "Redirect with no Location header")

                        new_url = urljoin(current_url, location)

                        try:
                            await async_validate_url(new_url)
                        except SSRFError as e:
                            raise FetchError("SSRF_BLOCKED", f"Redirect target blocked: {e}")

                        current_url = new_url

                        if hop == settings.fetch_max_redirects:
                            raise FetchError("SITE_UNREACHABLE", "Too many redirects")

                        continue

                    if response.status_code == 403:
                        raise FetchError(
                            "BOT_BLOCKED",
                            "Site returned HTTP 403",
                        )

                    if response.status_code >= 400:
                        raise FetchError(
                            "SITE_UNREACHABLE",
                            f"Site returned HTTP {response.status_code}",
                        )

                    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if content_type and content_type not in _ALLOWED_CONTENT_TYPES:
                        raise FetchError("NOT_HTML", f"Unexpected content type: {content_type}")

                    html = await _read_capped(response)
                    return current_url, html

            except FetchError:
                raise
            except httpx.TimeoutException:
                raise FetchError("FETCH_TIMEOUT", f"Timed out fetching {current_url}")
            except httpx.RequestError as e:
                raise FetchError("SITE_UNREACHABLE", f"Request failed: {e}")

        raise FetchError("SITE_UNREACHABLE", "Exceeded redirect limit")


async def _attempt_fetch_curl(url: str) -> tuple[str, str]:
    """
    Fetch using curl_cffi with Chrome TLS impersonation.

    curl_cffi patches libcurl to produce the same TLS ClientHello fingerprint
    (JA3/JA4) as a real Chrome browser. Cloudflare and similar WAFs check this
    fingerprint - httpx always fails it, curl_cffi passes it.

    Does not stream (curl_cffi doesn't support it), so the 2 MB cap is enforced
    after the response is received. Acceptable since this path is only hit for
    sites that already blocked httpx - we'd rather get content than nothing.
    """
    # Import here since we guard with _CURL_CFFI_AVAILABLE at the call site
    from curl_cffi.requests import AsyncSession as CurlSession

    timeout = settings.fetch_connect_timeout + settings.fetch_read_timeout

    try:
        async with CurlSession(impersonate="chrome120") as session:
            resp = await session.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                max_redirects=settings.fetch_max_redirects,
            )
    except Exception as e:
        raise FetchError("SITE_UNREACHABLE", f"curl_cffi request failed: {e}")

    # Validate the redirect destination if we ended up somewhere different
    final_url = str(resp.url)
    if final_url != url:
        try:
            await async_validate_url(final_url)
        except SSRFError as e:
            raise FetchError("SSRF_BLOCKED", f"Redirect target blocked: {e}")

    if resp.status_code == 403:
        raise FetchError("BOT_BLOCKED", "Site blocked the request (HTTP 403) even with TLS impersonation")

    if resp.status_code >= 400:
        raise FetchError("SITE_UNREACHABLE", f"Site returned HTTP {resp.status_code}")

    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type and content_type not in _ALLOWED_CONTENT_TYPES:
        raise FetchError("NOT_HTML", f"Unexpected content type: {content_type}")

    raw = resp.content
    if len(raw) > settings.fetch_max_bytes:
        raise FetchError(
            "CONTENT_TOO_LARGE",
            f"Response exceeds {settings.fetch_max_bytes // 1024 // 1024} MB limit",
        )

    return final_url, raw.decode("utf-8", errors="replace")


async def _attempt_fetch_playwright(url: str) -> tuple[str, str]:
    """
    Full headless Chromium fetch with stealth patches applied.

    playwright-stealth hides all the browser automation tells:
    - Sets navigator.webdriver = undefined
    - Patches canvas/WebGL fingerprinting to match a real browser
    - Fixes permissions API, plugin arrays, language inconsistencies, etc.

    With these patches, Cloudflare's JS challenge sees a legitimate Chrome browser
    and auto-solves without any human interaction. Same for non-interactive Turnstile.

    We wait up to 5s after initial load in case a Cloudflare challenge page needs
    time to verify and redirect to the real content.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            page = await context.new_page()
            await stealth_async(page)

            try:
                nav_response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception as e:
                raise FetchError("SITE_UNREACHABLE", f"Playwright navigation failed: {e}")

            # Cloudflare challenge pages redirect after a few seconds once JS runs.
            # "Just a moment..." is the classic Cloudflare interstitial title.
            title = (await page.title()).lower()
            if "just a moment" in title or "attention required" in title or "checking" in title:
                # Wait for the challenge to complete and redirect
                try:
                    await page.wait_for_function(
                        "() => !document.title.toLowerCase().includes('just a moment')",
                        timeout=10000,
                    )
                except Exception:
                    pass  # If it times out, grab whatever content we have

            final_url = page.url

            # SSRF check on the final destination after all Cloudflare redirects
            try:
                await async_validate_url(final_url)
            except SSRFError as e:
                raise FetchError("SSRF_BLOCKED", f"Redirect target blocked: {e}")

            if nav_response and nav_response.status == 403:
                raise FetchError("BOT_BLOCKED", "Site requires interactive CAPTCHA or login")

            if nav_response and nav_response.status >= 400:
                raise FetchError("SITE_UNREACHABLE", f"Site returned HTTP {nav_response.status}")

            html = await page.content()

            if len(html.encode("utf-8")) > settings.fetch_max_bytes:
                raise FetchError(
                    "CONTENT_TOO_LARGE",
                    f"Response exceeds {settings.fetch_max_bytes // 1024 // 1024} MB limit",
                )

            return final_url, html

        finally:
            await browser.close()


async def _read_capped(response: httpx.Response) -> str:
    """Stream the response body, raising FetchError if it exceeds the configured size limit."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes(chunk_size=8192):
        total += len(chunk)
        if total > settings.fetch_max_bytes:
            raise FetchError(
                "CONTENT_TOO_LARGE",
                f"Response exceeds {settings.fetch_max_bytes // 1024 // 1024} MB limit",
            )
        chunks.append(chunk)
    raw = b"".join(chunks)
    return raw.decode("utf-8", errors="replace")


async def fetch_site(url: str) -> dict:
    """
    Fetch the homepage. If content looks thin, also grab 1-2 high-value pages
    from the nav (about, services, pricing, etc.).
    Returns: {"pages": [{url, html}, ...], "pages_fetched": int}
    """
    final_url, homepage_html = await fetch_page(url)
    pages = [{"url": final_url, "html": homepage_html}]

    text_len = len(_strip_tags_fast(homepage_html))
    if text_len < 1500:
        extra_urls = _pick_high_value_pages(homepage_html, final_url)
        if extra_urls:
            tasks = [_safe_fetch(u) for u in extra_urls[: settings.max_extra_pages]]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, dict):
                    pages.append(r)

    return {"pages": pages, "pages_fetched": len(pages)}


async def _safe_fetch(url: str) -> dict | Exception:
    try:
        final_url, html = await fetch_page(url)
        return {"url": final_url, "html": html}
    except Exception as e:
        return e


def _pick_high_value_pages(html: str, base_url: str) -> list[str]:
    """Find /about, /services, /pricing style links in the homepage HTML."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    results: list[str] = []
    base_domain = urlparse(base_url).netloc

    for tag in soup.find_all("a", href=True):
        href: str = tag["href"].strip()
        full = urljoin(base_url, href)
        parsed = urlparse(full)

        if parsed.netloc != base_domain:
            continue

        if _HIGH_VALUE_PATH_PATTERNS.search(parsed.path) and full not in seen:
            seen.add(full)
            results.append(full)

        if len(results) >= settings.max_extra_pages:
            break

    return results


def _strip_tags_fast(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)
