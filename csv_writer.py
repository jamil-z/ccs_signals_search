"""
csv_writer.py — Writes results to two CSV files.

CSV 1: detailed_search_log_<timestamp>.csv
  One row per search step. Full audit trail of every query made.

CSV 2: company_summary_<timestamp>.csv
  One row per company. Contains the full CompanyProfile + all 8 automotive
  design signal fields + analyst summary and ICP score.
  This is the final deliverable for Michigan automotive/transportation design ICP.

Signal columns (8 new fields, one column per signal):
  sig1_has_internal_creative_team     — bool
  sig1_internal_creative_team_evidence
  sig2_requires_creative_support      — bool
  sig2_creative_support_evidence
  sig3_detected_creative_tools        — pipe-separated list
  sig3_tech_stack_evidence
  sig4_has_enterprise_software_budget — bool
  sig4_budget_evidence
  sig5_is_hiring_creative_roles       — bool
  sig5_creative_job_titles            — pipe-separated list
  sig5_hiring_evidence
  sig6_offers_upskilling              — bool
  sig6_upskilling_programs            — pipe-separated list
  sig6_upskilling_evidence
  sig7_has_creative_leadership        — bool
  sig7_creative_leadership_titles     — pipe-separated list
  sig7_leadership_evidence
  sig8_has_michigan_local_involvement — bool
  sig8_michigan_involvement_details   — pipe-separated list
  sig8_michigan_involvement_evidence
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path

from schemas import AgentState, CompanyProfile, SearchStepLog

logger = logging.getLogger(__name__)


def _ts() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M")


# ── Detailed search log CSV ───────────────────────────────────────────────────

DETAIL_HEADERS = [
    "company_name",
    "phase",
    "query_or_url",
    "backend_used",
    "success",
    "error_message",
    "extracted_data_json",
    "raw_html_snippet",
    "timestamp",
]


def _log_to_row(log: SearchStepLog) -> dict:
    return {
        "company_name": log.company_name,
        "phase": log.phase,
        "query_or_url": log.query_or_url,
        "backend_used": log.backend_used,
        "success": str(log.success),
        "error_message": log.error_message,
        "extracted_data_json": json.dumps(log.extracted_data, ensure_ascii=False),
        "raw_html_snippet": log.raw_html_snippet.replace("\n", " ")[:800],
        "timestamp": log.timestamp.isoformat(),
    }


# ── Summary CSV ───────────────────────────────────────────────────────────────

SUMMARY_HEADERS = [
    # ── Identity ──────────────────────────────────────────────────────────────
    "company_name",
    "official_url",
    "linkedin_url",
    "industry",
    "description",
    # ── Scale & Finance ───────────────────────────────────────────────────────
    "company_size",
    "employee_count_estimate",
    "funding_stage",
    "total_funding",
    "stock_ticker",
    "market_cap",
    "customer_count_estimate",
    # ── General Growth Signals ────────────────────────────────────────────────
    "growth_signals",
    "active_job_count",
    "active_job_titles",
    "expansion_news",
    # ── Signal 1: Internal Creative Team ─────────────────────────────────────
    "sig1_has_internal_creative_team",
    "sig1_internal_creative_team_evidence",
    # ── Signal 2: Creative Support Need ──────────────────────────────────────
    "sig2_requires_creative_support",
    "sig2_creative_support_evidence",
    # ── Signal 3: Creative Tech Stack ─────────────────────────────────────────
    "sig3_detected_creative_tools",
    "sig3_tech_stack_evidence",
    # ── Signal 4: Enterprise Software Budget ──────────────────────────────────
    "sig4_has_enterprise_software_budget",
    "sig4_budget_evidence",
    # ── Signal 5: Hiring Creative/Design Roles ────────────────────────────────
    "sig5_is_hiring_creative_roles",
    "sig5_creative_job_titles",
    "sig5_hiring_evidence",
    # ── Signal 6: Upskilling / Workforce Development ──────────────────────────
    "sig6_offers_upskilling",
    "sig6_upskilling_programs",
    "sig6_upskilling_evidence",
    # ── Signal 7: Creative Leadership ─────────────────────────────────────────
    "sig7_has_creative_leadership",
    "sig7_creative_leadership_titles",
    "sig7_leadership_evidence",
    # ── Signal 8: Michigan Local Involvement ──────────────────────────────────
    "sig8_has_michigan_local_involvement",
    "sig8_michigan_involvement_details",
    "sig8_michigan_involvement_evidence",
    # ── Synthesis / ICP Assessment ────────────────────────────────────────────
    "icp_score",
    "recommended_action",
    "key_buying_signals",
    "analyst_summary",
    # ── Meta ──────────────────────────────────────────────────────────────────
    "processing_errors",
    "detail_log_ref",
    "processed_at",
]


def _fmt(value: str, default: str = "N/A") -> str:
    """
    Return a human-readable sentinel for empty/None CSV cells.
    The in-memory schema retains empty-string semantics; this conversion
    only happens at the serialization boundary.
    """
    return value if value else default


def _bool_fmt(value: bool) -> str:
    """Serialize booleans to a human-readable string for spreadsheet clarity."""
    return "Yes" if value else "No"


def _list_fmt(items: list, default: str = "N/A") -> str:
    """Join a list with pipes, defaulting to sentinel when empty."""
    return " | ".join(str(i) for i in items) if items else default


def _profile_to_row(profile: CompanyProfile, detail_filename: str) -> dict:
    sigs = profile.automotive_signals
    return {
        # Identity
        "company_name":             profile.company_name,
        "official_url":             _fmt(profile.official_url),
        "linkedin_url":             _fmt(profile.linkedin_url),
        "industry":                 _fmt(profile.industry),
        "description":              _fmt(profile.description),
        # Scale & Finance
        "company_size":             profile.company_size.value,
        "employee_count_estimate":  _fmt(profile.employee_count_estimate),
        "funding_stage":            profile.funding_stage.value,
        # Financial — "Private" signals not publicly traded, not missing data
        "stock_ticker":             _fmt(profile.stock_ticker, default="Private"),
        "market_cap":               _fmt(profile.market_cap,   default="Private"),
        "total_funding":            _fmt(profile.total_funding),
        "customer_count_estimate":  _fmt(profile.customer_count_estimate),
        # General Growth
        "growth_signals":           _list_fmt([s.value for s in profile.growth_signals]),
        "active_job_count":         len(profile.active_job_postings),
        "active_job_titles":        _list_fmt(profile.active_job_postings[:20]),
        "expansion_news":           _list_fmt(profile.expansion_news[:5]),
        # Signal 1: Internal Creative Team
        "sig1_has_internal_creative_team":     _bool_fmt(sigs.has_internal_creative_team),
        "sig1_internal_creative_team_evidence": _fmt(sigs.internal_creative_team_evidence),
        # Signal 2: Creative Support Need
        "sig2_requires_creative_support":      _bool_fmt(sigs.requires_creative_support),
        "sig2_creative_support_evidence":      _fmt(sigs.creative_support_evidence),
        # Signal 3: Tech Stack
        "sig3_detected_creative_tools":        _list_fmt(sigs.detected_creative_tools),
        "sig3_tech_stack_evidence":            _fmt(sigs.tech_stack_evidence),
        # Signal 4: Enterprise Budget
        "sig4_has_enterprise_software_budget": _bool_fmt(sigs.has_enterprise_software_budget),
        "sig4_budget_evidence":                _fmt(sigs.budget_evidence),
        # Signal 5: Creative Hiring
        "sig5_is_hiring_creative_roles":       _bool_fmt(sigs.is_hiring_creative_roles),
        "sig5_creative_job_titles":            _list_fmt(sigs.creative_job_titles),
        "sig5_hiring_evidence":                _fmt(sigs.hiring_evidence),
        # Signal 6: Upskilling
        "sig6_offers_upskilling":              _bool_fmt(sigs.offers_upskilling),
        "sig6_upskilling_programs":            _list_fmt(sigs.upskilling_programs),
        "sig6_upskilling_evidence":            _fmt(sigs.upskilling_evidence),
        # Signal 7: Creative Leadership
        "sig7_has_creative_leadership":        _bool_fmt(sigs.has_creative_leadership),
        "sig7_creative_leadership_titles":     _list_fmt(sigs.creative_leadership_titles),
        "sig7_leadership_evidence":            _fmt(sigs.leadership_evidence),
        # Signal 8: Michigan Involvement
        "sig8_has_michigan_local_involvement": _bool_fmt(sigs.has_michigan_local_involvement),
        "sig8_michigan_involvement_details":   _list_fmt(sigs.michigan_involvement_details),
        "sig8_michigan_involvement_evidence":  _fmt(sigs.michigan_involvement_evidence),
        # Synthesis
        "icp_score":            profile.icp_score or "N/A",
        "recommended_action":   _fmt(profile.recommended_action),
        "key_buying_signals":   _list_fmt(profile.key_buying_signals),
        "analyst_summary":      _fmt(profile.analyst_summary),
        # Meta
        "processing_errors":    _list_fmt(profile.processing_errors, default="None"),
        "detail_log_ref":       detail_filename,
        "processed_at":         profile.processed_at.isoformat(),
    }


# ── Main writer class ─────────────────────────────────────────────────────────

class ResultsWriter:
    """
    Writes and appends results to both CSV files incrementally.
    Call write_company() after each company finishes — no need to wait
    for all companies to complete before writing begins.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        ts = _ts()
        self.detail_path = output_dir / f"detailed_search_log_{ts}.csv"
        self.summary_path = output_dir / f"company_summary_{ts}.csv"

        self._write_headers()
        logger.info(f"📁 Detail CSV:  {self.detail_path}")
        logger.info(f"📁 Summary CSV: {self.summary_path}")

    def _write_headers(self):
        with open(self.detail_path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=DETAIL_HEADERS).writeheader()

        with open(self.summary_path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=SUMMARY_HEADERS).writeheader()

    def write_company(self, state: AgentState):
        """
        Append one company's results to both CSV files.
        Thread-safe as long as each company writes sequentially
        (concurrent writes to the same file require a lock — handled in main.py).
        """
        with open(self.detail_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=DETAIL_HEADERS)
            for log in state.search_logs:
                writer.writerow(_log_to_row(log))

        with open(self.summary_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADERS)
            writer.writerow(_profile_to_row(state.profile, self.detail_path.name))

        logger.info(
            f"💾 Written: {state.company_name} "
            f"({len(state.search_logs)} search steps, ICP score={state.profile.icp_score})"
        )

    @property
    def detail_filename(self) -> str:
        return self.detail_path.name

    @property
    def summary_filename(self) -> str:
        return self.summary_path.name
