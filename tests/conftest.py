"""
Shared test fixtures.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Clear slowapi in-memory storage before each test to avoid 429s."""
    from app.utils.rate_limiter import limiter
    storage = getattr(limiter, "_storage", None)
    if storage is not None and hasattr(storage, "reset"):
        storage.reset()
    yield
    if storage is not None and hasattr(storage, "reset"):
        storage.reset()

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def rich_jsonld_html() -> str:
    return load_fixture("rich_jsonld.html")


@pytest.fixture
def og_only_html() -> str:
    return load_fixture("og_only.html")


@pytest.fixture
def spa_shell_html() -> str:
    return load_fixture("spa_shell.html")


@pytest.fixture
def malformed_html() -> str:
    return load_fixture("malformed.html")


@pytest.fixture
def fake_llm_call_a_response() -> dict:
    """A valid Call A response for testing without hitting the real LLM."""
    return {
        "brand": {
            "name": "Acme Web Solutions",
            "category": "Web Development Agency",
            "summary": "Professional web development and digital services company based in San Francisco.",
            "site_type": "agency",
            "competitive_advantages": ["10+ years experience", "500+ clients"],
            "opportunities": ["Expand to enterprise market", "Add SaaS offering"],
        },
        "services": [
            {
                "service_name": "Web Development",
                "description": "Custom websites built with React, Next.js, Node.js.",
                "target_industry": "Technology",
                "target_customer": "Small to medium-sized businesses",
                "main_benefit": "Fast, responsive, SEO-optimized websites",
                "source": "extracted",
                "evidence": "Listed under Services as Web Development",
            },
            {
                "service_name": "Mobile App Development",
                "description": "Native and cross-platform iOS and Android apps.",
                "target_industry": "Technology",
                "target_customer": "Businesses needing mobile presence",
                "main_benefit": "Performant apps using Flutter and React Native",
                "source": "extracted",
                "evidence": "Mobile Apps navigation link and dedicated section",
            },
        ],
        "seo_improvements": {
            "primary_keyword": "web development agency",
            "secondary_keywords": ["mobile app development", "digital marketing"],
            "long_tail_keywords": ["web development agency for small businesses"],
            "recommended_title": "Acme Web Solutions | Web Development Agency",
            "meta_description": "Professional web development, mobile apps & digital marketing for growing businesses. 10+ years, 500+ clients.",
            "supporting_content": [
                {"type": "faq", "title": "Web Development FAQ", "rationale": "Addresses common questions from prospects"},
            ],
        },
        "target_audience": {
            "primary_audience": [
                {
                    "segment_name": "Small Business Owners",
                    "business_type": "SMB",
                    "requirement": "Professional website",
                    "needs_and_challenges": ["Limited budget", "No in-house tech team"],
                    "interests": ["Business growth", "Online presence"],
                }
            ],
            "secondary_audience": [],
            "industries": ["Retail", "Healthcare", "Finance"],
            "geographical_focus": "San Francisco, CA",
            "customer_needs": ["Affordable solutions", "Reliable support"],
            "recommended_segments": ["Small businesses needing digital presence"],
        },
    }


@pytest.fixture
def fake_llm_call_b_response() -> dict:
    """A valid Call B response for testing without hitting the real LLM."""
    return {
        "competitor_analysis": {
            "brand_overview": "Acme Web Solutions is a web development agency targeting SMBs.",
            "competitors": [
                {
                    "name": "Toptal",
                    "domain": "toptal.com",
                    "source": "ai_suggested",
                    "main_service": "Freelance web development marketplace",
                    "target_market": "Businesses needing vetted developers",
                    "strengths": ["Large talent pool", "Rigorous vetting"],
                    "weaknesses_or_gaps": ["Higher cost", "Less personal service"],
                },
                {
                    "name": "Upwork",
                    "domain": "upwork.com",
                    "source": "ai_suggested",
                    "main_service": "Freelance marketplace",
                    "target_market": "All business sizes",
                    "strengths": ["Wide range of talent", "Flexible pricing"],
                    "weaknesses_or_gaps": ["Quality varies", "Less accountability"],
                },
                {
                    "name": "Clutch",
                    "domain": "clutch.co",
                    "source": "ai_suggested",
                    "main_service": "Agency discovery platform",
                    "target_market": "Businesses evaluating agencies",
                    "strengths": ["Verified reviews", "Wide agency listings"],
                    "weaknesses_or_gaps": ["Not a direct service provider"],
                },
            ],
        },
        "target_search_queries": [
            {
                "query": "web development agency for small business",
                "platform": "Google",
                "search_intent": "transactional",
                "business_relevance": "Direct service match",
                "target_audience": "Small business owners",
                "source": "ai_suggested",
            },
            {
                "query": "Looking for a web development company for my startup",
                "platform": "Facebook",
                "search_intent": "commercial",
                "business_relevance": "Community referral search",
                "target_audience": "Startup founders",
                "source": "ai_suggested",
            },
            {
                "query": "Web design inspiration for small business",
                "platform": "Instagram",
                "search_intent": "informational",
                "business_relevance": "Design discovery",
                "target_audience": "Business owners seeking design ideas",
                "source": "ai_suggested",
            },
            {
                "query": "web development agency for B2B companies",
                "platform": "LinkedIn",
                "search_intent": "commercial",
                "business_relevance": "Professional service discovery",
                "target_audience": "B2B company decision makers",
                "source": "ai_suggested",
            },
            {
                "query": "Affordable web development #SmallBusiness #WebDev",
                "platform": "X",
                "search_intent": "transactional",
                "business_relevance": "Social service search",
                "target_audience": "Budget-conscious entrepreneurs",
                "source": "ai_suggested",
            },
        ],
    }
