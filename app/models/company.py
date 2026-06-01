"""Pydantic models for company discovery."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Company(BaseModel):
    """A real company discovered as a potential sales target."""

    name: str = Field("", description="Company name")
    website: str = Field("", description="Root domain URL, e.g. https://appfolio.com")
    location: str = Field("", description="HQ location if discoverable, else empty")
    description: str = Field("", description="Short description of what the company does")
    employee_range: str = Field("", description="Estimated headcount band, e.g. '50-200'")
    why_match: str = Field("", description="Why this company matches the buyer profile")


class ClusterCompanies(BaseModel):
    """All companies discovered for a single market cluster."""

    cluster: str = Field("", description="Source cluster name")
    companies: list[Company] = Field(default_factory=list)
