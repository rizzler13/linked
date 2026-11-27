from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LinkAnalysisRequest(BaseModel):
    url: str = Field(..., max_length=2048, description="Website URL to analyze")

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class ErrorCode(str, Enum):
    INVALID_URL = "INVALID_URL"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    SITE_UNREACHABLE = "SITE_UNREACHABLE"
    BOT_BLOCKED = "BOT_BLOCKED"
    FETCH_TIMEOUT = "FETCH_TIMEOUT"
    NOT_HTML = "NOT_HTML"
    CONTENT_TOO_LARGE = "CONTENT_TOO_LARGE"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail


class TokenUsage(BaseModel):
    call_a_in: int = 0
    call_a_out: int = 0
    call_b_in: int = 0
    call_b_out: int = 0

    @property
    def total(self) -> int:
        return self.call_a_in + self.call_a_out + self.call_b_in + self.call_b_out


class Meta(BaseModel):
    request_id: str
    provider: str
    model: str
    latency_ms: int
    tokens: TokenUsage
    content_quality: Literal["rich", "partial", "minimal"]
    pages_fetched: int
    detected_language: str | None = None
    warnings: list[str] = Field(default_factory=list)


class BrandInfo(BaseModel):
    name: str
    category: str
    summary: str
    site_type: Literal[
        "saas", "e_commerce", "local_business", "portfolio",
        "agency", "blog", "nonprofit", "corporate", "educational", "other"
    ]
    competitive_advantages: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)


class Competitor(BaseModel):
    name: str
    domain: str | None = None
    domain_verified: bool | None = None
    source: Literal["ai_suggested"] = "ai_suggested"
    main_service: str
    target_market: str
    strengths: list[str]
    weaknesses_or_gaps: list[str]


class CompetitorAnalysis(BaseModel):
    brand_overview: str
    competitors: list[Competitor] = Field(default_factory=list)


class SearchQuery(BaseModel):
    query: str
    platform: Literal["Google", "Facebook", "Instagram", "LinkedIn", "X"]
    search_intent: Literal["transactional", "commercial", "informational", "navigational"]
    business_relevance: str
    target_audience: str
    source: Literal["ai_suggested"] = "ai_suggested"


class SupportingContent(BaseModel):
    type: str
    title: str
    rationale: str
    url: str | None = None


class SEOImprovements(BaseModel):
    primary_keyword: str
    secondary_keywords: list[str]
    long_tail_keywords: list[str]
    recommended_title: str = Field(..., max_length=70)
    meta_description: str = Field(..., max_length=165)
    supporting_content: list[SupportingContent] = Field(default_factory=list)


class Service(BaseModel):
    service_name: str
    description: str
    target_industry: str
    target_customer: str
    main_benefit: str
    source: Literal["extracted", "inferred"]
    evidence: str | None = None
    url: str | None = None


class AudienceSegment(BaseModel):
    segment_name: str
    business_type: str
    requirement: str
    needs_and_challenges: list[str]
    interests: list[str]


class TargetAudience(BaseModel):
    primary_audience: list[AudienceSegment]
    secondary_audience: list[AudienceSegment]
    industries: list[str]
    geographical_focus: str | None = None
    customer_needs: list[str]
    recommended_segments: list[str]


class AnalysisResult(BaseModel):
    competitor_analysis: CompetitorAnalysis
    target_search_queries: list[SearchQuery]
    seo_improvements: SEOImprovements
    top_services: list[Service]
    target_audience: TargetAudience


class LinkAnalysisResponse(BaseModel):
    success: bool = True
    website: str
    meta: Meta
    brand: BrandInfo
    analysis: AnalysisResult


# Internal LLM call outputs

class CallAOutput(BaseModel):
    brand: BrandInfo
    services: list[Service]
    seo_improvements: SEOImprovements
    target_audience: TargetAudience


class CallBOutput(BaseModel):
    competitor_analysis: CompetitorAnalysis
    target_search_queries: list[SearchQuery]
