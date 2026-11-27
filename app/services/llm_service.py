"""
LLM service - provider abstraction, two-call split, retry logic, and output repair.

Call A (grounded):   Gemini (primary, with model rotation on 429/503)
                     -> OpenRouter free model (optional fallback)
Call B (ungrounded): Groq (dedicated, requires GROQ_API_KEY)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Protocol, runtime_checkable

import httpx
from groq import AsyncGroq
from pydantic import ValidationError

from app.config import get_settings
from app.schemas import BrandInfo, CallAOutput, CallBOutput, SEOImprovements, Service, TargetAudience

logger = logging.getLogger(__name__)
settings = get_settings()


class LLMUnavailableError(Exception):
    pass


class LLMRepairFailedError(Exception):
    pass


class GeminiRateLimitError(Exception):
    """Raised on Gemini 429/503 to trigger model rotation, not a hard failure."""
    pass


def _sanitize_exc(exc: Exception) -> str:
    """Strip API keys from exception messages before logging."""
    msg = str(exc)
    msg = re.sub(r'(\?|&)key=[^&\s"\'>]+', r'\1key=***', msg)
    msg = re.sub(r'Bearer\s+[A-Za-z0-9._\-]{16,}', 'Bearer ***', msg)
    msg = re.sub(r'[0-9a-fA-F]{40,}', '***', msg)
    return msg


@runtime_checkable
class LLMProvider(Protocol):
    provider_name: str
    model_name: str

    async def generate(self, prompt: str, system: str) -> tuple[str, int, int]:
        """Returns (json_text, input_tokens, output_tokens)"""
        ...


class GeminiProvider:
    """
    Gemini via direct REST API. Rotates through free-tier model fallbacks
    when the primary hits a 429 or 503.
    """
    provider_name = "gemini"
    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    _ROTATION = [
        "gemini-1.5-flash-lite",
        "gemini-1.5-flash",
        "gemini-2.0-flash-lite",
    ]

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise LLMUnavailableError("GEMINI_API_KEY is not set")
        self.model_name = settings.gemini_model
        self._api_key = settings.gemini_api_key

    async def _call_model(self, model: str, prompt: str, system: str) -> tuple[str, int, int]:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        payload = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": settings.llm_temperature,
                "maxOutputTokens": settings.llm_max_output_tokens,
            },
        }
        url = f"{self._BASE_URL}/{model}:generateContent?key={self._api_key}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                resp = await client.post(url, json=payload)
        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            raise GeminiRateLimitError(f"{model} timed out")

        if resp.status_code in (429, 503):
            raise GeminiRateLimitError(f"{model} unavailable ({resp.status_code})")
        if resp.status_code != 200:
            raise Exception(f"Gemini API error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise Exception(f"Unexpected Gemini response shape: {exc}\n{json.dumps(data)[:300]}")

        usage = data.get("usageMetadata", {})
        return text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)

    async def generate(self, prompt: str, system: str) -> tuple[str, int, int]:
        candidates = [self.model_name] + [m for m in self._ROTATION if m != self.model_name]
        last_exc: Exception = GeminiRateLimitError("No models tried")
        for model in candidates:
            try:
                result = await self._call_model(model, prompt, system)
                if model != self.model_name:
                    logger.info(f"Gemini: using fallback model {model}")
                self.model_name = model
                return result
            except GeminiRateLimitError as exc:
                logger.warning(str(exc))
                last_exc = exc
        raise last_exc


class GroqProvider:
    provider_name = "groq"

    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise LLMUnavailableError("GROQ_API_KEY is not set")
        self.model_name = settings.groq_model
        self._client = AsyncGroq(api_key=settings.groq_api_key)

    async def generate(self, prompt: str, system: str) -> tuple[str, int, int]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_output_tokens,
            response_format={"type": "json_object"},
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        return text, (usage.prompt_tokens if usage else 0), (usage.completion_tokens if usage else 0)


class OpenRouterProvider:
    """OpenAI-compatible provider via OpenRouter. Used as optional Call A fallback."""
    provider_name = "openrouter"
    _BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self) -> None:
        if not settings.openrouter_api_key:
            raise LLMUnavailableError("OPENROUTER_API_KEY is not set")
        self.model_name = settings.openrouter_model
        self._api_key = settings.openrouter_api_key

    async def generate(self, prompt: str, system: str) -> tuple[str, int, int]:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:
            resp = await client.post(
                self._BASE_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "HTTP-Referer": "https://link-ed.onrender.com",
                    "X-Title": "link-ed",
                },
            )

        if resp.status_code != 200:
            raise Exception(f"OpenRouter error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise Exception(f"Unexpected OpenRouter response shape: {exc}")

        usage = data.get("usage", {})
        return text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def _make_provider(cls):
    """Instantiate a provider, return None if the required API key is missing."""
    try:
        return cls()
    except LLMUnavailableError:
        return None


class LLMService:
    """
    Two-call orchestrator.

    Call A: GeminiProvider (with internal rotation) -> OpenRouterProvider (optional fallback)
    Call B: GroqProvider (dedicated)
    """

    def __init__(
        self,
        primary: LLMProvider | None = None,
        fallback: LLMProvider | None = None,
        call_b_provider: LLMProvider | None = None,
    ) -> None:
        if primary is not None or fallback is not None or call_b_provider is not None:
            # Injected mode (tests)
            self._call_a_primary = primary
            self._call_a_fallback = fallback
            self._call_b = call_b_provider if call_b_provider is not None else primary
        else:
            # Production
            self._call_a_primary = _make_provider(GeminiProvider)
            self._call_a_fallback = _make_provider(OpenRouterProvider)
            self._call_b = _make_provider(GroqProvider)

    async def call_a(self, content_block: str) -> tuple[CallAOutput, str, str, int, int]:
        """Returns (CallAOutput, provider_name, model_name, in_tokens, out_tokens)."""
        prompt = _build_prompt_a(content_block)
        raw, provider_name, model_name, in_tok, out_tok = await self._run_call_a(prompt, _SYSTEM_CALL_A)
        parsed = self._parse_call_a(raw)
        return parsed, provider_name, model_name, in_tok, out_tok

    async def call_b(self, brand_profile: str) -> tuple[CallBOutput, int, int]:
        """Returns (CallBOutput, in_tokens, out_tokens). Requires Groq."""
        prompt = _build_prompt_b(brand_profile)
        raw, in_tok, out_tok = await self._run_call_b(prompt, _SYSTEM_CALL_B)
        parsed = self._parse_call_b(raw)
        return parsed, in_tok, out_tok

    async def _run_call_a(self, prompt: str, system: str) -> tuple[str, str, str, int, int]:
        if self._call_a_primary:
            for attempt in range(3):
                try:
                    text, in_tok, out_tok = await self._call_a_primary.generate(prompt, system)
                    return (
                        text,
                        self._call_a_primary.provider_name,
                        self._call_a_primary.model_name,
                        in_tok,
                        out_tok,
                    )
                except GeminiRateLimitError as e:
                    logger.warning(f"All Gemini models rate-limited: {e}")
                    break
                except Exception as e:
                    logger.warning(
                        f"Call A primary attempt {attempt + 1}/3 failed: "
                        f"{type(e).__name__}: {_sanitize_exc(e)}"
                    )
                    if attempt < 2:
                        await asyncio.sleep(1)

        if self._call_a_fallback:
            try:
                text, in_tok, out_tok = await self._call_a_fallback.generate(prompt, system)
                return (
                    text,
                    self._call_a_fallback.provider_name,
                    self._call_a_fallback.model_name,
                    in_tok,
                    out_tok,
                )
            except Exception as e:
                logger.error(
                    f"Call A fallback (OpenRouter) failed: "
                    f"{type(e).__name__}: {_sanitize_exc(e)}"
                )

        raise LLMUnavailableError("All Call A providers exhausted")

    async def _run_call_b(self, prompt: str, system: str) -> tuple[str, int, int]:
        if not self._call_b:
            raise LLMUnavailableError("GROQ_API_KEY not set - Groq is required for Call B")

        for attempt in range(3):
            try:
                text, in_tok, out_tok = await self._call_b.generate(prompt, system)
                return text, in_tok, out_tok
            except Exception as e:
                logger.warning(
                    f"Call B (Groq) attempt {attempt + 1}/3 failed: "
                    f"{type(e).__name__}: {_sanitize_exc(e)}"
                )
                if attempt < 2:
                    await asyncio.sleep(1)

        raise LLMUnavailableError("Groq (Call B) failed after 3 attempts")

    def _parse_call_a(self, raw: str) -> CallAOutput:
        try:
            data = self._parse_json(raw)
        except LLMRepairFailedError as e:
            raise LLMUnavailableError(f"LLM returned invalid JSON: {e}")
        try:
            return CallAOutput.model_validate(data)
        except ValidationError as e:
            logger.warning(f"Call A validation failed, attempting repair: {e}")
            try:
                repaired = self._repair_sync(raw, e, "call_a")
                return CallAOutput.model_validate(repaired)
            except (LLMRepairFailedError, ValidationError) as repair_err:
                raise LLMUnavailableError(f"LLM output unparseable after repair: {repair_err}")

    def _parse_call_b(self, raw: str) -> CallBOutput:
        try:
            data = self._parse_json(raw)
        except LLMRepairFailedError as e:
            raise LLMUnavailableError(f"LLM returned invalid JSON: {e}")
        try:
            return CallBOutput.model_validate(data)
        except ValidationError as e:
            logger.warning(f"Call B validation failed, attempting repair: {e}")
            try:
                repaired = self._repair_sync(raw, e, "call_b")
                return CallBOutput.model_validate(repaired)
            except (LLMRepairFailedError, ValidationError) as repair_err:
                raise LLMUnavailableError(f"LLM output unparseable after repair: {repair_err}")

    def _parse_json(self, raw: str) -> dict:
        """Extract JSON from raw LLM output, handling common model quirks."""
        text = raw.strip()

        # Strip <think>...</think> blocks (qwen/deepseek thinking mode)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # Strip markdown code fences
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()

        # Find the start of the JSON object in case the model prepended text
        brace = text.find("{")
        if brace > 0:
            text = text[brace:]

        # Strip trailing commas before } or ] (common model mistake)
        text = re.sub(r",\s*([}\]])", r"\1", text)

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMRepairFailedError(f"Invalid JSON from LLM: {e}\nRaw: {text[:300]}")

    def _repair_sync(self, raw: str, error: ValidationError, call_type: str) -> dict:
        """
        Programmatic repair for common structural issues.
        No second LLM call - just fills in missing fields and enforces constraints.
        """
        try:
            data = self._parse_json(raw)
        except Exception:
            raise LLMRepairFailedError("Cannot repair unparseable JSON")

        if call_type == "call_a":
            data = _repair_call_a(data)
        elif call_type == "call_b":
            data = _repair_call_b(data)

        return data


def _repair_call_a(data: dict) -> dict:
    data.setdefault("brand", {})
    data.setdefault("services", [])
    data.setdefault("seo_improvements", {})
    data.setdefault("target_audience", {})

    brand = data["brand"]
    brand.setdefault("name", "Unknown")
    brand.setdefault("category", "Unknown")
    brand.setdefault("summary", "")
    brand.setdefault("site_type", "other")
    brand.setdefault("competitive_advantages", [])
    brand.setdefault("opportunities", [])

    for svc in data["services"]:
        svc.setdefault("source", "inferred")
        svc.setdefault("evidence", None)
        svc.setdefault("url", None)
        if svc.get("evidence"):
            words = svc["evidence"].split()
            if len(words) > 15:
                svc["evidence"] = " ".join(words[:12]) + "..."

    seo = data["seo_improvements"]
    seo.setdefault("primary_keyword", "")
    seo.setdefault("secondary_keywords", [])
    seo.setdefault("long_tail_keywords", [])
    seo.setdefault("supporting_content", [])
    for rec in seo["supporting_content"]:
        rec.setdefault("url", None)

    if seo.get("recommended_title") and len(seo["recommended_title"]) > 70:
        seo["recommended_title"] = seo["recommended_title"][:67] + "..."
    if seo.get("meta_description") and len(seo["meta_description"]) > 165:
        seo["meta_description"] = seo["meta_description"][:162] + "..."

    ta = data["target_audience"]
    ta.setdefault("primary_audience", [])
    ta.setdefault("secondary_audience", [])
    ta.setdefault("industries", [])
    ta.setdefault("customer_needs", [])
    ta.setdefault("recommended_segments", [])

    return data


def _repair_call_b(data: dict) -> dict:
    data.setdefault("competitor_analysis", {})
    data.setdefault("target_search_queries", [])

    ca = data["competitor_analysis"]
    ca.setdefault("brand_overview", "")
    ca.setdefault("competitors", [])

    for comp in ca.get("competitors", []):
        comp["source"] = "ai_suggested"
        comp.setdefault("domain", None)
        comp.setdefault("domain_verified", None)
        comp.setdefault("strengths", [])
        comp.setdefault("weaknesses_or_gaps", [])

    for q in data["target_search_queries"]:
        q["source"] = "ai_suggested"
        q.setdefault("search_intent", "transactional")
        q.setdefault("business_relevance", "")
        q.setdefault("target_audience", "")

    return data


def build_brand_profile(call_a: CallAOutput) -> str:
    """Build a concise brand profile string to feed into Call B."""
    brand = call_a.brand
    ta = call_a.target_audience

    service_names = [s.service_name for s in call_a.services[:5]]
    industries = ta.industries[:5] if ta else []
    geo = ta.geographical_focus if ta else None
    primary_segments = [s.segment_name for s in (ta.primary_audience[:3] if ta else [])]
    customer_needs = ta.customer_needs[:4] if ta else []

    seo = call_a.seo_improvements
    primary_kw = seo.primary_keyword if seo else ""
    secondary_kws = ", ".join(seo.secondary_keywords[:4]) if seo else ""

    lines = [
        f"Brand Name: {brand.name}",
        f"Category: {brand.category}",
        f"Site Type: {brand.site_type}",
        f"Summary: {brand.summary}",
        f"Main Services: {', '.join(service_names) or 'N/A'}",
        f"Target Industries: {', '.join(industries) or 'N/A'}",
        f"Primary Customer Segments: {', '.join(primary_segments) or 'N/A'}",
        f"Core Customer Needs: {', '.join(customer_needs) or 'N/A'}",
        f"Geographical Focus: {geo or 'Global/unspecified'}",
        f"Competitive Advantages: {', '.join(brand.competitive_advantages[:3]) or 'N/A'}",
        f"Primary SEO Keyword: {primary_kw or 'N/A'}",
        f"Secondary Keywords: {secondary_kws or 'N/A'}",
    ]
    return "\n".join(lines)


_SYSTEM_CALL_A = """You are a website content analyst. Your job is to extract factual information from website content and generate structured business analysis.

