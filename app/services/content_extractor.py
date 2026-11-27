"""
Content extractor — layered extraction from raw HTML.

Priority order:
  1. JSON-LD (Schema.org)
  2. Open Graph / Twitter Card
  3. <title> + <meta> tags
  4. Navigation link text
  5. Heading hierarchy (h1-h3)
  6. Main body content (readability-style)
  7. Footer text
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import extruct
from bs4 import BeautifulSoup, Tag


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_page(url: str, html: str) -> dict:
    """
    Extract all structured and unstructured content from a page.
    Returns a normalized dict.
    """
    soup = BeautifulSoup(html, "lxml")
    _remove_noise(soup)

    json_ld = _extract_json_ld(html, url)
    open_graph = _extract_open_graph(html, url)
    meta_tags = _extract_meta_tags(soup)
    nav_links = _extract_nav_links(soup)
    headings = _extract_headings(soup)
    main_text = _extract_main_text(soup)
    footer_text = _extract_footer(soup)

    return {
        "url": url,
        "json_ld": json_ld,
        "open_graph": open_graph,
        "meta_tags": meta_tags,
        "nav_links": nav_links,
        "headings": headings,
        "main_text": main_text,
        "footer_text": footer_text,
    }


def merge_extractions(pages: list[dict]) -> dict:
    """
    Merge multiple page extractions.
    First page (homepage) takes priority for structured data.
    Subsequent pages contribute additional nav/heading/body content.
    """
    if not pages:
        return {}

    base = pages[0].copy()

    for extra in pages[1:]:
        # Extend nav and headings, deduplicate
        existing_nav = set(base.get("nav_links", []))
        for link in extra.get("nav_links", []):
            if link not in existing_nav:
                base["nav_links"].append(link)
                existing_nav.add(link)

        # Append extra body text with separator
        if extra.get("main_text"):
            base["main_text"] = (base.get("main_text", "") + "\n\n" + extra["main_text"]).strip()

        # Footer from first available page
        if not base.get("footer_text") and extra.get("footer_text"):
            base["footer_text"] = extra["footer_text"]

    return base


# ---------------------------------------------------------------------------
# Layer 1: JSON-LD
# ---------------------------------------------------------------------------

def _extract_json_ld(html: str, base_url: str) -> list[dict]:
    try:
        data = extruct.extract(
            html,
            base_url=base_url,
            syntaxes=["json-ld"],
            uniform=True,
        )
        return data.get("json-ld", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Layer 2: Open Graph / Twitter
# ---------------------------------------------------------------------------

def _extract_open_graph(html: str, base_url: str) -> dict:
    try:
        data = extruct.extract(
            html,
            base_url=base_url,
            syntaxes=["opengraph"],
            uniform=True,
        )
        og_list = data.get("opengraph", [])
        if og_list:
            # uniform=True returns data directly as dict keys (no "properties" wrapper)
            _skip = {"@type", "@context"}
            return {k: v for k, v in og_list[0].items() if k not in _skip}
        return {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Layer 3: Meta tags
# ---------------------------------------------------------------------------

def _extract_meta_tags(soup: BeautifulSoup) -> dict:
    result: dict[str, str] = {}

    title_tag = soup.find("title")
    if title_tag:
        result["title"] = title_tag.get_text(strip=True)

    for meta in soup.find_all("meta"):
        name = meta.get("name", meta.get("property", "")).lower()
        content = meta.get("content", "")
        if not name or not content:
            continue
        if name in ("description", "keywords", "robots", "author",
                    "og:title", "og:description", "og:type",
                    "twitter:title", "twitter:description"):
            result[name] = content

    canonical = soup.find("link", rel="canonical")
    if canonical and canonical.get("href"):
        result["canonical"] = canonical["href"]

    return result


# ---------------------------------------------------------------------------
# Layer 4: Navigation links
# ---------------------------------------------------------------------------

def _extract_nav_links(soup: BeautifulSoup) -> list[str]:
    nav_texts: list[str] = []
    seen: set[str] = set()

    # Look in <nav>, header, and elements with nav-like class/id
    nav_containers = soup.find_all(["nav", "header"]) or []
    nav_containers += soup.find_all(attrs={"id": re.compile(r"nav|menu|header", re.I)})
    nav_containers += soup.find_all(attrs={"class": re.compile(r"nav|menu|header", re.I)})

    for container in nav_containers:
        for a in container.find_all("a", href=True):
            text = a.get_text(strip=True)
            if text and text not in seen and len(text) < 60:
                nav_texts.append(text)
                seen.add(text)

    return nav_texts[:30]  # cap at 30 items


# ---------------------------------------------------------------------------
# Layer 5: Headings
# ---------------------------------------------------------------------------

def _extract_headings(soup: BeautifulSoup) -> list[str]:
    headings: list[str] = []
    for tag_name in ["h1", "h2", "h3"]:
        for tag in soup.find_all(tag_name):
            text = tag.get_text(separator=" ", strip=True)
            if text and len(text) > 2:
                headings.append(f"{tag_name.upper()}: {text}")
    return headings[:40]


# ---------------------------------------------------------------------------
# Layer 6: Main body content
# ---------------------------------------------------------------------------

def _extract_main_text(soup: BeautifulSoup) -> str:
    # Try semantic containers first
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"id": re.compile(r"main|content|body", re.I)})
        or soup.find(attrs={"class": re.compile(r"main|content|body", re.I)})
    )

    target = main if main else soup.find("body")
    if not target:
        return ""

    # Remove nav, header, footer, scripts, styles from the copy
    for tag in target.find_all(["nav", "header", "footer", "script", "style",
                                  "noscript", "aside", "form", "iframe"]):
        tag.decompose()

    text = target.get_text(separator="\n", strip=True)
    return _clean_text(text)


# ---------------------------------------------------------------------------
# Layer 7: Footer
# ---------------------------------------------------------------------------

def _extract_footer(soup: BeautifulSoup) -> str:
    footer = (
        soup.find("footer")
        or soup.find(attrs={"id": re.compile(r"footer", re.I)})
        or soup.find(attrs={"class": re.compile(r"footer", re.I)})
    )
    if not footer:
        return ""

    text = footer.get_text(separator=" ", strip=True)
    return _clean_text(text)[:500]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _remove_noise(soup: BeautifulSoup) -> None:
    """Remove script, style, hidden elements in-place."""
    for tag in soup.find_all(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    # Hidden elements
    for tag in soup.find_all(style=re.compile(r"display:\s*none", re.I)):
        tag.decompose()


def _clean_text(text: str) -> str:
    """Normalize whitespace and remove repeated blank lines."""
    lines = [line.strip() for line in text.splitlines()]
    # Remove empty lines that repeat more than once consecutively
    cleaned: list[str] = []
    blank_count = 0
    for line in lines:
        if not line:
            blank_count += 1
            if blank_count <= 1:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)
    return "\n".join(cleaned).strip()
