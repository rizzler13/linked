# link-ed

AI powered website intelligence API. Drop in a URL, get competitor analysis, SEO recommendations, search queries, services, and audience profiling, in seconds.

> Built as a submission for the Dxora Technologies Python test.

---

## Live

**Dashboard** - https://linked-6oi2.onrender.com


**Docs** - https://linked-6oi2.onrender.com/docs

Ping `/health` before demoing.

## [Why I cooked this?](cooked.md)

---
<img width="1041" height="820" alt="image" src="https://github.com/user-attachments/assets/3788af2b-ed21-459d-85c3-f57f7385fe22" />


## Quick Start

```bash
git clone https://github.com/your-username/link-ed && cd link-ed
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # fill in GEMINI_API_KEY and GROQ_API_KEY
uvicorn app.main:app --host 0.0.0.0 --port 4000
```

---
### Competitor Analysis
<img width="941" height="820" alt="image" src="https://github.com/user-attachments/assets/07b81c70-14d8-441b-bab2-9bef0e47eb2b" />

### Search Queries
<img width="904" height="586" alt="image" src="https://github.com/user-attachments/assets/47d5558e-e315-4083-8588-7587b25024c9" />

### SEO
<img width="879" height="652" alt="image" src="https://github.com/user-attachments/assets/53f6e8cd-96ab-419f-ae4f-0b1ae69f3b10" />


### Services
<img width="943" height="814" alt="image" src="https://github.com/user-attachments/assets/7904397b-cb1c-4041-a256-06e4391fafdf" />


### Audience
<img width="934" height="776" alt="image" src="https://github.com/user-attachments/assets/3d6b768c-7828-4942-a22a-358e053b4612" />



## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/link-analysis` | Full analysis, single JSON response |
| `POST` | `/link-analysis/stream` | Same analysis via SSE, streams partial results as they arrive |
| `POST` | `/link-analysis/pdf` | Download analysis as PDF (Chromium print engine) |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | API documentation |

All three analysis endpoints accept `{ "url": "https://example.com" }`.

### SSE Event Types (`/stream`)

| Event | Payload | When |
|---|---|---|
| `status` | `{"message": str}` | Throughout pipeline |
| `partial` | brand + seo + services + audience | ~5–10s in, after Call A |
| `result` | complete response | Pipeline complete |
| `error` | `{"code": str, "message": str}` | On failure |

---

## Response Schema

```json
{
  "success": true,
  "website": "https://stripe.com",
  "meta": {
    "request_id": "3f8a2b1c-...",
    "provider": "gemini",
    "model": "gemini-2.0-flash",
    "latency_ms": 7420,
    "tokens": { "call_a_in": 4800, "call_a_out": 1200, "call_b_in": 480, "call_b_out": 900 },
    "content_quality": "rich",
    "pages_fetched": 1,
    "warnings": []
  },
  "brand": {
    "name": "Stripe",
    "category": "Payment Infrastructure",
    "summary": "Stripe provides payment APIs and financial infrastructure for internet businesses.",
    "site_type": "saas",
    "competitive_advantages": ["Extensive API documentation", "Global coverage"],
    "opportunities": ["SMB market penetration", "Crypto payment expansion"]
  },
  "analysis": {
    "competitor_analysis": {
      "brand_overview": "Stripe is the leading developer-first payment platform...",
      "competitors": [{
        "name": "Braintree", "domain": "braintreepayments.com", "domain_verified": true,
        "source": "ai_suggested", "main_service": "Payment processing",
        "target_market": "Enterprise e-commerce",
        "strengths": ["PayPal integration"], "weaknesses_or_gaps": ["Less developer-friendly"]
      }]
    },
    "target_search_queries": [{
      "query": "best payment gateway for SaaS companies",
      "platform": "Google",
      "search_intent": "transactional",
      "business_relevance": "Direct service match for SaaS billing",
      "target_audience": "SaaS founders and CTOs",
      "source": "ai_suggested"
    }],
    "seo_improvements": {
      "primary_keyword": "payment processing API",
      "secondary_keywords": ["online payments", "payment gateway"],
      "long_tail_keywords": ["payment API for startups"],
      "recommended_title": "Stripe | Payment Processing API for Developers",
      "meta_description": "Accept payments, manage subscriptions, and scale your business.",
      "supporting_content": [{ "type": "landing_page", "title": "Payment API for Startups", "rationale": "Targets early-stage companies" }]
    },
    "top_services": [{
      "service_name": "Stripe Payments",
      "description": "Accept cards, bank transfers, and 100+ payment methods.",
      "target_industry": "E-commerce", "target_customer": "Online businesses",
      "main_benefit": "Increase conversion with optimized checkout",
      "source": "extracted", "evidence": "Homepage hero section"
    }],
    "target_audience": {
      "primary_audience": [{
        "segment_name": "SaaS Developers", "business_type": "Technology",
        "requirement": "Subscription billing and API integration",
        "needs_and_challenges": ["PCI compliance", "Global payments"],
        "interests": ["Developer tools", "Scalable infrastructure"]
      }],
      "secondary_audience": [],
      "industries": ["Technology", "E-commerce", "Financial Services"],
      "geographical_focus": null,
      "customer_needs": ["Reliable payment processing"],
      "recommended_segments": ["Early-stage startups building SaaS"]
    }
  }
}
```

`search_intent` is constrained to: `transactional` · `commercial` · `informational` · `navigational`

---

## Architecture

### Two-Call LLM Split

```
Page content
    │
    ▼
Call A (Gemini — grounded)        → brand, services, SEO, audience
    │
    │  brand profile (~500 tokens)
    ▼
Call B (Groq — ungrounded)        → competitors, search queries
    │
    ▼
Competitor domain verification    → parallel HTTP HEAD checks
    │
    ▼
Business rule validation          → dedup, truncate, platform coverage
    │
    ▼
Response
```

