"""
schemas.py  — QSR Signal Extraction Engine
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator
from typing_extensions import TypedDict


class ICPType(str, Enum):
    RESTAURANT = "restaurant"
    SAAS = "saas"
    UNKNOWN = "unknown"


class ATSPlatform(str, Enum):
    PARADOX    = "paradox"
    HARRI      = "harri"
    SNAGAJOB   = "snagajob"
    WORKDAY    = "workday"
    GREENHOUSE = "greenhouse"
    LEVER      = "lever"
    ASHBY      = "ashby"
    UNKNOWN    = "unknown"


class HiringSignalStrength(str, Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


class Lead(BaseModel):
    company_name:   str = Field(..., min_length=1)
    company_domain: str = Field(..., min_length=3)

    @field_validator("company_domain")
    @classmethod
    def _strip_scheme(cls, v: str) -> str:
        for prefix in ("https://", "http://", "www."):
            if v.startswith(prefix):
                v = v[len(prefix):]
        return v.rstrip("/").lower()


class StoreExpansionResult(BaseModel):
    source_url:         str       = ""
    store_count_today:  int       = 0
    store_count_delta:  int       = 0
    new_store_ids:      list[str] = Field(default_factory=list)
    sample_locations:   list[str] = Field(default_factory=list)
    expansion_detected: bool      = False
    from_cache:         bool      = False
    error:              str       = ""


class JobPosting(BaseModel):
    title:     str = ""
    store_ref: str = ""
    job_hash:  str = ""


class ATSHiringResult(BaseModel):
    ats_platform:           ATSPlatform          = ATSPlatform.UNKNOWN
    ats_url:                str                  = ""
    open_requisitions:      int                  = 0
    jobs:                   list[JobPosting]     = Field(default_factory=list)
    churn_anomalies:        int                  = 0
    critical_roles:         list[str]            = Field(default_factory=list)
    hiring_signal_strength: HiringSignalStrength = HiringSignalStrength.LOW
    from_cache:             bool                 = False
    error:                  str                  = ""


class OSINTArticle(BaseModel):
    headline:    str = ""
    publication: str = ""
    pub_date:    str = ""
    url:         str = ""


class OSINTResult(BaseModel):
    query_used:             str               = ""
    articles_found:         int               = 0
    articles:               list[OSINTArticle] = Field(default_factory=list)
    consolidation_detected: bool              = False
    top_headline:           str               = ""
    top_publication:        str               = ""
    top_date:               str               = ""
    from_cache:             bool              = False
    error:                  str               = ""


class RawSignals(BaseModel):
    store: StoreExpansionResult = Field(default_factory=StoreExpansionResult)
    ats:   ATSHiringResult      = Field(default_factory=ATSHiringResult)
    osint: OSINTResult          = Field(default_factory=OSINTResult)


class CSVRow(BaseModel):
    run_date:       str = Field(default_factory=lambda: date.today().isoformat())
    company_name:   str = ""
    company_domain: str = ""
    # Signal 1
    source_store_locator: str  = ""
    store_count_today:    int  = 0
    store_count_delta:    int  = 0
    new_store_ids:        str  = ""
    expansion_detected:   bool = False
    expansion_summary:    str  = ""
    # Signal 2
    source_ats_platform:    str  = ""
    source_ats_url:         str  = ""
    open_requisitions:      int  = 0
    churn_anomalies:        int  = 0
    critical_roles:         str  = ""
    hiring_signal_strength: str  = ""
    # Signal 3
    source_osint_query:     str  = ""
    osint_articles_found:   int  = 0
    osint_headline:         str  = ""
    osint_publication:      str  = ""
    osint_date:             str  = ""
    consolidation_detected: bool = False
    # Synthesis
    llm_confidence_score: float = 0.0
    outreach_context:     str   = ""
    cache_hits:           str   = ""
    primary_signal:       str   = ""


class GraphState(TypedDict, total=False):
    run_id:      str
    lead:        Lead
    raw_signals: RawSignals
    csv_row:     CSVRow | None
    step_logs:   list[str]
    error_count: int
    fatal_error: str
