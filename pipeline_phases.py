"""
pipeline_phases.py — The three research phases of the ICP pipeline.

Each phase is an async function that:
  1. Builds the exact query from the technical plan
  2. Calls buscar() (backend-agnostic)
  3. Asks the LLM to extract structured data from the raw results
  4. Returns updated AgentState with logs + extracted data

PHASE 1: Identity & Digital Presence
PHASE 2: Scale & Financial Health
PHASE 3: Hiring & Expansion Signals
"""

from __future__ import annotations

import json
import logging
import openai
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI

from config import (
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_DEPLOYMENT,
    AZURE_OPENAI_ENDPOINT,
)
from schemas import (
    AgentState,
    CompanySize,
    FundingStage,
    GrowthSignal,
    SearchStepLog,
)
from search_tools import SearchResult, buscar

logger = logging.getLogger(__name__)


# ── LLM instance (shared across phases) ──────────────────────────────────────

def _get_llm() -> AzureChatOpenAI:
    return AzureChatOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        deployment_name=AZURE_OPENAI_DEPLOYMENT,
        # temperature is not supported by this model (gpt-5.5 / o1-class)
        request_timeout=30,  # hard cap on a single LLM API call
        max_retries=2,        # retry transient network errors automatically
    )


# ── LLM extraction helper ─────────────────────────────────────────────────────

async def _extract(
    llm: AzureChatOpenAI,
    system_prompt: str,
    user_content: str,
    output_key: str = "result",
) -> dict:
    """
    Ask the LLM to extract structured data from search results.
    Always returns a dict even on parse/timeout failure — the pipeline
    must never crash due to a single LLM call going sideways.
    """
    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ])
        text = response.content.strip()
        # Strip markdown code fences if the model wraps the JSON in them.
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except openai.BadRequestError as exc:
        # Azure Content Filter (HTTP 400 / content_filter or jailbreak codes).
        # Raw HTML snippets from search results occasionally trigger false positives.
        # Log and return an empty dict so the pipeline can continue gracefully.
        logger.warning(
            f"Azure Content Filter triggered for key='{output_key}' — skipping extraction. "
            f"Error: {exc}"
        )
        return {}
    except json.JSONDecodeError:
        logger.warning(f"LLM returned non-JSON for '{output_key}': {response.content[:200]}")
        return {output_key: response.content.strip()}
    except Exception as exc:
        logger.error(f"LLM extraction error (key='{output_key}'): {exc}")
        return {}


def _log_step(
    state: AgentState,
    phase: str,
    result: SearchResult,
    extracted: dict,
) -> AgentState:
    """Append a SearchStepLog entry to state.search_logs."""
    log = SearchStepLog(
        company_name=state.company_name,
        phase=phase,
        query_or_url=result.query,
        backend_used=result.backend,
        raw_html_snippet=result.html_snippet or result.text_summary[:600],
        extracted_data=extracted,
        success=result.success,
        error_message=result.error,
        timestamp=datetime.utcnow(),
    )
    state.search_logs.append(log)
    return state


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1: Identity & Digital Presence
# ─────────────────────────────────────────────────────────────────────────────

