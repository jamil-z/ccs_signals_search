"""
main.py — QSR Signal Extraction Engine entry point.

Usage:
    .venv/bin/python main.py
    .venv/bin/python main.py --leads leads.txt --concurrency 2

leads.txt format (one per line):
    Company Name, domain.com
    Company Name          <- domain inferred as companyname.com
    # comment lines ignored
"""
from __future__ import annotations
import argparse, asyncio, re, sys, uuid
from pathlib import Path
import structlog
from browser_utils import close_browser
from config import configure_stdlib_logging, get_settings
from graph_orchestrator import graph
from schemas import GraphState, Lead

configure_stdlib_logging()
Path("outputs").mkdir(parents=True, exist_ok=True)
_log_file = open("outputs/pipeline.log", "a", encoding="utf-8")

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(colors=False),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(file=_log_file),
)
logger = structlog.get_logger("main")


def _infer_domain(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower()) + ".com"


def load_leads(path: Path) -> list[Lead]:
    if not path.exists():
        logger.error("leads_file.not_found", path=str(path)); sys.exit(1)
    leads: list[Lead] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"): continue
        if "," in line:
            parts = [p.strip() for p in line.split(",", 1)]
            name, domain = parts[0], parts[1] if len(parts) > 1 and parts[1] else _infer_domain(parts[0])
        else:
            name, domain = line, _infer_domain(line)
        try:
            leads.append(Lead(company_name=name, company_domain=domain))
        except Exception as exc:
            logger.warning("lead.invalid", line=i, raw=line, error=str(exc))
    if not leads:
        logger.error("leads_file.empty", path=str(path)); sys.exit(1)

    # Deduplicate by domain (case-insensitive)
    seen_domains: set[str] = set()
    unique_leads: list[Lead] = []
    for lead in leads:
        key = lead.company_domain.lower()
        if key in seen_domains:
            logger.warning("lead.duplicate_skipped", company=lead.company_name, domain=lead.company_domain)
            continue
        seen_domains.add(key)
        unique_leads.append(lead)

    skipped = len(leads) - len(unique_leads)
    logger.info("leads.loaded", count=len(unique_leads), duplicates_skipped=skipped)
    return unique_leads


async def enrich_lead(lead: Lead, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        run_id = str(uuid.uuid4())[:8]
        logger.info("lead.start", run_id=run_id, company=lead.company_name)
        state: GraphState = {
            "run_id": run_id, "lead": lead, "raw_signals": None,
            "csv_row": None, "step_logs": [], "error_count": 0, "fatal_error": "",
        }
        try:
            final: GraphState = await graph.ainvoke(state)
        except Exception as exc:
            logger.error("lead.crashed", run_id=run_id, company=lead.company_name, error=str(exc)[:300])
            return {"company": lead.company_name, "domain": lead.company_domain, "status": "CRASHED", "error": str(exc)[:200]}
        row = final.get("csv_row")
        errors = final.get("error_count", 0)
        status = "SUCCESS" if errors == 0 else f"PARTIAL ({errors} errors)"
        logger.info("lead.complete", run_id=run_id, company=lead.company_name, status=status)
        return {
            "company": lead.company_name, "status": status,
            "expansion": row.expansion_detected if row else False,
            "open_roles": row.open_requisitions if row else 0,
            "churn": row.churn_anomalies if row else 0,
            "consolidation": row.consolidation_detected if row else False,
            "confidence": row.llm_confidence_score if row else 0.0,
        }


async def run_pipeline(leads_path: Path, max_concurrent: int) -> None:
    leads = load_leads(leads_path)
    settings = get_settings()
    
    # Clean up old output files to prevent duplicating company rows from previous runs
    if settings.csv_output_path.exists():
        settings.csv_output_path.unlink()
    summary_path = Path(settings.output_dir) / "summary.csv"
    if summary_path.exists():
        summary_path.unlink()

    semaphore = asyncio.Semaphore(max_concurrent)
    logger.info("pipeline.start", total=len(leads), concurrency=max_concurrent, output=str(settings.csv_output_path))
    
    print(f"\n🚀 Starting pipeline for {len(leads)} leads (concurrency={max_concurrent})...")
    print("All detailed logs are being written to 'outputs/pipeline.log'\n")
    
    tasks = [enrich_lead(l, semaphore) for l in leads]
    results = []
    
    from tqdm import tqdm
    with tqdm(total=len(leads), desc="Processing QSR Leads", unit="lead") as pbar:
        for coro in asyncio.as_completed(tasks):
            res = await coro
            results.append(res)
            comp = res.get("company", "")
            stat = res.get("status", "")
            pbar.set_postfix_str(f"Last: {comp[:15]} ({stat})")
            pbar.update(1)
            
    await close_browser()
    W = 76
    print("\n" + "=" * W)
    print("  QSR SIGNAL EXTRACTION ENGINE — COMPLETE")
    print("=" * W)
    print(f"  {'COMPANY':<30} {'STATUS':<18} {'EXP':>4} {'ROLES':>6} {'CHURN':>5} {'CONF':>5}")
    print("-" * W)
    for r in results:
        exp_icon = "EXP" if r.get('expansion') else "---"
        print(f"  {r.get('company',''):<30} {r.get('status',''):<18} "
              f"{exp_icon:>5} {r.get('open_roles',0):>6} "
              f"{r.get('churn',0):>5} {r.get('confidence',0.0):>5.2f}")
    success = sum(1 for r in results if "SUCCESS" in r.get("status",""))
    print("=" * W)
    print(f"  Total: {len(results)} | Success: {success} | Failed: {len(results)-success}")
    print(f"  Output: {settings.csv_output_path}")
    print("=" * W + "\n")


def _build_parser() -> argparse.ArgumentParser:
    s = get_settings()
    p = argparse.ArgumentParser(description="QSR Signal Extraction Engine")
    p.add_argument("--leads", type=Path, default=s.leads_file_path)
    p.add_argument("--concurrency", type=int, default=s.max_concurrent_leads)
    return p


if __name__ == "__main__":
    args = _build_parser().parse_args()
    asyncio.run(run_pipeline(leads_path=args.leads, max_concurrent=args.concurrency))