RULES:
1. Only report what you can observe or reasonably infer from the PROVIDED content. Never fabricate.
2. For services: list services explicitly mentioned first (source: "extracted"), then fill up to 5 with reasonable inferences (source: "inferred"). Include evidence for extracted ones.
3. SEO title MUST be <= 60 characters. Meta description MUST be <= 155 characters. Primary keyword MUST appear in the recommended title.
4. Classify site_type as one of: saas, e_commerce, local_business, portfolio, agency, blog, nonprofit, corporate, educational, other.
5. Put evidence BEFORE description in services - cite what you saw, then conclude.
6. For extracted services, include the "url" field with the direct page URL if a link to that specific service page appears verbatim in the provided content (e.g. navigation links, href attributes). Set url to null if no specific page link is visible.
7. For supporting_content items, include the "url" field if that page already exists on the site and its URL appears in the content. Set url to null for genuinely new recommendations.
8. Return ONLY valid JSON matching the schema. No markdown, no explanation.

EXAMPLE OUTPUT STRUCTURE (fictional bakery - do not copy this content):
{
  "brand": {"name": "Sweet Crumbs", "category": "Food & Bakery", "summary": "Local artisan bakery.", "site_type": "local_business", "competitive_advantages": ["Handmade products"], "opportunities": ["Online ordering"]},
  "services": [{"service_name": "Custom Wedding Cakes", "description": "Bespoke cakes for weddings.", "target_industry": "Events", "target_customer": "Engaged couples", "main_benefit": "Personalized design", "source": "extracted", "evidence": "Listed on Services page under Wedding", "url": "https://sweetcrumbs.com/wedding-cakes"}],
  "seo_improvements": {"primary_keyword": "custom wedding cakes", "secondary_keywords": ["artisan cakes"], "long_tail_keywords": ["custom wedding cakes near me"], "recommended_title": "Sweet Crumbs | Custom Wedding Cakes", "meta_description": "Handmade wedding cakes crafted to order. Sweet Crumbs brings your vision to life.", "supporting_content": [{"type": "faq", "title": "Wedding Cake FAQ", "rationale": "Addresses common questions", "url": null}]},
  "target_audience": {"primary_audience": [{"segment_name": "Engaged Couples", "business_type": "Consumer", "requirement": "Wedding cake", "needs_and_challenges": ["Unique design"], "interests": ["Weddings"]}], "secondary_audience": [], "industries": ["Events", "Food"], "geographical_focus": "Local", "customer_needs": ["Quality ingredients"], "recommended_segments": ["Engaged couples planning weddings"]}
}"""


_SYSTEM_CALL_B = """You are a digital marketing strategist. Given a brand profile, identify real competitors and generate high-quality, platform-native search queries that potential customers actually use to find this type of business.

