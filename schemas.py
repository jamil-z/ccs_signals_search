"""
schemas.py — Pydantic models for the entire ICP pipeline.

These are the canonical data structures that flow through LangGraph.
Every node reads/writes to these models; nothing travels as raw dicts.
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


# ── Company profile (goes to summary CSV) ────────────────────────────────────

class CompanyProfile(BaseModel):
    """Aggregated intelligence for a single company."""
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

    # Phase 3 — Growth signals
    active_job_postings: list[str] = Field(default_factory=list)
    expansion_news: list[str] = Field(default_factory=list)
    growth_signals: list[GrowthSignal] = Field(default_factory=list)

    # Meta
    search_steps_ref: list[str] = Field(default_factory=list)  # Phase IDs for cross-ref
    analyst_summary: str = ""       # LLM-generated conclusion
    processed_at: datetime = Field(default_factory=datetime.utcnow)
    processing_errors: list[str] = Field(default_factory=list)


# ── LangGraph State ───────────────────────────────────────────────────────────

class AgentState(BaseModel):
    """
    The state that travels through the LangGraph pipeline for ONE company.

    - messages: used by the LLM tool-calling loop (add_messages reducer accumulates them)
    - company_name: the single input to the pipeline
    - profile: built up across Phase 1, 2, 3
    - search_logs: one entry per search action, for the detailed CSV
    """
    messages: Annotated[list[BaseMessage], add_messages] = Field(default_factory=list)
    company_name: str = ""
    profile: CompanyProfile = Field(default_factory=lambda: CompanyProfile(company_name=""))
    search_logs: list[SearchStepLog] = Field(default_factory=list)
    current_phase: str = "1.1"
    is_done: bool = False
