"""Batch market analysis across multiple niches.

Runs the full PainMiner pipeline (expand → crawl → extract → score → cluster)
for each keyword, aggregates results, and ranks niches by:
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

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

from app.ai_extractor import extract_opportunities
from app.clusterer import cluster_opportunities
from app.expander import expand_keyword
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

LIMIT_PER_TERM = 8    # results per expanded search term
MAX_POSTS = 30        # hard cap on unique posts per niche


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def pain_intensity(scored: list[dict]) -> float:
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
    if not clusters:
        return 0.0
    avg_score = sum(o.get("opportunity_score", 0) for o in scored) / len(scored)
    return round(len(clusters) * avg_score, 2)


def buyer_clarity(scored: list[dict]) -> float:
    buyers = [o.get("buyer_type", "") for o in scored if o.get("buyer_type")]
    if not buyers:
        return 0.0
    counter = Counter(buyers)
    top_buyer, top_count = counter.most_common(1)[0]
    score = top_count / len(buyers)
    if top_buyer.lower() in ("business owner", "general", "unknown", ""):
        score *= 0.6
    return round(score, 2)


def composite_score(pi: float, od: float, bc: float) -> float:
    pi_norm = pi / 10.0
    od_norm = min(od / 50.0, 1)
    return round((pi_norm * 0.40 + od_norm * 0.35 + bc * 0.25) * 10, 2)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def analyse_keyword(src: FirecrawlSource, keyword: str, idx: int, total: int) -> dict:
    print(f"\n[{idx}/{total}] {keyword!r}")
    t0 = time.perf_counter()

    # Expand
    t_exp = time.perf_counter()
    expanded = expand_keyword(keyword)
    print(f"  expanded  → {expanded}")
    t_expand = time.perf_counter() - t_exp

    # Crawl (concurrent multi-term search)
    t_cr = time.perf_counter()
    posts = src.search_expanded(keyword, expanded, limit_per_term=LIMIT_PER_TERM, max_total=MAX_POSTS)
    t_crawl = time.perf_counter() - t_cr
    print(f"  crawled   {len(posts)} unique pages in {t_crawl:.1f}s")

    # Extract + score
    t_ex = time.perf_counter()
    opportunities = extract_opportunities(posts, max_workers=min(len(posts), 15))
    scored = sorted(
        [score_opportunity(opp, post) for opp, post in zip(opportunities, posts)],
        key=lambda o: o["opportunity_score"],
        reverse=True,
    )
    t_extract = time.perf_counter() - t_ex
    print(f"  extracted {len(scored)} opps in {t_extract:.1f}s")

    # Cluster
    t_cl = time.perf_counter()
    clusters = cluster_opportunities(scored)
    t_cluster = time.perf_counter() - t_cl
    print(f"  clustered into {len(clusters)} themes in {t_cluster:.1f}s")

    pi = pain_intensity(scored)
    od = opportunity_density(scored, clusters)
    bc = buyer_clarity(scored)
    cs = composite_score(pi, od, bc)
    avg = round(sum(o.get("opportunity_score", 0) for o in scored) / len(scored), 2) if scored else 0

    print(f"  pain={pi}  density={od}  clarity={bc}  composite={cs}")

    return {
        "keyword": keyword,
        "expanded_terms": expanded,
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
    print("  PAINMINER — BATCH MARKET ANALYSIS REPORT  (expanded search)")
    print(SEP)

    ranked = sorted(results, key=lambda r: r["metrics"]["composite_score"], reverse=True)

    print("\n── NICHE RANKING (composite = pain×0.4 + density×0.35 + clarity×0.25)\n")
    header = (
        f"{'Rank':<5} {'Niche':<22} {'Posts':>5} {'Pain':>6} "
        f"{'Density':>8} {'Clarity':>8} {'Composite':>10} {'AvgScore':>9}"
    )
    print(header)
    print("-" * len(header))
    for rank, r in enumerate(ranked, 1):
        m = r["metrics"]
        print(
            f"{rank:<5} {r['keyword']:<22} {r['posts']:>5} {m['pain_intensity']:>6} "
            f"{m['opportunity_density']:>8} {m['buyer_clarity']:>8} "
            f"{m['composite_score']:>10} {m['avg_opportunity_score']:>9}"
        )

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

    print(f"{'─'*72}")
    print("  CLUSTER SUMMARY BY NICHE (top clusters per niche)\n")
    for r in ranked:
        m = r["metrics"]
        print(f"  {r['keyword'].upper()}  (composite={m['composite_score']}  posts={r['posts']})")
        print(f"  Expanded: {r['expanded_terms']}")
        for c in r["clusters"][:4]:
            print(
                f"    • {c['cluster_name']:<38} "
                f"mentions={c['mention_count']}  "
                f"avg={c['average_opportunity_score']}"
            )
        print()

    total_elapsed = sum(r["elapsed"] for r in results)
    print(f"{'─'*72}")
    print(f"  Total analysis time : {total_elapsed:.0f}s  ({total_elapsed/len(results):.0f}s avg/niche)")
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