COMPETITOR RULES:
1. Suggest at least 3 real, named competitors with known domains. Do NOT invent companies.
2. All competitors are AI-suggested - set source: "ai_suggested" on every item.

SEARCH QUERY RULES:
3. Generate EXACTLY 5 queries, one per platform: Google, Facebook, Instagram, LinkedIn, X.
4. Write queries from the CUSTOMER'S perspective - what someone types when they need this service. NOT the brand's own keywords.
5. Do NOT use the brand name in any query.
6. Each query must be specific to the brand's actual services and target segment, not generic filler.

PLATFORM-SPECIFIC FORMATS:
- Google: Transactional/commercial intent. What someone types when ready to hire/buy. E.g. "UX design agency for fintech startups" or "best inventory management software for small retailers"
- LinkedIn: B2B professional search. How a decision-maker finds vendors. E.g. "data analytics consulting firm for mid-market manufacturing" or "enterprise HR software solutions"
- Facebook: Local/community discovery or interest-group search. E.g. "affordable brand identity designer for small business" or "digital marketing services for restaurant owners"
- Instagram: Short, visual discovery or inspiration-driven. E.g. "minimalist logo design portfolio" or "sustainable packaging branding". Keep it concise and hashtag-friendly.
- X: Conversational recommendation request or topic search. E.g. "anyone know a solid no-code SaaS tool for client onboarding?" or "best email marketing tool for ecommerce brands?"

