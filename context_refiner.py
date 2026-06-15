"""
context_refiner.py — GPT-5.5 synthesis layer for QSR signals -> CSVRow.
"""
from __future__ import annotations
import json, re
import structlog
from openai import AsyncAzureOpenAI
from config import get_settings
from schemas import ATSHiringResult, CSVRow, Lead, OSINTResult, RawSignals, StoreExpansionResult

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """You are a deterministic B2B signal analyst for QSR leads.
You receive ONLY pre-verified structured data from web scrapers.
RULES: Do NOT invent or infer anything. If a field is empty in the input, leave the output field blank.
Respond ONLY with a single valid JSON object. No markdown fences.
Output schema:
{
  "expansion_summary": string,
  "hiring_signal_strength": string,
  "critical_roles": [string],
  "consolidation_detected": boolean,
  "outreach_context": string,
  "confidence_score": float
}
outreach_context: 2-4 sentences an SDR can paste directly into a cold email. Reference specific facts. No filler."""

def _strip_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    return m.group(1).strip() if m else text.strip()

def _build_user_message(lead: Lead, signals: RawSignals) -> str:
    store = signals.store
    ats = signals.ats
    osint = signals.osint
    ctx = {
        "company": lead.company_name, "domain": lead.company_domain,
        "signal_1_store_expansion": {
            "source": store.source_url, "store_count_today": store.store_count_today,
            "store_count_delta": store.store_count_delta,
            "new_store_ids_count": len(store.new_store_ids),
            "sample_new_locations": store.new_store_ids[:5],
            "expansion_detected": store.expansion_detected, "error": store.error or None,
        },
        "signal_2_ats_hiring": {
            "platform_detected": ats.ats_platform, "platform_url": ats.ats_url,
            "open_requisitions": ats.open_requisitions,
            "churn_anomalies_30d": ats.churn_anomalies,
            "sample_roles": [j.title for j in ats.jobs[:15]], "error": ats.error or None,
        },
        "signal_3_osint_news": {
            "query_used": osint.query_used, "articles_found": osint.articles_found,
            "top_headline": osint.top_headline, "top_publication": osint.top_publication,
            "top_date": osint.top_date,
            "all_headlines": [a.headline for a in osint.articles[:5]], "error": osint.error or None,
        },
    }
    return json.dumps(ctx, indent=2, ensure_ascii=False)

def _determine_primary_signal(store_delta: int, expansion: bool, strength: str, consolidation: bool, context: str) -> str:
    if consolidation:
        return "CONSOLIDATION"
    if expansion or store_delta > 0:
        return "EXPANSION"
    if strength in ("HIGH", "MEDIUM"):
        return "HIRING"
    if "unreachable" in context.lower():
        return "UNREACHABLE"
    return "STABLE"

async def synthesise_to_csv_row(lead: Lead, signals: RawSignals) -> CSVRow:
    s = get_settings()
    store, ats, osint = signals.store, signals.ats, signals.osint
    cache_hits = []
    if store.from_cache: cache_hits.append("store_locator")
    if ats.from_cache:   cache_hits.append("ats_hiring")
    if osint.from_cache: cache_hits.append("osint_news")

    client = AsyncAzureOpenAI(
        azure_endpoint=s.azure_openai_endpoint,
        api_key=s.azure_openai_api_key,
        api_version=s.azure_openai_api_version,
    )
    logger.info("llm_synthesis.start", company=lead.company_name)
    try:
        resp = await client.chat.completions.create(
            model=s.azure_openai_deployment,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message(lead, signals)},
            ],
            temperature=1.0, max_completion_tokens=1024,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(_strip_fence(resp.choices[0].message.content or ""))
        logger.info("llm_synthesis.ok", company=lead.company_name, confidence=parsed.get("confidence_score", 0.0))
        
        strength = parsed.get("hiring_signal_strength", "LOW")
        consolidation = parsed.get("consolidation_detected", False)
        context = parsed.get("outreach_context", "")
        primary_signal = _determine_primary_signal(
            store.store_count_delta,
            store.expansion_detected,
            strength,
            consolidation,
            context
        )
        
        return CSVRow(
            company_name=lead.company_name, company_domain=lead.company_domain,
            source_store_locator=store.source_url,
            store_count_today=store.store_count_today, store_count_delta=store.store_count_delta,
            new_store_ids=", ".join(store.new_store_ids[:10]),
            expansion_detected=store.expansion_detected,
            expansion_summary=parsed.get("expansion_summary", ""),
            source_ats_platform=ats.ats_platform, source_ats_url=ats.ats_url,
            open_requisitions=ats.open_requisitions, churn_anomalies=ats.churn_anomalies,
            critical_roles=", ".join(parsed.get("critical_roles", [])),
            hiring_signal_strength=strength,
            source_osint_query=osint.query_used, osint_articles_found=osint.articles_found,
            osint_headline=osint.top_headline, osint_publication=osint.top_publication,
            osint_date=osint.top_date,
            consolidation_detected=consolidation,
            llm_confidence_score=float(parsed.get("confidence_score", 0.0)),
            outreach_context=context,
            cache_hits=", ".join(cache_hits),
            primary_signal=primary_signal,
        )
    except Exception as exc:
        logger.error("llm_synthesis.error", company=lead.company_name, error=str(exc)[:200])
    # Fallback: no LLM, fill raw data
    logger.warning("llm_synthesis.fallback", company=lead.company_name)
    hs = ats.hiring_signal_strength.value if hasattr(ats.hiring_signal_strength, "value") else str(ats.hiring_signal_strength)
    primary_signal = _determine_primary_signal(
        store.store_count_delta,
        store.expansion_detected,
        hs,
        osint.consolidation_detected,
        "[LLM synthesis failed]"
    )
    return CSVRow(
        company_name=lead.company_name, company_domain=lead.company_domain,
        source_store_locator=store.source_url,
        store_count_today=store.store_count_today, store_count_delta=store.store_count_delta,
        new_store_ids=", ".join(store.new_store_ids[:10]),
        expansion_detected=store.expansion_detected,
        source_ats_platform=ats.ats_platform, source_ats_url=ats.ats_url,
        open_requisitions=ats.open_requisitions, churn_anomalies=ats.churn_anomalies,
        critical_roles=", ".join(ats.critical_roles), hiring_signal_strength=hs,
        source_osint_query=osint.query_used, osint_articles_found=osint.articles_found,
        osint_headline=osint.top_headline, osint_publication=osint.top_publication,
        osint_date=osint.top_date, consolidation_detected=osint.consolidation_detected,
        llm_confidence_score=0.0,
        outreach_context="[LLM synthesis failed — raw signals captured, manual review needed]",
        cache_hits=", ".join(cache_hits),
        primary_signal=primary_signal,
    )
