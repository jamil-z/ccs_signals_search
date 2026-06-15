"""
csv_writer.py — Thread-safe CSV writer. Auto-creates header on first run.
"""
from __future__ import annotations
import csv, threading
from pathlib import Path
import structlog
from schemas import CSVRow

logger = structlog.get_logger(__name__)
_lock = threading.Lock()
CSV_COLUMNS: list[str] = list(CSVRow.model_fields.keys())

def ensure_csv_header(csv_path: Path) -> None:
    if not csv_path.exists():
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS).writeheader()
        logger.info("csv.header_written", path=str(csv_path))

def append_row(csv_path: Path, row: CSVRow) -> None:
    ensure_csv_header(csv_path)
    row_dict = row.model_dump()
    for k, v in row_dict.items():
        if isinstance(v, bool):
            row_dict[k] = "TRUE" if v else "FALSE"
    with _lock:
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore").writerow(row_dict)
    logger.info("csv.row_appended", company=row.company_name)

def append_summary_row(summary_path: Path, row: CSVRow) -> None:
    headers = ["company_name", "company_domain", "primary_signal", "open_roles", "store_delta", "confidence", "one_line_summary"]
    one_line = "No active signals found."
    if row.outreach_context:
        parts = row.outreach_context.split(".")
        if parts:
            one_line = parts[0].strip() + "."
    row_dict = {
        "company_name": row.company_name,
        "company_domain": row.company_domain,
        "primary_signal": row.primary_signal,
        "open_roles": row.open_requisitions,
        "store_delta": row.store_count_delta,
        "confidence": f"{row.llm_confidence_score:.2f}",
        "one_line_summary": one_line
    }
    if not summary_path.exists():
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=headers).writeheader()
    with _lock:
        with summary_path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=headers, extrasaction="ignore").writerow(row_dict)
    logger.info("summary.row_appended", company=row.company_name, signal=row.primary_signal)