async def phase1_identity(state: AgentState) -> AgentState:
    """
    Step 1.1: Official URL
    Step 1.2: LinkedIn + industry + description
    """
    llm = _get_llm()
    name = state.company_name
    logger.info(f"[{name}] ── PHASE 1: Identity ──")

    # ── Step 1.1: Official URL ────────────────────────────────────────────────
    query_11 = f"{name} official website"
    result_11 = await buscar(query_11, num_results=5)
    state.current_phase = "1.1"

    if result_11.success and result_11.organic:
        extracted_11 = await _extract(
            llm,
            system_prompt=(
                "You are an OSINT analyst. Given Google search results, identify the official "
                "company website URL. Return ONLY a valid JSON object with this shape:\n"
                '{"official_url": "https://example.com", "confidence": "high|medium|low"}\n'
                "Exclude LinkedIn, Crunchbase, social media, and news sites. "
                "If unsure, return the most likely candidate."
            ),
            user_content=(
                f"Company: {name}\n\nSearch results:\n{result_11.text_summary}"
            ),
            output_key="official_url",
        )
        state.profile.official_url = extracted_11.get("official_url", "")
        logger.info(f"[{name}] 1.1 official_url={state.profile.official_url}")
    else:
        state.profile.processing_errors.append(f"1.1 search failed: {result_11.error}")

    state = _log_step(state, "1.1_official_url", result_11, {"official_url": state.profile.official_url})

    # ── Step 1.2: LinkedIn + industry + description ───────────────────────────
    query_12 = f'site:linkedin.com/company "{name}"'
    result_12 = await buscar(query_12, num_results=5)
    state.current_phase = "1.2"

    if result_12.success and result_12.organic:
        extracted_12 = await _extract(
            llm,
            system_prompt=(
                "You are an OSINT analyst. Extract LinkedIn profile data from search results.\n"
                "Return ONLY valid JSON:\n"
                '{"linkedin_url": "...", "industry": "...", "description": "...", "employee_count_estimate": "..."}\n'
                "Keep description under 200 chars. Industry should be the official LinkedIn sector "
                "(e.g. 'Software Development', 'Retail', 'Healthcare').\n"
                "For employee_count_estimate, extract the size range if mentioned (e.g. '11-50 employees', '51-200 employees')."
            ),
            user_content=(
                f"Company: {name}\n\nSearch results:\n{result_12.text_summary}"
            ),
            output_key="linkedin",
        )
        state.profile.linkedin_url = extracted_12.get("linkedin_url", "")
        state.profile.industry = extracted_12.get("industry", "")
        state.profile.description = extracted_12.get("description", "")
        state.profile.employee_count_estimate = extracted_12.get("employee_count_estimate", "")

        # Map employee count to company size
        emp_est = state.profile.employee_count_estimate.lower()
        if emp_est:
            if any(x in emp_est for x in ["1-10", "11-50", "2-10", "1-9", "1-4"]):
                state.profile.company_size = CompanySize.STARTUP
            elif any(x in emp_est for x in ["51-200", "201-500", "50-200", "51-500"]):
                state.profile.company_size = CompanySize.SMB
            elif any(x in emp_est for x in ["501-1000", "1001-5000", "501-5000"]):
                state.profile.company_size = CompanySize.MID_MARKET
            elif any(x in emp_est for x in ["5001+", "10000+", "5000+"]):
                state.profile.company_size = CompanySize.ENTERPRISE

        logger.info(f"[{name}] 1.2 linkedin={state.profile.linkedin_url} industry={state.profile.industry} size={state.profile.employee_count_estimate}")
    else:
        state.profile.processing_errors.append(f"1.2 search failed: {result_12.error}")

    state = _log_step(state, "1.2_linkedin", result_12, {
        "linkedin_url": state.profile.linkedin_url,
        "industry": state.profile.industry,
        "description": state.profile.description,
        "employee_count_estimate": state.profile.employee_count_estimate,
    })

    return state


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2: Scale & Financial Health
# ─────────────────────────────────────────────────────────────────────────────

