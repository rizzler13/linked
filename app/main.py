"""
link-ed - AI-Powered Website Link Analysis API
"""

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import get_settings
from app.schemas import ErrorCode, ErrorDetail, ErrorResponse, LinkAnalysisRequest
from app.services.analysis_service import run_analysis, run_analysis_stream
from app.services.website_fetcher import FetchError
from app.services.llm_service import LLMUnavailableError
from app.utils.rate_limiter import (
    get_cached_result,
    limiter,
    llm_semaphore,
    set_cached_result,
)
from app.utils.ssrf_guard import SSRFError

settings = get_settings()
logger = logging.getLogger(__name__)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# httpx logs full URLs which can contain API keys
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"link-ed starting on port {settings.port}")
    logger.info(f"Call A: {settings.gemini_model} (OpenRouter fallback: {'enabled' if settings.openrouter_api_key else 'disabled'})")
    logger.info(f"Call B: {settings.groq_model}")
    yield
    logger.info("link-ed shutting down")


app = FastAPI(
    title="link-ed",
    description="""
## AI-Powered Website Intelligence API

**link-ed** fetches any public website URL, extracts structured content across multiple layers (JSON-LD -> OpenGraph -> meta -> headings -> body), and passes it through a two-stage LLM pipeline to produce business-grade analysis.

### What you get from a single URL

| Section | Description |
|---|---|
| **Brand profile** | Name, category, summary, site type, competitive advantages |
| **Competitor analysis** | 3+ AI-suggested competitors with verified domains, strengths, and gaps |
| **Target search queries** | 5 platform-specific queries (Google, Facebook, Instagram, LinkedIn, X) |
| **SEO improvements** | Primary/secondary/long-tail keywords, recommended title + meta description, content page suggestions |
| **Top services** | Up to 5 services with industry, customer, and benefit details |
| **Target audience** | Primary and secondary audience segments with needs, interests, and geographic focus |

### LLM Pipeline

| Call | Provider | Purpose |
|---|---|---|
| **Call A** (grounded) | Gemini -> OpenRouter (optional fallback) | Brand, services, SEO, audience |
| **Call B** (ungrounded) | Groq (dedicated) | Competitor intelligence + search queries |

### Endpoints

- `POST /link-analysis` - standard JSON response (waits for full analysis)
- `POST /link-analysis/stream` - Server-Sent Events stream with progress updates and partial results

### SSE Event Types

| Event | Data |
|---|---|
| `status` | `{"message": str}` - progress update |
| `partial` | brand + services + SEO + audience (available after Call A) |
| `result` | complete analysis response |
| `error` | `{"code": str, "message": str}` |

### Protections

- SSRF guard on every request and every redirect hop
- Per-IP rate limiting (5 req/min)
- 10-minute URL-keyed result cache
- 2 MB streamed body cap
- Prompt injection wrapper around all untrusted website content
""",
    version="1.0.0",
    contact={"name": "link-ed API", "url": "https://github.com"},
    license_info={"name": "MIT"},
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_tags=[
        {"name": "analysis", "description": "Website analysis endpoints"},
        {"name": "system",   "description": "Health and status"},
    ],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    response.headers["X-Response-Time-Ms"] = str(elapsed_ms)
    return response


@app.post(
    "/link-analysis",
    response_model=None,
    tags=["analysis"],
    summary="Analyze a website URL",
    description=(
        "Fetches the website, extracts content across 7 layers, then runs a two-stage LLM "
        "pipeline. Returns the full analysis as a single JSON response. "
        "Results are cached for 10 minutes per URL. "
        "For streaming with progress updates, use POST /link-analysis/stream."
    ),
    responses={
        200: {"description": "Successful analysis"},
        400: {"model": ErrorResponse, "description": "SSRF-blocked URL"},
        403: {"model": ErrorResponse, "description": "Site blocked all fetch attempts (bot protection, login wall, or Cloudflare)"},
        413: {"model": ErrorResponse, "description": "Response too large"},
        415: {"model": ErrorResponse, "description": "Non-HTML content"},
        422: {"model": ErrorResponse, "description": "Invalid URL"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        502: {"model": ErrorResponse, "description": "Site unreachable"},
        503: {"model": ErrorResponse, "description": "LLM unavailable"},
        504: {"model": ErrorResponse, "description": "Fetch timeout"},
    },
)
@limiter.limit(f"{settings.rate_limit_per_minute}/minute")
async def link_analysis(request: Request, body: LinkAnalysisRequest):
    url = body.url

    cached = get_cached_result(url)
    if cached is not None:
        logger.info(f"Cache hit for {url}")
        return cached

    async with llm_semaphore:
        try:
            result = await run_analysis(url)
        except FetchError as e:
            return _fetch_error_response(e)
        except SSRFError as e:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCode.SSRF_BLOCKED,
                        message=f"URL is blocked for security reasons: {e.reason}",
                    )
                ).model_dump(),
            )
        except LLMUnavailableError:
            return JSONResponse(
                status_code=503,
                content=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCode.LLM_UNAVAILABLE,
                        message="All LLM providers are currently unavailable. Please try again shortly.",
                    )
                ).model_dump(),
            )
        except Exception as e:
            logger.exception(f"Unexpected error analyzing {url}: {e}")
            return JSONResponse(
                status_code=500,
                content=ErrorResponse(
                    error=ErrorDetail(
                        code=ErrorCode.INTERNAL_ERROR,
                        message="An unexpected error occurred. Please try again.",
                    )
                ).model_dump(),
            )

    set_cached_result(url, result)
    return result


