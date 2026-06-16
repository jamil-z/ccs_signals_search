"""
csv_writer.py — Writes results to two CSV files.

CSV 1: detailed_search_log_<timestamp>.csv
  One row per search step. Contains: company, phase, query, backend,
  raw HTML snippet, extracted data, success/error.
  Purpose: Full audit trail — you can see exactly what was searched
  and what the page returned.

CSV 2: company_summary_<timestamp>.csv
  One row per company. Contains the full CompanyProfile + analyst summary.
  Purpose: The final deliverable — the ICP intelligence table.
  References the detailed log file for drill-down.
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
    "company_name",
    "official_url",
    "linkedin_url",
    "industry",
    "description",
    "company_size",
    "funding_stage",
    "total_funding",
    "stock_ticker",
    "market_cap",
    "customer_count_estimate",
    "growth_signals",
    "active_job_count",
    "active_job_titles",
    "expansion_news",
    "analyst_summary",
    "processing_errors",
    "detail_log_ref",
    "processed_at",
]


def _fmt(value: str, default: str = "N/A") -> str:
    """
    Return a human-readable sentinel for empty/None CSV cells.

    Using a visible placeholder instead of a blank cell prevents spreadsheet
    tools from misinterpreting an absent value as a data-entry error.
    The in-memory schema retains empty-string semantics; this conversion only
    happens at the serialization boundary.
    """
    return value if value else default


def _profile_to_row(profile: CompanyProfile, detail_filename: str) -> dict:
    return {
        "company_name":           profile.company_name,
        "official_url":           _fmt(profile.official_url),
        "linkedin_url":           _fmt(profile.linkedin_url),
        "industry":               _fmt(profile.industry),
        "description":            _fmt(profile.description),
        "company_size":           profile.company_size.value,
        "funding_stage":          profile.funding_stage.value,
        # Financial fields: "Private" signals "not publicly traded", not "data missing"
        "stock_ticker":           _fmt(profile.stock_ticker, default="Private"),
        "market_cap":             _fmt(profile.market_cap,   default="Private"),
        "total_funding":          _fmt(profile.total_funding),
        "customer_count_estimate": _fmt(profile.customer_count_estimate),
        "growth_signals":         ", ".join(s.value for s in profile.growth_signals) or "N/A",
        "active_job_count":       len(profile.active_job_postings),
        "active_job_titles":      " | ".join(profile.active_job_postings[:20]) or "N/A",
        "expansion_news":         " | ".join(profile.expansion_news[:5]) or "N/A",
        "analyst_summary":        _fmt(profile.analyst_summary),
        "processing_errors":      " | ".join(profile.processing_errors) or "None",
        "detail_log_ref":         detail_filename,
        "processed_at":           profile.processed_at.isoformat(),
    }


# ── Main writer class ─────────────────────────────────────────────────────────

class ResultsWriter:
    """
    Writes and appends results to both CSV files.
    Call write_company() after each company finishes — it appends to both files
    incrementally (no need to wait for all companies to finish).
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        ts = _ts()
        self.detail_path = output_dir / f"detailed_search_log_{ts}.csv"
        self.summary_path = output_dir / f"company_summary_{ts}.csv"

        # Write headers
        self._write_headers()
        logger.info(f"📁 Detail CSV:  {self.detail_path}")
        logger.info(f"📁 Summary CSV: {self.summary_path}")

    def _write_headers(self):
        with open(self.detail_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=DETAIL_HEADERS)
            writer.writeheader()

        with open(self.summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADERS)
            writer.writeheader()

    def write_company(self, state: AgentState):
        """
        Append one company's results to both CSV files.
        Thread-safe as long as each company writes sequentially
        (concurrent writes to same file require a lock — handled in main.py).
        """
        # ── Write detail rows ─────────────────────────────────────────────────
        with open(self.detail_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=DETAIL_HEADERS)
            for log in state.search_logs:
                writer.writerow(_log_to_row(log))

        # ── Write summary row ─────────────────────────────────────────────────
        with open(self.summary_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SUMMARY_HEADERS)
            writer.writerow(
                _profile_to_row(state.profile, self.detail_path.name)
            )

        logger.info(
            f"💾 Written: {state.company_name} "
            f"({len(state.search_logs)} search steps)"
        )

    @property
    def detail_filename(self) -> str:
        return self.detail_path.name

    @property
    def summary_filename(self) -> str:
        return self.summary_path.name
