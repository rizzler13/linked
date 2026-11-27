"""
Per-IP rate limiting, URL-keyed result cache, and LLM concurrency semaphore.
"""

import asyncio
from urllib.parse import urlparse, urlunparse

from cachetools import TTLCache
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings

settings = get_settings()

limiter = Limiter(key_func=get_remote_address)

_analysis_cache: TTLCache = TTLCache(
    maxsize=settings.cache_max_size,
    ttl=settings.cache_ttl_seconds,
)

# Caps concurrent LLM calls to stay within free-tier rate limits
llm_semaphore = asyncio.Semaphore(settings.llm_semaphore_slots)


def normalize_url(url: str) -> str:
    """
    Normalize a URL for cache key comparison.
    https://Example.COM/ and https://example.com both map to the same key.
    """
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, p.params, p.query, ""))


def get_cached_result(url: str):
    return _analysis_cache.get(normalize_url(url))


def set_cached_result(url: str, result) -> None:
    _analysis_cache[normalize_url(url)] = result
