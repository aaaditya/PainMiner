"""Batch market analysis across multiple niches.

Runs the full PainMiner pipeline (crawl → extract → score → cluster) for
each keyword, aggregates results, and ranks niches by:
  - Pain intensity    (average severity_score of all opportunities)
  - Opportunity density (volume of relevant discussions found)
  - Buyer clarity     (how concentrated / specific the buyer personas are)

Finally surfaces the top 10 SaaS opportunities across all niches.

Usage:
    python scripts/batch_analysis.py
"""

from __future__ import annotations

import os
import sys
import time
from collections import Counter

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.ai_extractor import extract_opportunities
from app.clusterer import cluster_opportunities
from app.scorer import score_opportunity
from app.sources.firecrawl_source import FirecrawlSource

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

KEYWORDS = [
    "warehouse",
    "accounting",
    "restaurant",
    "solar installer",
    "electrician",
    "dentist",
    "property management",
    "coffee shop",
    "construction",
    "gym owner",
]

POSTS_PER_KEYWORD = 10   # keep low so extraction stays under ~10s per niche


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def pain_intensity(scored: list[dict]) -> float:
    """Average severity_score weighted by opportunity_score."""
    if not scored:
        return 0.0
    total_weight = sum(o.get("opportunity_score", 1) for o in scored)
    if total_weight == 0:
        return 0.0
    weighted = sum(
        o.get("severity_score", 0) * o.get("opportunity_score", 1)
        for o in scored
    )
    return round(weighted / total_weight, 2)


def opportunity_density(scored: list[dict], clusters: list[dict]) -> float:
    """Number of distinct clusters × average opportunity score.

    More clusters with higher scores = richer niche.
    """
    if not clusters:
        return 0.0
    avg_score = sum(o.get("opportunity_score", 0) for o in scored) / len(scored)
    return round(len(clusters) * avg_score, 2)


def buyer_clarity(scored: list[dict]) -> float:
    """Concentration of the dominant buyer persona (0–1).

    1.0 = every post points to the same buyer type.
    0.0 = completely fragmented across many buyer types.
    Penalises generic labels like 'Business Owner'.
    """
    buyers = [o.get("buyer_type", "") for o in scored if o.get("buyer_type")]
    if not buyers:
        return 0.0
    counter = Counter(buyers)
    top_buyer, top_count = counter.most_common(1)[0]
    score = top_count / len(buyers)
    # Small penalty for overly generic labels
    if top_buyer.lower() in ("business owner", "general", "unknown", ""):
        score *= 0.6
    return round(score, 2)


