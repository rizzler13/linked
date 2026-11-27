"""
Analysis pipeline orchestrator.

Steps:
  1. Fetch website (homepage + extra pages if thin)
  2. Extract structured + unstructured content
  3. Build LLM prompt block
  4. LLM Call A - brand, services, SEO, audience (grounded on page content)
  5. LLM Call B - competitors, search queries (uses Call A's brand profile)
  6. Competitor domain verification (parallel)
  7. Business rule validation
  8. Build final response

run_analysis_stream() is the primary function - an async generator that yields
progress events and the final result. run_analysis() wraps it for callers that
just want the finished response.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import AsyncGenerator

from app.schemas import (
    AnalysisResult,
    BrandInfo,
    CallAOutput,
    CallBOutput,
    CompetitorAnalysis,
    LinkAnalysisResponse,
    Meta,
    TokenUsage,
)
from app.services.competitor_verifier import verify_competitor_domains
from app.services.content_cleaner import assess_content_quality, build_llm_prompt_block
from app.services.content_extractor import extract_page, merge_extractions
from app.services.llm_service import LLMService, LLMUnavailableError, build_brand_profile
from app.services.website_fetcher import FetchError, fetch_site

logger = logging.getLogger(__name__)


async def run_analysis_stream(url: str) -> AsyncGenerator[dict, None]:
    """
    Async generator that runs the full analysis pipeline and yields events:

      {"type": "status",  "data": {"message": str}}
        - progress updates throughout the pipeline

      {"type": "partial", "data": {"brand": {...}, "analysis": {...}}}
        - Call A results (brand, services, SEO, audience) - emitted as soon as
          available so clients can start rendering while Call B runs

      {"type": "result",  "data": LinkAnalysisResponse}
        - final complete response (Pydantic model, convert with model_dump() for JSON)

    Exceptions propagate out of the generator normally. Callers should catch
    FetchError, LLMUnavailableError, and Exception.
    """
    start_time = time.monotonic()
    request_id = str(uuid.uuid4())
    warnings: list[str] = []

    yield {"type": "status", "data": {"message": "Fetching website..."}}

    fetch_result = await fetch_site(url)
    pages = fetch_result["pages"]
    pages_fetched = fetch_result["pages_fetched"]

    yield {"type": "status", "data": {"message": "Extracting content..."}}

    extractions = [extract_page(p["url"], p["html"]) for p in pages]
    merged = merge_extractions(extractions)
    merged["_raw_html"] = pages[0]["html"]

    prompt_block, content_quality = build_llm_prompt_block(merged)

    if content_quality == "minimal":
        warnings.append(
            "Site appears to be a JavaScript-rendered SPA. "
            "Only partial content was available for analysis."
        )
    elif content_quality == "partial":
        warnings.append("Limited content extracted. Analysis may be less specific.")

    yield {"type": "status", "data": {"message": "Analyzing content..."}}

    llm = LLMService()
    call_a_result, provider_name, model_name, a_in, a_out = await llm.call_a(prompt_block)

    # Emit Call A results immediately so clients can start rendering
    yield {
        "type": "partial",
        "data": {
            "brand": call_a_result.brand.model_dump(),
            "analysis": {
                "seo_improvements": call_a_result.seo_improvements.model_dump(),
                "top_services": [s.model_dump() for s in call_a_result.services],
                "target_audience": call_a_result.target_audience.model_dump(),
            },
        },
    }

    yield {"type": "status", "data": {"message": "Finding competitors..."}}

    call_b_result: CallBOutput | None = None
    b_in = b_out = 0

    brand_profile = build_brand_profile(call_a_result)
    try:
        call_b_result, b_in, b_out = await llm.call_b(brand_profile)
    except (LLMUnavailableError, Exception) as e:
        logger.warning(f"Call B failed, returning partial results: {e}")
        warnings.append(
            "Competitor and search query analysis unavailable due to a temporary LLM issue."
        )

    competitors = []
    if call_b_result and call_b_result.competitor_analysis.competitors:
        try:
            competitors = await verify_competitor_domains(
                call_b_result.competitor_analysis.competitors
            )
        except Exception as e:
            logger.warning(f"Competitor verification failed: {e}")
            competitors = call_b_result.competitor_analysis.competitors
            warnings.append("Competitor domain verification skipped due to network issue.")

    call_a_result, call_b_result, new_warnings = _apply_business_rules(
        call_a_result, call_b_result, competitors
    )
    warnings.extend(new_warnings)

    latency_ms = int((time.monotonic() - start_time) * 1000)

    if call_b_result:
        competitor_analysis = CompetitorAnalysis(
            brand_overview=call_b_result.competitor_analysis.brand_overview,
            competitors=competitors,
        )
        search_queries = call_b_result.target_search_queries
    else:
        competitor_analysis = CompetitorAnalysis(brand_overview="", competitors=[])
        search_queries = []

    full_response = LinkAnalysisResponse(
        success=True,
        website=url,
        meta=Meta(
            request_id=request_id,
            provider=provider_name,
            model=model_name,
            latency_ms=latency_ms,
            tokens=TokenUsage(
                call_a_in=a_in,
                call_a_out=a_out,
                call_b_in=b_in,
                call_b_out=b_out,
            ),
            content_quality=content_quality,
            pages_fetched=pages_fetched,
            detected_language=merged.get("detected_language"),
            warnings=warnings,
        ),
        brand=call_a_result.brand,
        analysis=AnalysisResult(
            competitor_analysis=competitor_analysis,
            target_search_queries=search_queries,
            seo_improvements=call_a_result.seo_improvements,
            top_services=call_a_result.services,
            target_audience=call_a_result.target_audience,
        ),
    )

    yield {"type": "result", "data": full_response}


async def run_analysis(url: str) -> LinkAnalysisResponse:
    """Non-streaming wrapper. Runs the full pipeline and returns the final response."""
    async for event in run_analysis_stream(url):
        if event["type"] == "result":
            return event["data"]
    raise LLMUnavailableError("Analysis pipeline produced no result")


def _apply_business_rules(
    call_a: CallAOutput,
    call_b: CallBOutput | None,
    competitors: list,
) -> tuple[CallAOutput, CallBOutput | None, list[str]]:
    warnings: list[str] = []

    seo = call_a.seo_improvements
    if len(seo.recommended_title) > 70:
        seo.recommended_title = seo.recommended_title[:67] + "..."
        warnings.append("SEO title was truncated to 70 characters.")

    if len(seo.meta_description) > 165:
        seo.meta_description = seo.meta_description[:162] + "..."
        warnings.append("Meta description was truncated to 165 characters.")

    pk = seo.primary_keyword.lower().strip()
    if pk and pk not in seo.recommended_title.lower():
        warnings.append(
            f"Primary keyword '{seo.primary_keyword}' does not appear in the recommended SEO title."
        )

    seen_services: set[str] = set()
    deduped_services = []
    for svc in call_a.services:
        key = svc.service_name.strip().lower()
        if key not in seen_services:
            seen_services.add(key)
            deduped_services.append(svc)
    call_a.services = deduped_services[:5]

    if call_b:
        brand_name_lower = call_a.brand.name.strip().lower()
        filtered = [c for c in competitors if c.name.strip().lower() != brand_name_lower]
        if len(filtered) < len(competitors):
            warnings.append("Removed competitor entry that matched the brand name.")
        competitors[:] = filtered

        seen_comp: set[str] = set()
        deduped_comp = []
        for c in competitors:
            key = c.name.strip().lower()
            if key not in seen_comp:
                seen_comp.add(key)
                deduped_comp.append(c)
        competitors[:] = deduped_comp

        if len(competitors) < 3:
            warnings.append(
                f"Only {len(competitors)} competitors identified. "
                "Analysis may be less comprehensive."
            )

        seen_platforms: set[str] = set()
        deduped_queries = []
        for q in call_b.target_search_queries:
            if q.platform not in seen_platforms:
                seen_platforms.add(q.platform)
                deduped_queries.append(q)
        call_b.target_search_queries = deduped_queries[:5]

        expected_platforms = {"Google", "Facebook", "Instagram", "LinkedIn", "X"}
        missing = expected_platforms - {q.platform for q in call_b.target_search_queries}
        if missing:
            warnings.append(f"Missing search queries for platforms: {', '.join(missing)}")

    return call_a, call_b, warnings