async def phase2_scale(state: AgentState) -> AgentState:
    """
    Step 2.1: Stock market / public company check
    Step 2.2: Funding rounds (private/startup)
    Step 2.3: Customer volume estimate
    """
    llm = _get_llm()
    name = state.company_name
    logger.info(f"[{name}] ── PHASE 2: Scale & Finance ──")

    # ── Step 2.1: Public market check ────────────────────────────────────────
    query_21 = f'"{name}" (ticker OR "market cap" OR site:finance.yahoo.com OR site:marketwatch.com)'
    result_21 = await buscar(query_21, num_results=8)
    state.current_phase = "2.1"

    # Provide Phase 1 context to help the LLM disambiguate same-name companies.
    p1_url = state.profile.official_url or "unknown"
    p1_desc = state.profile.description or "no description available"

    extracted_21 = {}
    if result_21.success and result_21.organic:
        extracted_21 = await _extract(
            llm,
            system_prompt=(
                "You are a financial analyst. Determine if this company is publicly traded.\n"
                "Return ONLY valid JSON:\n"
                '{"is_public": true/false, "ticker": "AAPL or null", "exchange": "NASDAQ/NYSE/null", '
                '"market_cap": "e.g. $2.5T or null"}\n'
                "CRITICAL: Cross-reference any found stock ticker with the company's description "
                "and URL provided below. If the ticker belongs to a company in a DIFFERENT country "
                "or a completely different industry (e.g., finding a Tokyo Stock Exchange ticker "
                "for a US software company, or a manufacturing ticker for a SaaS company), "
                "discard it and return is_public: false. "
                "Do NOT assume a ticker is correct just because company names look similar.\n"
                "If no unambiguously matching public ticker is found, return is_public: false."
            ),
            user_content=(
                f"Company: {name}\n"
                f"Official URL: {p1_url}\n"
                f"Description: {p1_desc}\n\n"
                f"Search results:\n{result_21.text_summary}"
            ),
            output_key="public_market",
        )

        if extracted_21.get("is_public"):
            state.profile.stock_ticker = extracted_21.get("ticker") or ""
            state.profile.market_cap = extracted_21.get("market_cap") or ""
            state.profile.funding_stage = FundingStage.PUBLIC
            state.profile.company_size = CompanySize.ENTERPRISE
            logger.info(f"[{name}] 2.1 PUBLIC ticker={state.profile.stock_ticker}")

    state = _log_step(state, "2.1_public_market", result_21, extracted_21)

    # ── Step 2.2: Funding / VC rounds OR traditional revenue signals ──────────
    # Covers both VC-backed startups (Crunchbase, Series A) and traditional
    # companies (annual earnings reports, Q-results, revenue announcements).
    if state.profile.funding_stage != FundingStage.PUBLIC:
        query_22 = (
            f'"{name}" (crunchbase OR "funding round" OR "series A" OR "annual revenue" '
            f'OR "Q3 results" OR "Q4 results" OR "earnings report" OR "raised")'
        )
        result_22 = await buscar(query_22, num_results=8)
        state.current_phase = "2.2"

        extracted_22 = {}
        if result_22.success and result_22.organic:
            extracted_22 = await _extract(
                llm,
                system_prompt=(
                    "You are a startup analyst. Extract funding information from search results.\n"
                    "Return ONLY valid JSON:\n"
                    '{"has_funding": true/false, "total_funding": "$5M or null", '
                    '"stage": "seed|series_a|series_b|series_c_plus|bootstrapped|unknown", '
                    '"investors": ["investor1"] }\n'
                    "If no funding found, return has_funding: false and stage: unknown."
                ),
                user_content=f"Company: {name}\n\nSearch results:\n{result_22.text_summary}",
                output_key="funding",
            )

            if extracted_22.get("has_funding"):
                state.profile.total_funding = extracted_22.get("total_funding") or ""
                stage_str = extracted_22.get("stage", "unknown")
                try:
                    state.profile.funding_stage = FundingStage(stage_str)
                except ValueError:
                    state.profile.funding_stage = FundingStage.UNKNOWN
                if state.profile.funding_stage in (FundingStage.SERIES_B, FundingStage.SERIES_C_PLUS):
                    state.profile.company_size = CompanySize.MID_MARKET
                elif state.profile.funding_stage == FundingStage.SERIES_A:
                    state.profile.company_size = CompanySize.SMB
                elif state.profile.funding_stage == FundingStage.SEED:
                    state.profile.company_size = CompanySize.STARTUP

            state = _log_step(state, "2.2_funding", result_22, extracted_22)

    # ── Step 2.3: Customer volume estimate ───────────────────────────────────
    official_url = state.profile.official_url
    if official_url:
        domain = official_url.replace("https://", "").replace("http://", "").split("/")[0]
        query_23 = (
            f'site:{domain} ("trusted by" OR "customers" OR "teams relying on" OR "clients" OR '
            f'"case studies" OR "success stories" OR "investor relations" OR "annual report")'
        )
    else:
        query_23 = (
            f'"{name}" ("trusted by" OR "customers" OR "teams relying on" OR "clients" OR '
            f'"case studies" OR "success stories" OR "investor relations" OR "annual report" OR "10,000 users")'
        )

    result_23 = await buscar(query_23, num_results=8)
    state.current_phase = "2.3"

    extracted_23 = {}
    if result_23.success and result_23.organic:
        extracted_23 = await _extract(
            llm,
            system_prompt=(
                "You are a market analyst. Estimate customer volume and notable client logos from search results.\n"
                "Return ONLY valid JSON:\n"
                '{"customer_count_estimate": "e.g. 500+ customers or unknown", '
                '"size_signal": "startup|smb|mid_market|enterprise|unknown", '
                '"evidence": "brief quote or evidence"}\n'
                "You must extract the customer count and notable logos.\n"
                "If the exact word 'customers' is not found, look for synonyms like 'teams', 'organizations', "
                "or 'users relying on us', and look for numbers indicating volume (e.g. '300+ teams', '10,000 users').\n"
                "Also check investor relations pages and annual reports for precise subscriber/customer figures.\n"
                "If you cannot find the exact number, look for milestones (e.g. 'used by over half of the Fortune 500' "
                "or 'thousands of teams'). Do NOT default to 'unknown' if strong contextual evidence exists."
            ),
            user_content=f"Company: {name}\n\nSearch results:\n{result_23.text_summary}",
            output_key="customers",
        )

        state.profile.customer_count_estimate = extracted_23.get("customer_count_estimate", "")
        size_signal = extracted_23.get("size_signal", "unknown")
        if state.profile.company_size == CompanySize.UNKNOWN:
            try:
                state.profile.company_size = CompanySize(size_signal)
            except ValueError:
                pass

    state = _log_step(state, "2.3_customers", result_23, extracted_23)

    return state


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3: Hiring & Growth Signals
# ─────────────────────────────────────────────────────────────────────────────