**Why split?** Competitors and search queries require world knowledge — they can't be grounded in page content. Call B gets only the brand profile (~500 tokens) instead of the full page, saving ~4K tokens per request.

### Content Extraction — 7 Layers

| Layer | Source | What it captures |
|---|---|---|
| 1 | JSON-LD (Schema.org) | Structured business data |
| 2 | Open Graph / Twitter | Title, description, type |
| 3 | `<meta>` tags | Description, keywords, canonical |
| 4 | Navigation links | Services list proxy |
| 5 | Heading hierarchy | h1–h3 structural outline |
| 6 | Main body text | Readability-extracted, boilerplate removed |
| 7 | Footer | Location, contact, socials |

### Bot Bypass — 3-Phase Waterfall

| Phase | Method | Bypasses |
|---|---|---|
| 1 | httpx + 4 browser header profiles | Basic UA / header checks |
| 2 | curl_cffi TLS impersonation (Chrome JA3/JA4) | TLS fingerprint checks (Cloudflare WAF) |
| 3 | Playwright + playwright-stealth | Full JS challenges, Cloudflare Managed Challenge, non-interactive Turnstile |

Each phase only runs if the previous returned a bot-block. Phase 3 patches 50+ browser automation tells. Interactive CAPTCHAs (visible checkbox/puzzle) are not solvable programmatically.

### SSRF Protection

Every URL — including LLM-suggested competitor domains — passes a full guard:

- HTTP/HTTPS only, no userinfo, ports 80/443 only
- DNS resolution + IP check against RFC-1918, loopback, link-local, metadata ranges
- Manual redirect handling — every hop re-validated
- Dual A-record protection (all resolved IPs must pass, not just the first)

---

## Project Structure

```
link-ed/
├── app/
│   ├── main.py                    # FastAPI app + all endpoints
│   ├── config.py                  # Settings (pydantic-settings)
│   ├── schemas.py                 # All Pydantic models
│   ├── services/
│   │   ├── analysis_service.py    # Pipeline orchestrator + SSE generator
│   │   ├── website_fetcher.py     # 3-phase fetch waterfall + SSRF guard
│   │   ├── content_extractor.py   # 7-layer extraction (extruct + BS4)
│   │   ├── content_cleaner.py     # Quality scoring, token budget
│   │   ├── llm_service.py         # Gemini + Groq + retry + JSON repair
│   │   ├── competitor_verifier.py # Parallel domain verification
│   │   └── pdf_service.py         # Playwright HTML-to-PDF
│   └── utils/
│       ├── ssrf_guard.py          # URL validation, DNS, IP blocklist
│       └── rate_limiter.py        # Rate limiting, TTL cache, semaphore
├── static/
│   ├── index.html                 # Dashboard (SSE streaming + typewriter UI)
│   └── api-docs.html              # API docs page
├── tests/                         # 84 tests, no API keys needed
├── benchmarks/run.py              # Provider comparison script
├── .env.example
├── requirements.txt
├── Dockerfile
└── render.yaml
```

---

## LLM Providers

| Provider | Model | Cost (in/out per 1M) | JSON | Context |
|---|---|---|---|---|
| **Google (primary)** | Gemini 2.0 Flash | $0.10 / $0.40 | Native schema | 1M tokens |
| **Groq (fallback)** | Llama-3.1-70B | Free / Free | json_object | 128K |
| OpenAI | GPT-4o-mini | $0.15 / $0.60 | Structured output | 128K |
| Anthropic | Claude Haiku 4.5 | $0.80 / $4.00 | JSON mode | 200K |

**Why Gemini Flash:** Native JSON schema enforcement (eliminates most validation failures), 1M context, cheapest capable option, 15 RPM free tier.

**Why Groq fallback:** Completely free, fastest inference, reliable json_object mode.

### Cost per Request

| Call | Tokens | Cost |
|---|---|---|
| Call A input | ~5,000 | $0.000500 |
| Call A output | ~1,500 | $0.000600 |
| Call B input | ~500 | $0.000050 |
| Call B output | ~1,000 | $0.000400 |
| **Total** | **~8,000** | **~$0.0015** |

~667 analyses per $1 at Gemini paid tier. Cache hits are free.

---

## Error Reference

| HTTP | Code | Condition |
|---|---|---|
| 422 | `INVALID_URL` | Malformed or missing URL |
| 400 | `SSRF_BLOCKED` | URL resolves to internal/private IP |
| 403 | `BOT_BLOCKED` | Site blocked all fetch attempts (Cloudflare, login wall) |
| 502 | `SITE_UNREACHABLE` | Site returns 4xx/5xx |
| 504 | `FETCH_TIMEOUT` | Fetch timed out |
| 415 | `NOT_HTML` | Non-HTML content type |
| 413 | `CONTENT_TOO_LARGE` | Response > 2 MB |
| 503 | `LLM_UNAVAILABLE` | All LLM providers down |
| 429 | `RATE_LIMITED` | 5 req/min per IP exceeded |

JS-only SPAs return HTTP 200 with `content_quality: "minimal"` and a note in `meta.warnings`.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Google Gemini API key |
| `GROQ_API_KEY` | Yes | Groq API key |
| `OPENROUTER_API_KEY` | No | OpenRouter key (optional Gemini fallback) |
| `APP_ENV` | No | `development` / `production` (default: `development`) |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` (default: `INFO`) |
| `PORT` | No | Server port (default: `4000`) |

---

## Tests

```bash
pytest                          # run all 84 tests
pytest tests/test_ssrf_guard.py -v
pytest --cov=app tests/
```

No API keys needed — LLM providers are mocked.

---