METADATA RULES:
- search_intent: Use exactly one of - "transactional", "commercial", "informational", "navigational"
  * transactional = ready to buy/hire now
  * commercial = comparing options before deciding
  * informational = learning about a topic/solution
  * navigational = looking for a specific resource/page
- business_relevance: One concrete sentence: which customer pain point this query captures and why it leads to this brand's services.
- target_audience: A specific persona description (e.g. "Early-stage B2B SaaS founder needing a launch website") - not a generic category.

7. Do NOT include search volume, rankings, or traffic numbers.
8. Return ONLY valid JSON. No markdown, no explanation."""


def _build_prompt_a(content_block: str) -> str:
    return f"""Analyze the following website content and return a JSON object with keys: brand, services, seo_improvements, target_audience.

{content_block}

Return valid JSON only. Schema:
{{
  "brand": {{"name": str, "category": str, "summary": str, "site_type": str, "competitive_advantages": [str], "opportunities": [str]}},
  "services": [{{"service_name": str, "description": str, "target_industry": str, "target_customer": str, "main_benefit": str, "source": "extracted"|"inferred", "evidence": str|null, "url": str|null}}],
  "seo_improvements": {{"primary_keyword": str, "secondary_keywords": [str], "long_tail_keywords": [str], "recommended_title": str, "meta_description": str, "supporting_content": [{{"type": str, "title": str, "rationale": str, "url": str|null}}]}},
  "target_audience": {{"primary_audience": [{{"segment_name": str, "business_type": str, "requirement": str, "needs_and_challenges": [str], "interests": [str]}}], "secondary_audience": [...], "industries": [str], "geographical_focus": str|null, "customer_needs": [str], "recommended_segments": [str]}}
}}"""


def _build_prompt_b(brand_profile: str) -> str:
    return f"""Given this brand profile, identify real competitors and generate 5 platform-specific search queries potential customers use to find this type of business.

BRAND PROFILE:
{brand_profile}

Return valid JSON only. Schema:
{{
  "competitor_analysis": {{
    "brand_overview": str,
    "competitors": [
      {{"name": str, "domain": str|null, "source": "ai_suggested", "main_service": str, "target_market": str, "strengths": [str], "weaknesses_or_gaps": [str]}}
    ]
  }},
  "target_search_queries": [
    {{
      "query": str,
      "platform": "Google"|"Facebook"|"Instagram"|"LinkedIn"|"X",
      "search_intent": "transactional"|"commercial"|"informational"|"navigational",
      "business_relevance": str,
      "target_audience": str,
      "source": "ai_suggested"
    }}
  ]
}}

Hard requirements:
- At least 3 named competitors with real domains
- Exactly 5 queries - one per platform (Google, Facebook, Instagram, LinkedIn, X)
- Every query must reflect a real customer need, NOT the brand name or tagline
- search_intent must be one of the four allowed values above"""
