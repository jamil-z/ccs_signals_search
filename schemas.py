"""
schemas.py — Pydantic models for the Automotive & Transportation Design ICP pipeline.

These are the canonical data structures that flow through LangGraph.
Every node reads/writes to these models; nothing travels as raw dicts.

Target niche: Automotive and Transportation Design Companies in Michigan.
New signals (Phase 4/5) cover: internal creative teams, creative support needs,
tech stack (Adobe/Alias/Autodesk/Unity/Unreal), enterprise software budget,
hiring for creative/design roles, upskilling/workforce development, creative
leadership presence, and local Michigan involvement (grants, MEDC, alliances).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


# ── Enums ─────────────────────────────────────────────────────────────────────

class CompanySize(str, Enum):
    STARTUP = "startup"          # < 50 employees or seed/Series-A
    SMB = "smb"                  # 50-500 employees
    MID_MARKET = "mid_market"    # 500-5000 employees
    ENTERPRISE = "enterprise"    # 5000+ or publicly traded
    UNKNOWN = "unknown"


class FundingStage(str, Enum):
    BOOTSTRAPPED = "bootstrapped"
    SEED = "seed"
    SERIES_A = "series_a"
    SERIES_B = "series_b"
    SERIES_C_PLUS = "series_c_plus"
    PUBLIC = "public"
    UNKNOWN = "unknown"


class GrowthSignal(str, Enum):
    EXPANDING = "expanding"       # new offices, new markets
    HIRING = "hiring"             # active job postings
    FUNDED = "funded"             # recent funding round
    CONTRACTING = "contracting"   # layoffs / office closures
    STABLE = "stable"             # no strong signals
    UNKNOWN = "unknown"


# ── Per-search-step log entry (goes to detailed CSV) ─────────────────────────

class SearchStepLog(BaseModel):
    """One atomic search action recorded for the detailed CSV."""
    company_name: str
    phase: str                     # e.g. "1.1_official_url"
    query_or_url: str              # the exact query or URL used
    backend_used: str              # "serper" | "playwright"
    raw_html_snippet: str = ""     # first ~500 chars of page HTML / result text
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    error_message: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ── Automotive Design Signals (Phase 4 — the core new signals) ───────────────

class AutomotiveDesignSignals(BaseModel):
    """
    The 8 hyper-specific signals targeting Automotive & Transportation Design
    companies in Michigan as prospective clients for creative services or
    3D/Design enterprise software.
    """

    # Signal 1: Internal Creative/Design Team
    has_internal_creative_team: bool = False
    internal_creative_team_evidence: str = ""
    # e.g. "Has a 40-person CMF and Exterior Design studio in Dearborn"

    # Signal 2: Creative/Design Support Need (external agency or contractor need)
    requires_creative_support: bool = False
    creative_support_evidence: str = ""
    # e.g. "Posted RFP for industrial design consultancy Q1 2025"

    # Signal 3: Creative Tech Stack (Adobe, Alias, Autodesk, Unity, Unreal, etc.)
    detected_creative_tools: list[str] = Field(default_factory=list)
    # e.g. ["Autodesk Alias", "Adobe Creative Suite", "Unreal Engine"]
    tech_stack_evidence: str = ""

    # Signal 4: Enterprise Software Budget / License Purchase Signals
    has_enterprise_software_budget: bool = False
    budget_evidence: str = ""
    # e.g. "Job posting mentions 'Autodesk enterprise license management'"

    # Signal 5: Hiring for Creative/Design/Engineering Roles
    is_hiring_creative_roles: bool = False
    creative_job_titles: list[str] = Field(default_factory=list)
    # e.g. ["CMF Designer", "Transportation Designer", "UX Designer – Mobility"]
    hiring_evidence: str = ""

    # Signal 6: Upskilling / Workforce Development / Professional Development
    offers_upskilling: bool = False
    upskilling_programs: list[str] = Field(default_factory=list)
    # e.g. ["Ford College Graduate Program", "MEDC Workforce Training Grant"]
    upskilling_evidence: str = ""

    # Signal 7: Leadership Position in Creative/Design
    has_creative_leadership: bool = False
    creative_leadership_titles: list[str] = Field(default_factory=list)
    # e.g. ["Chief Design Officer", "VP of Transportation Design"]
    leadership_evidence: str = ""

    # Signal 8: Local Michigan Involvement (grants, MEDC, community college alliances)
    has_michigan_local_involvement: bool = False
    michigan_involvement_details: list[str] = Field(default_factory=list)
    # e.g. ["MEDC grant recipient 2024", "Partner: Macomb Community College"]
    michigan_involvement_evidence: str = ""


# ── Company profile (goes to summary CSV) ────────────────────────────────────

class CompanyProfile(BaseModel):
    """Aggregated intelligence for a single Automotive/Transportation Design company."""
    company_name: str

    # Phase 1 — Identity
    official_url: str = ""
    linkedin_url: str = ""
    industry: str = ""
    description: str = ""

    # Phase 2 — Scale & Finance
    stock_ticker: str = ""
    market_cap: str = ""
    funding_stage: FundingStage = FundingStage.UNKNOWN
    total_funding: str = ""
    company_size: CompanySize = CompanySize.UNKNOWN
    employee_count_estimate: str = ""
    customer_count_estimate: str = ""

    # Phase 3 — Growth signals (general)
    active_job_postings: list[str] = Field(default_factory=list)
    expansion_news: list[str] = Field(default_factory=list)
    growth_signals: list[GrowthSignal] = Field(default_factory=list)

    # Phase 4 & 5 — Automotive Design Signals (the 8 new ICP signals)
    automotive_signals: AutomotiveDesignSignals = Field(
        default_factory=AutomotiveDesignSignals
    )

    # Synthesis outputs
    icp_score: int = 0              # 1-10: fit score as creative services client
    recommended_action: str = ""    # "reach_out" | "monitor" | "skip" | "research_more"
    key_buying_signals: list[str] = Field(default_factory=list)

    # Meta
    search_steps_ref: list[str] = Field(default_factory=list)
    analyst_summary: str = ""
    processed_at: datetime = Field(default_factory=datetime.utcnow)
    processing_errors: list[str] = Field(default_factory=list)


# ── LangGraph State ───────────────────────────────────────────────────────────

class AgentState(BaseModel):
    """
    The state that travels through the LangGraph pipeline for ONE company.

    - messages: used by the LLM tool-calling loop (add_messages reducer accumulates them)
    - company_name: the single input to the pipeline
    - profile: built up across Phase 1, 2, 3, 4, 5
    - search_logs: one entry per search action, for the detailed CSV
    """
    messages: Annotated[list[BaseMessage], add_messages] = Field(default_factory=list)
    company_name: str = ""
    profile: CompanyProfile = Field(default_factory=lambda: CompanyProfile(company_name=""))
    search_logs: list[SearchStepLog] = Field(default_factory=list)
    current_phase: str = "1.1"
    is_done: bool = False
