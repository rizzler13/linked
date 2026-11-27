"""
Checks whether AI-suggested competitor domains actually exist.
Runs HEAD requests in parallel. Every domain goes through the SSRF guard first.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.schemas import Competitor
from app.utils.ssrf_guard import SSRFError, async_validate_url

logger = logging.getLogger(__name__)

_VERIFY_TIMEOUT = 2.5


async def verify_competitor_domains(competitors: list[Competitor]) -> list[Competitor]:
    tasks = [_check_one(comp) for comp in competitors]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    verified: list[Competitor] = []
    for i, result in enumerate(results):
        if isinstance(result, Competitor):
            verified.append(result)
        else:
            comp = competitors[i].model_copy()
            comp.domain_verified = False
            verified.append(comp)

    return verified


async def _check_one(comp: Competitor) -> Competitor:
    updated = comp.model_copy()

    if not comp.domain:
        updated.domain_verified = None
        return updated

    # Strip any protocol prefix the LLM might have added.
    # removeprefix avoids the str.lstrip() pitfall where lstrip("https://")
    # strips individual characters, not the prefix as a whole.
    domain = comp.domain.strip().removeprefix("https://").removeprefix("http://").split("/")[0]
    url = f"https://{domain}"

    try:
        await async_validate_url(url)
    except SSRFError as e:
        logger.warning(f"Competitor domain blocked: {domain} - {e}")
        updated.domain_verified = False
        return updated

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_VERIFY_TIMEOUT),
            follow_redirects=False,  # 3xx still means the domain exists; no need to follow
        ) as client:
            resp = await client.head(url)
            updated.domain_verified = resp.status_code < 500
    except (httpx.RequestError, httpx.TimeoutException) as e:
        logger.debug(f"Competitor domain unreachable: {domain} - {e}")
        updated.domain_verified = False
    except Exception as e:
        logger.warning(f"Unexpected error verifying {domain}: {e}")
        updated.domain_verified = False

    return updated