def composite_score(pi: float, od: float, bc: float) -> float:
    """Weighted composite niche score (0–10 scale)."""
    pi_norm = pi / 10.0          # severity is already 1-10
    od_norm = min(od / 50.0, 1)  # cap at 50 to normalise
    return round((pi_norm * 0.40 + od_norm * 0.35 + bc * 0.25) * 10, 2)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def analyse_keyword(
    src: FirecrawlSource,
    keyword: str,
    idx: int,
    total: int,
) -> dict:
    print(f"\n[{idx}/{total}] {keyword!r}")

    t0 = time.perf_counter()

    # Crawl
    posts = src.search(keyword, limit=POSTS_PER_KEYWORD)
    t_crawl = time.perf_counter() - t0
    print(f"  crawled  {len(posts)} pages in {t_crawl:.1f}s")

    # Extract + score (concurrent per-keyword)
    t1 = time.perf_counter()
    opportunities = extract_opportunities(posts, max_workers=POSTS_PER_KEYWORD)
    scored = sorted(
        [score_opportunity(opp, post) for opp, post in zip(opportunities, posts)],
        key=lambda o: o["opportunity_score"],
        reverse=True,
    )
    t_extract = time.perf_counter() - t1
    print(f"  extracted {len(scored)} opps in {t_extract:.1f}s")

    # Cluster
    t2 = time.perf_counter()
    clusters = cluster_opportunities(scored)
    t_cluster = time.perf_counter() - t2
    print(f"  clustered into {len(clusters)} themes in {t_cluster:.1f}s")

    pi  = pain_intensity(scored)
    od  = opportunity_density(scored, clusters)
    bc  = buyer_clarity(scored)
    cs  = composite_score(pi, od, bc)
    avg = round(sum(o.get("opportunity_score", 0) for o in scored) / len(scored), 2) if scored else 0

    print(f"  pain={pi}  density={od}  clarity={bc}  composite={cs}")

    return {
        "keyword": keyword,
        "posts": len(posts),
        "opportunities": scored,
        "clusters": clusters,
        "metrics": {
            "pain_intensity": pi,
            "opportunity_density": od,
            "buyer_clarity": bc,
            "composite_score": cs,
            "avg_opportunity_score": avg,
        },
        "elapsed": round(time.perf_counter() - t0, 1),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(results: list[dict]) -> None:
    SEP = "=" * 72

    print(f"\n\n{SEP}")
    print("  PAINMINER — BATCH MARKET ANALYSIS REPORT")
    print(SEP)

    # ── Niche ranking ────────────────────────────────────────────────────
    ranked = sorted(results, key=lambda r: r["metrics"]["composite_score"], reverse=True)

    print("\n── NICHE RANKING (composite = pain×0.4 + density×0.35 + clarity×0.25)\n")
    header = f"{'Rank':<5} {'Niche':<22} {'Pain':>5} {'Density':>8} {'Clarity':>8} {'Composite':>10} {'Avg Score':>10}"
    print(header)
    print("-" * len(header))
    for rank, r in enumerate(ranked, 1):
        m = r["metrics"]
        print(
            f"{rank:<5} {r['keyword']:<22} {m['pain_intensity']:>5} "
            f"{m['opportunity_density']:>8} {m['buyer_clarity']:>8} "
            f"{m['composite_score']:>10} {m['avg_opportunity_score']:>10}"
        )

    # ── Top 10 SaaS opportunities ────────────────────────────────────────
    print(f"\n{'─'*72}")
    print("  TOP 10 SaaS OPPORTUNITIES ACROSS ALL NICHES")
    print(f"{'─'*72}\n")

    all_opps = []
    for r in results:
        for opp in r["opportunities"]:
            all_opps.append({**opp, "_niche": r["keyword"]})

    top10 = sorted(all_opps, key=lambda o: o["opportunity_score"], reverse=True)[:10]

    for rank, opp in enumerate(top10, 1):
        print(f"#{rank:02d}  [{opp['_niche']}]  score={opp['opportunity_score']}/10")
        print(f"     Problem  : {opp['problem']}")
        print(f"     Buyer    : {opp['buyer_type']}")
        print(f"     Industry : {opp['industry']}")
        why = opp.get("why_this_is_a_problem", "")
        if why:
            print(f"     Why      : {why[:110]}")
        print()

    # ── Cluster summary per niche ─────────────────────────────────────────
    print(f"{'─'*72}")
    print("  CLUSTER SUMMARY BY NICHE\n")
    for r in ranked:
        print(f"  {r['keyword'].upper()}  (composite={r['metrics']['composite_score']})")
        for c in r["clusters"][:4]:
            print(
                f"    • {c['cluster_name']:<35} "
                f"mentions={c['mention_count']}  "
                f"avg_score={c['average_opportunity_score']}"
            )
        print()

    # ── Timing ───────────────────────────────────────────────────────────
    total_elapsed = sum(r["elapsed"] for r in results)
    print(f"{'─'*72}")
    print(f"  Total analysis time : {total_elapsed:.0f}s  "
          f"({total_elapsed/len(results):.0f}s avg per niche)")
    print(SEP)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    src = FirecrawlSource()
    total = len(KEYWORDS)
    t_start = time.perf_counter()

    results = []
    for idx, kw in enumerate(KEYWORDS, 1):
        result = analyse_keyword(src, kw, idx, total)
        results.append(result)

    print_report(results)
    print(f"\nWall-clock total: {time.perf_counter() - t_start:.0f}s")
