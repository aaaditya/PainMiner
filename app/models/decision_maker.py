"""Pydantic models for decision-maker discovery.

No personal data is collected. Names are left empty until explicit lead
enrichment is added. All output represents inferred role profiles, not
real individuals.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DecisionMaker(BaseModel):
    """An inferred decision-maker role at a discovered company."""

    name: str = Field(
        "",
        description="Person name — empty until lead enrichment; do not infer",
    )
    title: str = Field("", description="Job title, e.g. 'Director of Property Management'")
    seniority: str = Field(
        "",
        description="Seniority band: Owner | VP | Director | Manager | Coordinator | IC",
    )
    department: str = Field("", description="Functional department, e.g. 'Operations'")
    buying_power: int = Field(
        0,
        ge=0,
        le=10,
        description=(
            "Buying authority score 0-10: "
            "10=Owner/Founder/VP, 8=Director, 6=Manager, 4=Coordinator, 2=IC"
        ),
    )
    linkedin_query: str = Field(
        "",
        description=(
            'Google search string to find LinkedIn profiles, e.g. '
            'site:linkedin.com/in "Director of PM" "CompanyName"'
        ),
    )
    reason: str = Field(
        "",
        description="Why this role has authority over the relevant buying decision",
    )


class CompanyDecisionMakers(BaseModel):
    """All inferred decision makers for a single discovered company."""

    company_name: str = Field("", description="Company name")
    website: str = Field("", description="Company root domain URL")
    cluster: str = Field("", description="Market cluster this company was found under")
    decision_makers: list[DecisionMaker] = Field(default_factory=list)