async def phase3_signals(state: AgentState) -> AgentState:
    """
    Step 3.1: Scan active job postings across ATS portals.
    Step 3.2: Search for expansion and growth news in the last 12 months.
    """
    llm = _get_llm()
    name = state.company_name
    logger.info(f"[{name}] -- PHASE 3: Growth Signals --")

    # Step 3.1: ATS / Job portals
    # Coverage tiers:
    #   Startup/SMB  : Wellfound, Ashby, Workable
    #   Mid-market   : Lever, Greenhouse, SmartRecruiters
    #   Enterprise   : Workday, Taleo (Oracle), iCIMS, SAP SuccessFactors
    industry_ctx = state.profile.industry or "unknown"
    official_url_ctx = state.profile.official_url or name
    query_31 = (
        f'(site:greenhouse.io OR site:boards.greenhouse.io OR site:jobs.lever.co OR '
        f'site:*.myworkdayjobs.com OR site:jobs.taleo.net OR site:icims.com OR '
        f'site:careers.smartrecruiters.com OR site:successfactors.com OR '
        f'site:wellfound.com/company OR site:jobs.ashbyhq.com) '
        f'"{name}" careers'
    )
    result_31 = await buscar(query_31, num_results=10)
    state.current_phase = "3.1"

    extracted_31 = {}
    if result_31.success and result_31.organic:
        extracted_31 = await _extract(
            llm,
            system_prompt=(
                f"You are a talent intelligence analyst researching the company '{name}'.\n"
                f"CRITICAL: You are researching a company in the '{industry_ctx}' sector with "
                f"the official domain '{official_url_ctx}'. "
                f"Discard ANY job postings that clearly belong to completely unrelated businesses "
                f"(e.g., a local gym or a restaurant) that happen to share the same name.\n"
                f"IMPORTANT: Do NOT aggressively filter out jobs if the company name doesn't match "
                f"perfectly. Large enterprises use subsidiaries, parent companies, franchises, and "
                f"sub-brands (e.g., 'Marriott Vacations' belongs to 'Marriott', 'Amazon Web Services' "
                f"belongs to 'Amazon'). If the job is clearly part of the target company's broader "
                f"corporate ecosystem, count it and extract it.\n"
                "Extract active job postings from the remaining valid results.\n"
                "Return ONLY valid JSON:\n"
                '{"has_openings": true/false, "job_titles": ["title 1", "title 2"], '
                '"departments": ["Engineering", "Sales"], "total_count": 5}\n'
                "Include all unique job titles found. Be specific (e.g. 'Senior Backend Engineer' not 'Engineer').\n"
                "If you cannot find exact role names, look for hiring milestone signals. "
                "Do NOT return empty if strong contextual evidence of active hiring exists."
            ),
            user_content=f"Company: {name}\n\nSearch results:\n{result_31.text_summary}",
            output_key="jobs",
        )

        if extracted_31.get("has_openings"):
            state.profile.active_job_postings = extracted_31.get("job_titles", [])
            state.profile.growth_signals.append(GrowthSignal.HIRING)
            logger.info(f"[{name}] 3.1 active jobs: {len(state.profile.active_job_postings)}")

    state = _log_step(state, "3.1_job_postings", result_31, extracted_31)

    # ── Step 3.2: Expansion & growth news ────────────────────────────────────
    query_32 = (
        f'"{name}" (expansion OR "new office" OR "new market" OR "new headquarters" OR '
        f'"hiring" OR "growing team" OR "series" OR "acqui") '
        f'after:2024-01-01'
    )
    result_32 = await buscar(query_32, num_results=8)
    state.current_phase = "3.2"

    extracted_32 = {}
    if result_32.success and result_32.organic:
        extracted_32 = await _extract(
            llm,
            system_prompt=(
                f"You are a business intelligence analyst researching the company '{name}'.\n"
                f"CRITICAL: You are researching a company in the '{industry_ctx}' sector with "
                f"the official domain '{official_url_ctx}'. "
                f"Discard ANY news or signals that belong to local businesses, retail stores, gyms, "
                f"restaurants, or unrelated entities that happen to share the same name. "
                f"Only include results clearly about the company '{name}' at the above domain.\n"
                "Extract expansion and growth signals from the remaining valid results.\n"
                "Return ONLY valid JSON:\n"
                '{"has_signals": true/false, "news_headlines": ["headline 1", "headline 2"], '
                '"signal_types": ["expanding", "hiring", "funded", "contracting", "stable"], '
                '"summary": "brief one-line summary"}\n'
                "signal_types must be from: expanding, hiring, funded, contracting, stable\n"
                "CRITICAL: Only apply the 'contracting' signal if there is EXPLICIT evidence in the "
                "text of layoffs, downsizing, office closures, or massive revenue decline. "
                "NEVER infer or guess 'contracting' from an absence of data, "
                "neutral corporate restructuring, or general market uncertainty.\n"
                "Only include news from the last 18 months."
            ),
            user_content=f"Company: {name}\n\nSearch results:\n{result_32.text_summary}",
            output_key="expansion",
        )

        if extracted_32.get("has_signals"):
            state.profile.expansion_news = extracted_32.get("news_headlines", [])
            for sig_str in extracted_32.get("signal_types", []):
                try:
                    state.profile.growth_signals.append(GrowthSignal(sig_str))
                except ValueError:
                    pass
            logger.info(f"[{name}] 3.2 signals: {state.profile.growth_signals}")

    state = _log_step(state, "3.2_expansion_news", result_32, extracted_32)

    # Deduplicate growth signals
    state.profile.growth_signals = list(set(state.profile.growth_signals))

    return state


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4: LLM Synthesis / Analyst Summary
# ─────────────────────────────────────────────────────────────────────────────

