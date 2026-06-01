"""Pydantic models for buyer intelligence data."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BuyerDiscoveryInput(BaseModel):
    """Input cluster data fed into the buyer discovery engine."""

    problem: str = Field(..., description="Core problem description from the cluster")
    buyer_type: str = Field(..., description="Primary buyer persona from extraction")
    industry: str = Field(..., description="Industry vertical")
    cluster_name: str = Field("", description="Human-readable cluster theme name")
    average_opportunity_score: float = Field(
        0.0, ge=0.0, le=10.0, description="Cluster's average opportunity score"
    )


class BuyerProfile(BaseModel):
    """Structured buyer intelligence output for a single cluster."""

    # Source context
    cluster_name: str = Field("", description="Cluster this profile was generated from")
    problem: str = Field("", description="Problem this profile addresses")

    # Company targeting
    company_types: list[str] = Field(
        default_factory=list,
        description="Specific company profiles that have this problem and budget",
    )

    # Role targeting
    buyer_roles: list[str] = Field(
        default_factory=list,
        description="Day-to-day users who feel the pain most acutely",
    )
    decision_makers: list[str] = Field(
        default_factory=list,
        description="Economic buyers and decision-making authority holders",
    )

    # Discovery
    search_keywords: list[str] = Field(
        default_factory=list,
        description="Phrases buyers search when looking for a solution",
    )
    linkedin_search_queries: list[str] = Field(
        default_factory=list,
        description="LinkedIn people-search queries to find qualified prospects",
    )

    # Outreach
    outreach_angles: list[str] = Field(
        default_factory=list,
        description="Pain-focused talking points for cold outreach",
    )
    why_they_would_buy: str = Field(
        "",
        description="1-2 sentence summary of the primary purchase motivation",
    )
