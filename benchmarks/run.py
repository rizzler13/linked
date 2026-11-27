"""
Benchmarking script for link-ed LLM provider comparison.

Runs analysis on 5 diverse websites and records:
- Wall-clock latency (p50, p95)
- Token usage (input/output per call)
- JSON validity rate
- Computed cost from actual token usage

NOTE: This is a 5-site manual evaluation, NOT a statistically significant
benchmark. Results are indicative and reflect a single test run.

Accuracy is scored with a 1–5 manual rubric (see below). Scores are
subjective and noted as such.

Usage:
    cd link-ed
    python -m benchmarks.run
    python -m benchmarks.run --provider gemini   # gemini only
    python -m benchmarks.run --provider groq     # groq only
"""

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone

TEST_URLS = [
    "https://stripe.com",        # large SaaS
    "https://shopify.com",       # e-commerce platform
    "https://basecamp.com",      # project management SaaS
    "https://doist.com",         # productivity tool (smaller company)
    "https://www.notion.so",     # note-taking/workspace SaaS
]

# Gemini 2.0 Flash pricing (as of Nov 2026, verify before publishing)
GEMINI_PRICE_IN  = 0.10 / 1_000_000   # $ per input token
GEMINI_PRICE_OUT = 0.40 / 1_000_000   # $ per output token

# Groq Llama-3.1-70B: free tier
GROQ_PRICE_IN  = 0.0
GROQ_PRICE_OUT = 0.0


def compute_cost(in_tokens: int, out_tokens: int, provider: str) -> float:
    if provider == "gemini":
        return in_tokens * GEMINI_PRICE_IN + out_tokens * GEMINI_PRICE_OUT
    return 0.0


async def run_one(url: str, provider_override: str | None = None) -> dict:
    from app.services.analysis_service import run_analysis

    start = time.monotonic()
    try:
        result = await run_analysis(url)
        elapsed = time.monotonic() - start
        tok = result.meta.tokens
        total_in  = tok.call_a_in + tok.call_b_in
        total_out = tok.call_a_out + tok.call_b_out
        return {
            "url": url,
            "success": True,
            "latency_s": round(elapsed, 2),
            "provider": result.meta.provider,
            "model": result.meta.model,
            "tokens_in": total_in,
            "tokens_out": total_out,
            "total_tokens": total_in + total_out,
            "cost_usd": round(compute_cost(total_in, total_out, result.meta.provider), 6),
            "content_quality": result.meta.content_quality,
            "pages_fetched": result.meta.pages_fetched,
            "competitors_found": len(result.analysis.competitor_analysis.competitors),
            "services_found": len(result.analysis.top_services),
            "warnings": result.meta.warnings,
            "accuracy_score": None,   # fill in manually after reviewing output
            "accuracy_notes": "",
        }
    except Exception as e:
        return {
            "url": url,
            "success": False,
            "error": str(e),
            "latency_s": round(time.monotonic() - start, 2),
        }


async def main(provider_override: str | None = None):
    print(f"\nlink-ed Benchmark Run")
    print(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Sites: {len(TEST_URLS)}")
    print(f"Provider: {provider_override or 'default (gemini → groq fallback)'}")
    print("-" * 60)

    results = []
    for url in TEST_URLS:
        print(f"  Testing {url} ...", end=" ", flush=True)
        r = await run_one(url, provider_override)
        if r["success"]:
            print(f"✓ {r['latency_s']}s | {r['total_tokens']} tokens | ${r['cost_usd']}")
        else:
            print(f"✗ {r.get('error', 'unknown error')}")
        results.append(r)

    # Summary statistics
    successful = [r for r in results if r["success"]]
    if successful:
        latencies = [r["latency_s"] for r in successful]
        tokens    = [r["total_tokens"] for r in successful]
        costs     = [r["cost_usd"] for r in successful]

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Success rate:    {len(successful)}/{len(results)}")
        print(f"Latency p50:     {statistics.median(latencies):.2f}s")
        print(f"Latency p95:     {sorted(latencies)[int(len(latencies)*0.95)]:.2f}s")
        print(f"Latency min/max: {min(latencies):.2f}s / {max(latencies):.2f}s")
        print(f"Avg tokens/req:  {statistics.mean(tokens):.0f}")
        print(f"Avg cost/req:    ${statistics.mean(costs):.6f}")
        print(f"Total cost:      ${sum(costs):.6f}")
        print()
        print("NOTE: Accuracy requires manual review. See results JSON.")
        print("Fill in accuracy_score (1–5) and accuracy_notes per URL.")
        print()
        print("Accuracy rubric:")
        print("  1 = mostly wrong or generic")
        print("  2 = partially relevant")
        print("  3 = acceptable, mostly accurate")
        print("  4 = good, specific to the site")
        print("  5 = excellent, actionable insights")

    # Save to file
    output_file = f"benchmarks/results_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(output_file, "w") as f:
        json.dump({"run_date": datetime.now(timezone.utc).isoformat(), "results": results}, f, indent=2)
    print(f"\nResults saved to {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["gemini", "groq"], default=None)
    args = parser.parse_args()
    asyncio.run(main(args.provider))
