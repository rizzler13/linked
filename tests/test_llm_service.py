"""
LLM service tests — uses fake providers, tests repair flow and fallback.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas import CallAOutput, CallBOutput
from app.services.llm_service import (
    LLMService,
    LLMUnavailableError,
    _repair_call_a,
    _repair_call_b,
    build_brand_profile,
)


# ---------------------------------------------------------------------------
# Fake provider helpers
# ---------------------------------------------------------------------------

def make_fake_provider(response_dict: dict, provider_name="fake", model_name="fake-model"):
    """Create a mock LLM provider that returns a given dict as JSON."""
    provider = MagicMock()
    provider.provider_name = provider_name
    provider.model_name = model_name
    provider.generate = AsyncMock(
        return_value=(json.dumps(response_dict), 100, 200)
    )
    return provider


def make_failing_provider(error=Exception("LLM error")):
    provider = MagicMock()
    provider.provider_name = "failing"
    provider.model_name = "failing-model"
    provider.generate = AsyncMock(side_effect=error)
    return provider


# ---------------------------------------------------------------------------
# Call A parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_a_success(fake_llm_call_a_response):
    provider = make_fake_provider(fake_llm_call_a_response)
    service = LLMService(primary=provider, fallback=None)

    result, prov, model, in_tok, out_tok = await service.call_a("<website_content>test</website_content>")

    assert isinstance(result, CallAOutput)
    assert result.brand.name == "Acme Web Solutions"
    assert len(result.services) == 2
    assert result.services[0].source == "extracted"
    assert prov == "fake"
    assert in_tok == 100
    assert out_tok == 200


@pytest.mark.asyncio
async def test_call_a_fallback_on_primary_failure(fake_llm_call_a_response):
    failing = make_failing_provider()
    fallback = make_fake_provider(fake_llm_call_a_response, provider_name="groq")
    service = LLMService(primary=failing, fallback=fallback)

    result, prov, model, _, _ = await service.call_a("<website_content>test</website_content>")

    assert prov == "groq"
    assert isinstance(result, CallAOutput)


@pytest.mark.asyncio
async def test_call_a_raises_when_all_providers_fail():
    failing = make_failing_provider()
    also_failing = make_failing_provider()
    service = LLMService(primary=failing, fallback=also_failing)

    with pytest.raises(LLMUnavailableError):
        await service.call_a("<website_content>test</website_content>")


# ---------------------------------------------------------------------------
# Call B parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_b_success(fake_llm_call_b_response):
    provider = make_fake_provider(fake_llm_call_b_response)
    service = LLMService(primary=provider, fallback=None)

    result, in_tok, out_tok = await service.call_b("Brand: Acme\nCategory: Agency")

    assert isinstance(result, CallBOutput)
    assert len(result.competitor_analysis.competitors) == 3
    assert len(result.target_search_queries) == 5
    # All queries must have source="ai_suggested"
    for q in result.target_search_queries:
        assert q.source == "ai_suggested"
    # All competitors must have source="ai_suggested"
    for c in result.competitor_analysis.competitors:
        assert c.source == "ai_suggested"


# ---------------------------------------------------------------------------
# Repair logic
# ---------------------------------------------------------------------------

def test_repair_call_a_adds_missing_keys():
    incomplete = {
        "brand": {"name": "Test Co"},
        "services": [],
    }
    repaired = _repair_call_a(incomplete)
    assert "seo_improvements" in repaired
    assert "target_audience" in repaired
    assert repaired["brand"]["category"] == "Unknown"
    assert repaired["brand"]["site_type"] == "other"


def test_repair_call_a_truncates_seo_title():
    data = {
        "brand": {"name": "T", "category": "T", "summary": "T", "site_type": "other",
                  "competitive_advantages": [], "opportunities": []},
        "services": [],
        "seo_improvements": {
            "primary_keyword": "test",
            "secondary_keywords": [],
            "long_tail_keywords": [],
            "recommended_title": "A" * 100,
            "meta_description": "Short desc",
            "supporting_content": [],
        },
        "target_audience": {
            "primary_audience": [], "secondary_audience": [],
            "industries": [], "customer_needs": [], "recommended_segments": [],
        },
    }
    repaired = _repair_call_a(data)
    assert len(repaired["seo_improvements"]["recommended_title"]) <= 70


def test_repair_call_a_truncates_meta_description():
    data = {
        "brand": {"name": "T", "category": "T", "summary": "T", "site_type": "other",
                  "competitive_advantages": [], "opportunities": []},
        "services": [],
        "seo_improvements": {
            "primary_keyword": "test",
            "secondary_keywords": [],
            "long_tail_keywords": [],
            "recommended_title": "Short Title",
            "meta_description": "X" * 200,
            "supporting_content": [],
        },
        "target_audience": {
            "primary_audience": [], "secondary_audience": [],
            "industries": [], "customer_needs": [], "recommended_segments": [],
        },
    }
    repaired = _repair_call_a(data)
    assert len(repaired["seo_improvements"]["meta_description"]) <= 165


def test_repair_call_b_enforces_source_labels():
    data = {
        "competitor_analysis": {
            "brand_overview": "Test brand",
            "competitors": [
                {"name": "CompA", "source": "extracted", "main_service": "X",
                 "target_market": "Y", "strengths": [], "weaknesses_or_gaps": []},
            ],
        },
        "target_search_queries": [
            {"query": "test", "platform": "Google", "search_intent": "X",
             "business_relevance": "Y", "target_audience": "Z", "source": "extracted"},
        ],
    }
    repaired = _repair_call_b(data)
    assert repaired["competitor_analysis"]["competitors"][0]["source"] == "ai_suggested"
    assert repaired["target_search_queries"][0]["source"] == "ai_suggested"


# ---------------------------------------------------------------------------
# Brand profile builder
# ---------------------------------------------------------------------------

def test_build_brand_profile_contains_key_info(fake_llm_call_a_response):
    call_a = CallAOutput.model_validate(fake_llm_call_a_response)
    profile = build_brand_profile(call_a)
    assert "Acme Web Solutions" in profile
    assert "agency" in profile
    assert "Web Development" in profile


# ---------------------------------------------------------------------------
# JSON repair: markdown fence stripping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_strips_markdown_fences(fake_llm_call_a_response):
    wrapped = f"```json\n{json.dumps(fake_llm_call_a_response)}\n```"
    provider = MagicMock()
    provider.provider_name = "fake"
    provider.model_name = "fake"
    provider.generate = AsyncMock(return_value=(wrapped, 10, 20))

    service = LLMService(primary=provider, fallback=None)
    result, _, _, _, _ = await service.call_a("test content")
    assert isinstance(result, CallAOutput)