async def phase4_synthesis(state: AgentState) -> AgentState:
    """
    Generate a final analyst summary from all collected data.
    This is the human-readable conclusion that goes into the summary CSV.
    """
    llm = _get_llm()
    name = state.company_name
    logger.info(f"[{name}] ── PHASE 4: Synthesis ──")

    profile = state.profile
    context = f"""
Company: {name}
Official URL: {profile.official_url}
LinkedIn: {profile.linkedin_url}
Industry: {profile.industry}
Description: {profile.description}

== SCALE ==
Company Size: {profile.company_size.value}
Funding Stage: {profile.funding_stage.value}
Total Funding: {profile.total_funding or 'N/A'}
Stock Ticker: {profile.stock_ticker or 'N/A (private)'}
Market Cap: {profile.market_cap or 'N/A'}
Customer Estimate: {profile.customer_count_estimate or 'N/A'}

== GROWTH SIGNALS ==
Growth Signals: {', '.join(s.value for s in profile.growth_signals) or 'none detected'}
Active Job Postings ({len(profile.active_job_postings)} found):
{chr(10).join(f'  - {j}' for j in profile.active_job_postings[:10]) or '  None found'}
Expansion News:
{chr(10).join(f'  - {n}' for n in profile.expansion_news[:5]) or '  None found'}

== ERRORS ==
{chr(10).join(profile.processing_errors) or 'None'}
"""

    extracted = await _extract(
        llm,
        system_prompt=(
            "You are a senior B2B sales intelligence analyst. Based on the research data, "
            "write a concise ICP (Ideal Customer Profile) assessment.\n"
            "Return ONLY valid JSON:\n"
            '{"summary": "3-5 sentence assessment", "icp_score": 1-10, '
            '"recommended_action": "reach_out|monitor|skip|research_more", '
            '"key_buying_signals": ["signal 1", "signal 2"]}\n'
            "icp_score: 10=perfect fit, 1=poor fit. "
            "recommended_action: reach_out=strong signals now, monitor=potential future, "
            "skip=wrong fit, research_more=insufficient data.\n"
            "COMPANY SIZE INFERENCE RULES (apply when explicit employee count is missing):\n"
            "  - If the company is publicly traded OR has raised >$100M in funding -> classify as at least 'mid_market'.\n"
            "  - If market cap exceeds $1B -> classify as 'enterprise'.\n"
            "  - If funding is seed/angel stage or market cap is small (<$50M) -> 'startup' or 'smb'.\n"
            "  - Do NOT default to 'smb' or 'startup' simply because the employee count field is empty; "
            "    always cross-reference funding stage, ticker, and market cap first."
        ),
        user_content=context,
        output_key="synthesis",
    )

    state.profile.analyst_summary = extracted.get("summary", "")
    # Store extra fields in search_logs for reference
    synthesis_log = SearchStepLog(
        company_name=name,
        phase="4.0_synthesis",
        query_or_url="[LLM synthesis - no search]",
        backend_used="llm",
        extracted_data=extracted,
        success=bool(extracted.get("summary")),
        timestamp=datetime.utcnow(),
    )
    state.search_logs.append(synthesis_log)
    state.is_done = True

    logger.info(f"[{name}] ✅ Done. Score={extracted.get('icp_score')} Action={extracted.get('recommended_action')}")
    return state
