"""
graph_orchestrator.py — LangGraph pipeline: routing -> extraction -> synthesis -> CSV.
"""
from __future__ import annotations
import asyncio, re
from typing import Any
import structlog
from langgraph.graph import END, StateGraph
from cache_manager import CacheManager
from config import get_settings
from context_refiner import synthesise_to_csv_row
from csv_writer import append_row
from schemas import ATSHiringResult, CSVRow, GraphState, ICPType, Lead, OSINTResult, RawSignals, StoreExpansionResult
from signal_extractors import extract_ats_hiring, extract_osint_news, extract_store_expansion

logger = structlog.get_logger(__name__)
settings = get_settings()
_cache = CacheManager(settings.cache_db_path)

_RESTAURANT_HINTS = re.compile(
    r"\b(restaurant|food|pizza|burger|cafe|coffee|grill|sushi|taco|"
    r"diner|kitchen|bistro|bakery|chain|franchise|fastfood|quick.?service|qsr)\b", re.I)

def _classify_icp(lead: Lead) -> ICPType:
    combined = f"{lead.company_name} {lead.company_domain}"
    if _RESTAURANT_HINTS.search(combined): return ICPType.RESTAURANT
    return ICPType.RESTAURANT  # QSR-focused engine

async def routing_node(state: GraphState) -> dict[str, Any]:
    lead: Lead = state["lead"]
    icp = _classify_icp(lead)
    logger.info("routing.ok", company=lead.company_name, icp=icp.value)
    return {"step_logs": state.get("step_logs", []) + [f"[routing] {lead.company_name} | icp={icp.value}"], "error_count": 0, "fatal_error": ""}

async def extraction_node(state: GraphState) -> dict[str, Any]:
    lead: Lead = state["lead"]
    logs = list(state.get("step_logs", []))
    error_count = state.get("error_count", 0)
    logs.append(f"[extraction] 3 parallel signals for {lead.company_name}")
    logger.info("extraction.start", company=lead.company_name)

    store_result, ats_result, osint_result = await asyncio.gather(
        extract_store_expansion(lead.company_name, lead.company_domain, _cache),
        extract_ats_hiring(lead.company_name, lead.company_domain, _cache),
        extract_osint_news(lead.company_name, _cache),
    )

    for result, name in [(store_result,"store_locator"),(ats_result,"ats_hiring"),(osint_result,"osint_news")]:
        if result.error:
            # Log as warning but only count as error if it's a real failure
            # (missing optional API key is expected, not an error)
            is_optional_missing = "not set in .env" in result.error or "not configured" in result.error
            if is_optional_missing:
                logs.append(f"[extraction] {name} info: {result.error[:100]}")
            else:
                error_count += 1
                logs.append(f"[extraction] {name} error: {result.error[:100]}")

    cache_hits = []
    if isinstance(store_result, StoreExpansionResult) and store_result.from_cache: cache_hits.append("store_locator")
    if isinstance(ats_result, ATSHiringResult) and ats_result.from_cache: cache_hits.append("ats_hiring")
    if isinstance(osint_result, OSINTResult) and osint_result.from_cache: cache_hits.append("osint_news")

    logs.append(f"[extraction] Done | store={store_result.store_count_today} | ats={ats_result.ats_platform}({ats_result.open_requisitions}) | osint={osint_result.articles_found} | cache={cache_hits}")
    logger.info("extraction.complete", company=lead.company_name, store_delta=store_result.store_count_delta, open_roles=ats_result.open_requisitions, churn=ats_result.churn_anomalies, articles=osint_result.articles_found)

    return {"raw_signals": RawSignals(store=store_result, ats=ats_result, osint=osint_result), "step_logs": logs, "error_count": error_count}

async def synthesis_node(state: GraphState) -> dict[str, Any]:
    lead: Lead = state["lead"]
    signals: RawSignals = state["raw_signals"]
    logs = list(state.get("step_logs", []))
    logs.append(f"[synthesis] Calling GPT-5.5 for {lead.company_name}")
    csv_row: CSVRow = await synthesise_to_csv_row(lead, signals)
    append_row(settings.csv_output_path, csv_row)
    
    # Also write a clean, digested 1-word summary row
    from pathlib import Path
    from csv_writer import append_summary_row
    summary_path = Path(settings.output_dir) / "summary.csv"
    append_summary_row(summary_path, csv_row)
    
    logs.append(f"[synthesis] Done | conf={csv_row.llm_confidence_score:.2f} | expansion={csv_row.expansion_detected} | hiring={csv_row.hiring_signal_strength} | primary_signal={csv_row.primary_signal}")
    logger.info("synthesis.done", company=lead.company_name, confidence=csv_row.llm_confidence_score)
    return {"csv_row": csv_row, "step_logs": logs}

def build_graph() -> Any:
    wf = StateGraph(GraphState)
    wf.add_node("routing_node", routing_node)
    wf.add_node("extraction_node", extraction_node)
    wf.add_node("synthesis_node", synthesis_node)
    wf.set_entry_point("routing_node")
    wf.add_edge("routing_node", "extraction_node")
    wf.add_edge("extraction_node", "synthesis_node")
    wf.add_edge("synthesis_node", END)
    return wf.compile()

graph = build_graph()
