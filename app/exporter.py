"""CSV Export Engine.

Converts PainMiner pipeline results into CSV files ready for import into
Clay, Apollo, HubSpot, Instantly, or any spreadsheet / CRM.

Four export functions:
  export_opportunities_csv()   → opportunities.csv
  export_companies_csv()       → companies.csv
  export_decision_makers_csv() → decision_makers.csv
  export_saas_ideas_csv()      → saas_ideas.csv

All files are written to  exports/<keyword-slug>/  in the project root.
Each run creates a fresh directory (existing files are overwritten).
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from app.models.decision_maker import CompanyDecisionMakers

# Project root is one level up from this file
_PROJECT_ROOT = Path(__file__).parent.parent
_EXPORTS_DIR = _PROJECT_ROOT / "exports"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(text: str) -> str:
    """Convert text to a safe directory/file name slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _run_dir(keyword: str) -> Path:
    """Return (and create) an export directory for this keyword."""
    d = _EXPORTS_DIR / _slug(keyword)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> int:
    """Write *rows* to a CSV at *path*. Returns the number of data rows written."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _sample(rows: list[dict], n: int = 3) -> list[dict]:
    return rows[:n]


# ---------------------------------------------------------------------------
# Export functions
# ---------------------------------------------------------------------------

def export_opportunities_csv(
    keyword: str,
    scored_opportunities: list[dict],
    clusters: list[dict] | None = None,
    buyer_profiles: list | None = None,
) -> tuple[Path, int, list[dict]]:
    """Export per-post opportunities to opportunities.csv.

    Columns: keyword, cluster, problem, buyer_role, severity_score,
             urgency_score, opportunity_score, outreach_angle

    Returns (file_path, row_count, sample_rows).
    """
    # Build a quick lookup: buyer_type → cluster_name from cluster data
    buyer_to_cluster: dict[str, str] = {}
    if clusters:
        for c in clusters:
            for bt in c.get("buyer_types", []):
                buyer_to_cluster[bt] = c.get("cluster_name", "")

    # Build outreach-angle lookup: cluster_name → first angle
    cluster_to_angle: dict[str, str] = {}
    if buyer_profiles:
        for bp in buyer_profiles:
            cn = getattr(bp, "cluster_name", "")
            angles = getattr(bp, "outreach_angles", [])
            if cn and angles:
                cluster_to_angle[cn] = angles[0]

    rows: list[dict] = []
    for opp in scored_opportunities:
        buyer_type = opp.get("buyer_type", "")
        cluster_name = buyer_to_cluster.get(buyer_type, "")
        why = opp.get("why_this_is_a_problem", "")
        angle = (
            cluster_to_angle.get(cluster_name)
            or why
            or f"Resolve {opp.get('problem', '').lower()} for {buyer_type}"
        )
        rows.append(
            {
                "keyword": keyword,
                "cluster": cluster_name,
                "problem": opp.get("problem", ""),
                "buyer_role": buyer_type,
                "severity_score": opp.get("severity_score", ""),
                "urgency_score": opp.get("urgency_score", ""),
                "opportunity_score": opp.get("opportunity_score", ""),
                "outreach_angle": angle[:200],
            }
        )

    path = _run_dir(keyword) / "opportunities.csv"
    count = _write_csv(
        path,
        ["keyword", "cluster", "problem", "buyer_role",
         "severity_score", "urgency_score", "opportunity_score", "outreach_angle"],
        rows,
    )
    return path, count, _sample(rows)


def export_companies_csv(
    keyword: str,
    companies: list[dict],
) -> tuple[Path, int, list[dict]]:
    """Export discovered companies to companies.csv.

    Columns: cluster, company_name, website, location, employee_range, why_match

    Returns (file_path, row_count, sample_rows).
    """
    rows = [
        {
            "cluster": c.get("cluster", ""),
            "company_name": c.get("name", ""),
            "website": c.get("website", ""),
            "location": c.get("location", ""),
            "employee_range": c.get("employee_range", ""),
            "why_match": c.get("why_match", "")[:200],
        }
        for c in companies
    ]

    path = _run_dir(keyword) / "companies.csv"
    count = _write_csv(
        path,
        ["cluster", "company_name", "website", "location", "employee_range", "why_match"],
        rows,
    )
    return path, count, _sample(rows)


def export_decision_makers_csv(
    keyword: str,
    dm_results: list[CompanyDecisionMakers],
) -> tuple[Path, int, list[dict]]:
    """Export decision-maker role profiles to decision_makers.csv.

    Columns: company, website, cluster, title, seniority, department,
             buying_power, linkedin_query, reason

    Returns (file_path, row_count, sample_rows).
    """
    rows: list[dict] = []
    for result in dm_results:
        for dm in result.decision_makers:
            rows.append(
                {
                    "company": result.company_name,
                    "website": result.website,
                    "cluster": result.cluster,
                    "title": dm.title,
                    "seniority": dm.seniority,
                    "department": dm.department,
                    "buying_power": dm.buying_power,
                    "linkedin_query": dm.linkedin_query,
                    "reason": dm.reason[:200],
                }
            )

    path = _run_dir(keyword) / "decision_makers.csv"
    count = _write_csv(
        path,
        ["company", "website", "cluster", "title", "seniority",
         "department", "buying_power", "linkedin_query", "reason"],
        rows,
    )
    return path, count, _sample(rows)


def export_saas_ideas_csv(
    keyword: str,
    saas_ideas: list[dict],
) -> tuple[Path, int, list[dict]]:
    """Export SaaS opportunity ideas to saas_ideas.csv.

    Columns: rank, saas_name, one_line_pitch, ideal_customer,
             pricing_model, rank_score, source_cluster

    Returns (file_path, row_count, sample_rows).
    """
    rows = [
        {
            "rank": i + 1,
            "saas_name": idea.get("saas_name", ""),
            "one_line_pitch": idea.get("one_line_pitch", ""),
            "ideal_customer": idea.get("ideal_customer", ""),
            "pricing_model": idea.get("pricing_model", ""),
            "rank_score": idea.get("rank_score", ""),
            "source_cluster": idea.get("source_cluster", ""),
        }
        for i, idea in enumerate(saas_ideas)
    ]

    path = _run_dir(keyword) / "saas_ideas.csv"
    count = _write_csv(
        path,
        ["rank", "saas_name", "one_line_pitch", "ideal_customer",
         "pricing_model", "rank_score", "source_cluster"],
        rows,
    )
    return path, count, _sample(rows)


# ---------------------------------------------------------------------------
# Export manifest
# ---------------------------------------------------------------------------

def build_manifest(keyword: str, results: dict) -> dict:
    """Return a summary manifest of all exported files."""
    return {
        "keyword": keyword,
        "export_directory": str(_run_dir(keyword)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "csv_files": results.get("csv_files", []),
        "row_counts": results.get("row_counts", {}),
        "download_paths": [str(p) for p in results.get("download_paths", [])],
    }