@app.post(
    "/link-analysis/stream",
    tags=["analysis"],
    summary="Stream website analysis (Server-Sent Events)",
    description=(
        "Same analysis as POST /link-analysis but streams results progressively using SSE. "
        "Emits 'status' events as the pipeline progresses, a 'partial' event with brand/services/SEO "
        "as soon as Call A completes, then a final 'result' event with the complete response. "
        "On error, emits an 'error' event instead of a final result."
    ),
    response_class=StreamingResponse,
)
@limiter.limit(f"{settings.rate_limit_per_minute}/minute")
async def link_analysis_stream(request: Request, body: LinkAnalysisRequest):
    url = body.url

    async def generate():
        cached = get_cached_result(url)
        if cached is not None:
            logger.info(f"Cache hit (stream) for {url}")
            data = cached.model_dump() if hasattr(cached, "model_dump") else cached
            yield f"event: result\ndata: {json.dumps(data)}\n\n"
            return

        async with llm_semaphore:
            try:
                async for event in run_analysis_stream(url):
                    etype = event["type"]
                    edata = event["data"]

                    if etype == "result":
                        set_cached_result(url, edata)
                        edata = edata.model_dump()

                    yield f"event: {etype}\ndata: {json.dumps(edata)}\n\n"

            except FetchError as e:
                err = _fetch_error_dict(e)
                yield f"event: error\ndata: {json.dumps(err)}\n\n"
            except SSRFError as e:
                yield f"event: error\ndata: {json.dumps({'code': 'SSRF_BLOCKED', 'message': f'URL blocked: {e.reason}'})}\n\n"
            except LLMUnavailableError:
                yield f"event: error\ndata: {json.dumps({'code': 'LLM_UNAVAILABLE', 'message': 'All LLM providers unavailable.'})}\n\n"
            except Exception as e:
                logger.exception(f"Streaming error for {url}: {e}")
                yield f"event: error\ndata: {json.dumps({'code': 'INTERNAL_ERROR', 'message': 'An unexpected error occurred.'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disables Nginx proxy buffering
            "Connection": "keep-alive",
        },
    )


