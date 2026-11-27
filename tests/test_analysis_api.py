"""
Integration tests for POST /link-analysis endpoint.
Uses mocked analysis service to avoid real network/LLM calls.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import (
    AnalysisResult,
    BrandInfo,
    CallAOutput,
    CallBOutput,
    CompetitorAnalysis,
    Competitor,
    LinkAnalysisResponse,
    Meta,
    SearchQuery,
    TokenUsage,
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make_full_response(fake_a: dict, fake_b: dict) -> LinkAnalysisResponse:
    call_a = CallAOutput.model_validate(fake_a)
    call_b = CallBOutput.model_validate(fake_b)
    return LinkAnalysisResponse(
        success=True,
        website="https://acme.example.com",
        meta=Meta(
            request_id="test-uuid",
            provider="gemini",
            model="gemini-2.0-flash",
            latency_ms=1500,
            tokens=TokenUsage(call_a_in=1000, call_a_out=500, call_b_in=200, call_b_out=400),
            content_quality="rich",
            pages_fetched=1,
            warnings=[],
        ),
        brand=call_a.brand,
        analysis=AnalysisResult(
            competitor_analysis=call_b.competitor_analysis,
            target_search_queries=call_b.target_search_queries,
            seo_improvements=call_a.seo_improvements,
            top_services=call_a.services,
            target_audience=call_a.target_audience,
        ),
    )


# ---------------------------------------------------------------------------
# Success case
# ---------------------------------------------------------------------------

def test_link_analysis_success(client, fake_llm_call_a_response, fake_llm_call_b_response):
    expected = make_full_response(fake_llm_call_a_response, fake_llm_call_b_response)

    with patch("app.main.run_analysis", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = expected
        response = client.post(
            "/link-analysis",
            json={"url": "https://acme.example.com"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["website"] == "https://acme.example.com"
    assert "brand" in data
    assert "analysis" in data
    assert data["brand"]["name"] == "Acme Web Solutions"


def test_response_has_all_five_sections(client, fake_llm_call_a_response, fake_llm_call_b_response):
    expected = make_full_response(fake_llm_call_a_response, fake_llm_call_b_response)

    with patch("app.main.run_analysis", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = expected
        response = client.post(
            "/link-analysis",
            json={"url": "https://acme.example.com"},
        )

    analysis = response.json()["analysis"]
    assert "competitor_analysis" in analysis
    assert "target_search_queries" in analysis
    assert "seo_improvements" in analysis
    assert "top_services" in analysis
    assert "target_audience" in analysis


def test_response_has_meta_envelope(client, fake_llm_call_a_response, fake_llm_call_b_response):
    expected = make_full_response(fake_llm_call_a_response, fake_llm_call_b_response)

    with patch("app.main.run_analysis", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = expected
        response = client.post(
            "/link-analysis",
            json={"url": "https://acme.example.com"},
        )

    meta = response.json()["meta"]
    assert "request_id" in meta
    assert "provider" in meta
    assert "latency_ms" in meta
    assert "tokens" in meta
    assert "content_quality" in meta
    assert meta["content_quality"] in ("rich", "partial", "minimal")


def test_source_labels_present(client, fake_llm_call_a_response, fake_llm_call_b_response):
    expected = make_full_response(fake_llm_call_a_response, fake_llm_call_b_response)

    with patch("app.main.run_analysis", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = expected
        response = client.post(
            "/link-analysis",
            json={"url": "https://acme.example.com"},
        )

    data = response.json()
    # Services must have source labels
    for svc in data["analysis"]["top_services"]:
        assert svc["source"] in ("extracted", "inferred")
    # Competitors must be ai_suggested
    for comp in data["analysis"]["competitor_analysis"]["competitors"]:
        assert comp["source"] == "ai_suggested"
    # Search queries must be ai_suggested
    for q in data["analysis"]["target_search_queries"]:
        assert q["source"] == "ai_suggested"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_invalid_url_returns_422(client):
    response = client.post(
        "/link-analysis",
        json={"url": "not-a-url"},
    )
    assert response.status_code == 422


def test_missing_url_returns_422(client):
    response = client.post("/link-analysis", json={})
    assert response.status_code == 422


def test_ssrf_blocked_returns_400(client):
    from app.utils.ssrf_guard import SSRFError

    with patch("app.main.run_analysis", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = SSRFError("loopback", "http://127.0.0.1")
        response = client.post(
            "/link-analysis",
            json={"url": "http://127.0.0.1"},
        )

    assert response.status_code == 400
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "SSRF_BLOCKED"


def test_site_unreachable_returns_502(client):
    from app.services.website_fetcher import FetchError

    with patch("app.main.run_analysis", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = FetchError("SITE_UNREACHABLE", "Connection refused")
        response = client.post(
            "/link-analysis",
            json={"url": "https://definitely-down.example.com"},
        )

    assert response.status_code == 502
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "SITE_UNREACHABLE"


def test_llm_unavailable_returns_503(client):
    from app.services.llm_service import LLMUnavailableError

    with patch("app.main.run_analysis", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = LLMUnavailableError("All providers failed")
        response = client.post(
            "/link-analysis",
            json={"url": "https://example.com"},
        )

    assert response.status_code == 503
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "LLM_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_cache_returns_same_result(client, fake_llm_call_a_response, fake_llm_call_b_response):
    expected = make_full_response(fake_llm_call_a_response, fake_llm_call_b_response)

    with patch("app.main.run_analysis", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = expected

        response1 = client.post("/link-analysis", json={"url": "https://cache-test.example.com"})
        response2 = client.post("/link-analysis", json={"url": "https://cache-test.example.com"})

    # Second call should be served from cache — run_analysis called only once
    assert mock_run.call_count == 1
    assert response1.status_code == 200
    assert response2.status_code == 200


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
