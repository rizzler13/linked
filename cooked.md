## Extra Features in Linked 

### API & Delivery
| Feature | Detail |
|---|---|
| **SSE Streaming** (`/stream`) | Streams partial results live — brand/SEO/services arrive ~5s in, before the full response is ready |
| **PDF Export** (`/pdf`) | Chromium print-to-PDF via Playwright — no extra dependencies, A4 formatted report |
| **JSON Download** | Client-side only — no server roundtrip, downloads the already-in-memory result instantly |
| **Result Caching** | 10-minute URL-keyed cache — repeat requests return instantly, no LLM cost |

### Intelligence Pipeline
| Feature | Detail |
|---|---|
| **Two-Call LLM Split** | Call A (Gemini, grounded on page content) → Call B (Groq, world knowledge for competitors/queries) — saves ~4K tokens per request |
| **OpenRouter Fallback** | Optional secondary path for Gemini if the primary key is rate-limited |
| **Competitor Domain Verification** | Every AI-suggested competitor domain gets a live HTTP HEAD check to confirm it's real |
| **7-Layer Content Extraction** | JSON-LD → OpenGraph → meta → nav links → headings → body text → footer, in priority order |

### Resilience & Security
| Feature | Detail |
|---|---|
| **3-Phase Bot Bypass** | httpx (UA spoofing) → curl_cffi (TLS fingerprint / JA3 impersonation) → Playwright stealth (full JS challenges, non-interactive Turnstile) |
| **SSRF Protection** | Every URL — including LLM-suggested ones — blocked against RFC-1918, loopback, link-local, and cloud metadata ranges, with per-hop redirect re-validation |
| **Per-IP Rate Limiting** | 5 req/min enforced via slowapi middleware |

### Developer Experience
| Feature | Detail |
|---|---|
| **Typewriter UI** | `requestAnimationFrame` time-delta loop at 50 chars/sec on the brand summary |
| **Partial Render on Partial Data** | Dashboard shows brand card + loading spinners for competitors/queries the moment Call A finishes — before Call B completes |
| **84 Tests, No API Keys Needed** | Full test suite with mocked LLM providers |
| **Custom Docs Page** | Hand-crafted `/docs` instead of default Swagger UI |