@app.post(
    "/link-analysis/pdf",
    tags=["analysis"],
    summary="Download analysis as PDF",
    description=(
        "Same as POST /link-analysis but returns the result as a downloadable PDF. "
        "Checks the cache first; runs a fresh analysis if the URL hasn't been seen recently."
    ),
    responses={
        200: {"description": "PDF file"},
        400: {"model": ErrorResponse, "description": "SSRF-blocked URL"},
        403: {"model": ErrorResponse, "description": "Site blocked all fetch attempts"},
        503: {"model": ErrorResponse, "description": "LLM unavailable"},
    },
)
@limiter.limit(f"{settings.rate_limit_per_minute}/minute")
async def link_analysis_pdf(request: Request, body: LinkAnalysisRequest):
    url = body.url

    result = get_cached_result(url)
    if result is None:
        async with llm_semaphore:
            try:
                result = await run_analysis(url)
            except FetchError as e:
                return _fetch_error_response(e)
            except SSRFError as e:
                return JSONResponse(
                    status_code=400,
                    content=ErrorResponse(
                        error=ErrorDetail(code=ErrorCode.SSRF_BLOCKED, message=f"URL is blocked: {e.reason}")
                    ).model_dump(),
                )
            except LLMUnavailableError:
                return JSONResponse(
                    status_code=503,
                    content=ErrorResponse(
                        error=ErrorDetail(code=ErrorCode.LLM_UNAVAILABLE, message="LLM unavailable.")
                    ).model_dump(),
                )
            except Exception as e:
                logger.exception(f"Unexpected error analyzing {url}: {e}")
                return JSONResponse(
                    status_code=500,
                    content=ErrorResponse(
                        error=ErrorDetail(code=ErrorCode.INTERNAL_ERROR, message="An unexpected error occurred.")
                    ).model_dump(),
                )
        set_cached_result(url, result)

    try:
        from app.services.pdf_service import generate_pdf
        pdf_bytes = await generate_pdf(result)
    except Exception as e:
        logger.exception(f"PDF generation failed for {url}: {e}")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorDetail(code=ErrorCode.INTERNAL_ERROR, message="PDF generation failed.")
            ).model_dump(),
        )

    domain = urlparse(url).netloc.replace(".", "-") or "analysis"
    filename = f"link-ed-{domain}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/health", tags=["system"], summary="Health check")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/docs", include_in_schema=False)
async def custom_docs():
    with open(_STATIC_DIR / "api-docs.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/redoc", include_in_schema=False)
async def custom_redoc():
    with open(_STATIC_DIR / "api-docs.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


def _fetch_error_response(e: FetchError) -> JSONResponse:
    code_map = {
        "SSRF_BLOCKED":      (400, ErrorCode.SSRF_BLOCKED),
        "BOT_BLOCKED":       (403, ErrorCode.BOT_BLOCKED),
        "SITE_UNREACHABLE":  (502, ErrorCode.SITE_UNREACHABLE),
        "FETCH_TIMEOUT":     (504, ErrorCode.FETCH_TIMEOUT),
        "NOT_HTML":          (415, ErrorCode.NOT_HTML),
        "CONTENT_TOO_LARGE": (413, ErrorCode.CONTENT_TOO_LARGE),
    }
    http_status, error_code = code_map.get(e.code, (502, ErrorCode.SITE_UNREACHABLE))
    return JSONResponse(
        status_code=http_status,
        content=ErrorResponse(
            error=ErrorDetail(code=error_code, message=e.message)
        ).model_dump(),
    )


def _fetch_error_dict(e: FetchError) -> dict:
    """Same as _fetch_error_response but returns a plain dict for the SSE stream."""
    code_map = {
        "SSRF_BLOCKED":      "SSRF_BLOCKED",
        "BOT_BLOCKED":       "BOT_BLOCKED",
        "SITE_UNREACHABLE":  "SITE_UNREACHABLE",
        "FETCH_TIMEOUT":     "FETCH_TIMEOUT",
        "NOT_HTML":          "NOT_HTML",
        "CONTENT_TOO_LARGE": "CONTENT_TOO_LARGE",
    }
    return {"code": code_map.get(e.code, "SITE_UNREACHABLE"), "message": e.message}


@app.get("/", include_in_schema=False)
async def dashboard():
    with open(_STATIC_DIR / "index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


try:
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
except Exception:
    pass  # static dir may not exist in test environments
