"""
pipeline_phases.py - Research phases for the Automotive & Transportation Design ICP pipeline.
Target niche: Automotive and Transportation Design Companies in Michigan.

PHASE 1: Identity & Digital Presence
PHASE 2: Scale & Financial Health
PHASE 3: Hiring & Expansion Signals
PHASE 4: Automotive Design Signals (8 new ICP signals)
PHASE 5: LLM Synthesis (Creative Services & Transportation Design Strategist)
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
    AutomotiveDesignSignals,
    CompanySize,
    FundingStage,
    GrowthSignal,
    SearchStepLog,
)
from search_tools import SearchResult, buscar

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

class PublicMarketExtraction(BaseModel):
    is_public: bool = False
    ticker: str | None = None
    exchange: str | None = None
    market_cap: str | None = None

class JobPostingsExtraction(BaseModel):
    has_openings: bool = False
    active_job_count: int = 0
    job_titles: list[str] = Field(default_factory=list)
    departments: list[str] = Field(default_factory=list)

# ── LLM instance (shared across phases) ──────────────────────────────────────

def _get_llm() -> AzureChatOpenAI:
    return AzureChatOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        deployment_name=AZURE_OPENAI_DEPLOYMENT,
        # temperature is not supported by this model (gpt-5.5 / o1-class)
        request_timeout=30,
        max_retries=2,
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
    Always returns a dict even on parse/timeout failure so the pipeline
    never crashes due to a single LLM call going sideways.
    """
    try:
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ])
        text = response.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text)
    except openai.BadRequestError as exc:
        # Azure Content Filter (HTTP 400). Raw HTML snippets from search results
        # occasionally trigger false positives. Log and return empty dict.
        logger.warning(
            f"Azure Content Filter triggered for key='{output_key}' — skipping. "
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
            user_content=(f"Company: {name}\n\nSearch results:\n{result_11.text_summary}"),
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
                "(e.g. 'Automotive', 'Industrial Design', 'Transportation/Trucking/Railroad').\n"
                "For employee_count_estimate, extract the size range if mentioned (e.g. '11-50 employees')."
            ),
            user_content=(f"Company: {name}\n\nSearch results:\n{result_12.text_summary}"),
            output_key="linkedin",
        )
        state.profile.linkedin_url = extracted_12.get("linkedin_url", "")
        state.profile.industry = extracted_12.get("industry", "")
        state.profile.description = extracted_12.get("description", "")
        state.profile.employee_count_estimate = extracted_12.get("employee_count_estimate", "")

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

        logger.info(f"[{name}] 1.2 linkedin={state.profile.linkedin_url} industry={state.profile.industry}")
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

    p1_url = state.profile.official_url or "unknown"
    p1_desc = state.profile.description or "no description available"

    extracted_21 = {}
    if result_21.success and result_21.organic:
        extracted_21 = await _extract(
            llm,
            system_prompt=(
                "You are a financial analyst. Determine if this company is publicly traded.\n"
                "CRITICAL: Be extremely aggressive in identifying stock tickers and market capitalization from the search results.\n"
                "Look closely for ticker symbols (often in parentheses next to the company name, e.g. 'The Shyft Group(SHYF)' or 'SHYF') "
                "and any conversational mentions of shares, stock value, trading, or market capitalization (e.g. 'shares are valued at...', "
                "'market cap stands at 438.95M').\n"
                "If a matching stock ticker or market cap is found/mentioned for this company, set is_public to true, "
                "extract the ticker (e.g., 'SHYF' or 'F'), and extract the market cap (e.g., '438.95M' or '$12B').\n"
                "Do not discard a ticker unless you are absolutely sure it belongs to an entirely different, unrelated company.\n\n"
                "Return ONLY a valid JSON object matching this schema:\n"
                '{"is_public": true/false, "ticker": "TICKER or null", "exchange": "EXCHANGE_OR_NULL", "market_cap": "MARKET_CAP_OR_NULL"}'
            ),
            user_content=(
                f"Company: {name}\nOfficial URL: {p1_url}\nDescription: {p1_desc}\n\n"
                f"Search results:\n{result_21.text_summary}"
            ),
            output_key="public_market",
        )

        try:
            validated_21 = PublicMarketExtraction.model_validate(extracted_21)
            extracted_21 = validated_21.model_dump()
        except Exception:
            is_pub = extracted_21.get("is_public")
            if not isinstance(is_pub, bool):
                is_pub = str(is_pub).lower() in ("true", "1", "yes")
            extracted_21 = {
                "is_public": is_pub,
                "ticker": extracted_21.get("ticker") if extracted_21.get("ticker") else None,
                "exchange": extracted_21.get("exchange") if extracted_21.get("exchange") else None,
                "market_cap": extracted_21.get("market_cap") if extracted_21.get("market_cap") else None,
            }

        if extracted_21.get("is_public"):
            state.profile.stock_ticker = extracted_21.get("ticker") or ""
            state.profile.market_cap = extracted_21.get("market_cap") or ""
            state.profile.funding_stage = FundingStage.PUBLIC
            state.profile.company_size = CompanySize.ENTERPRISE
            logger.info(f"[{name}] 2.1 PUBLIC ticker={state.profile.stock_ticker}")

    state = _log_step(state, "2.1_public_market", result_21, extracted_21)

    # ── Step 2.2: Funding / VC rounds OR revenue signals ─────────────────────
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
                    '"investors": ["investor1"]}\n'
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
            f'site:{domain} ("trusted by" OR "customers" OR "clients" OR '
            f'"case studies" OR "success stories" OR "annual report")'
        )
    else:
        query_23 = (
            f'"{name}" ("trusted by" OR "customers" OR "clients" OR '
            f'"case studies" OR "success stories" OR "annual report")'
        )

    result_23 = await buscar(query_23, num_results=8)
    state.current_phase = "2.3"

    extracted_23 = {}
    if result_23.success and result_23.organic:
        extracted_23 = await _extract(
            llm,
            system_prompt=(
                "You are a market analyst. Estimate customer volume and notable client logos.\n"
                "Return ONLY valid JSON:\n"
                '{"customer_count_estimate": "e.g. 500+ customers or unknown", '
                '"size_signal": "startup|smb|mid_market|enterprise|unknown", '
                '"evidence": "brief quote or evidence"}\n'
                "Check annual reports for precise figures. Do NOT default to 'unknown' if strong contextual evidence exists."
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
# PHASE 3: Hiring & Growth Signals (general)
# ─────────────────────────────────────────────────────────────────────────────

async def phase3_signals(state: AgentState) -> AgentState:
    """
    Step 3.1: Scan active job postings across ATS portals.
    Step 3.2: Search for expansion and growth news in the last 12 months.
    """
    llm = _get_llm()
    name = state.company_name
    logger.info(f"[{name}] ── PHASE 3: Growth Signals ──")

    industry_ctx = state.profile.industry or "Automotive / Transportation Design"
    official_url_ctx = state.profile.official_url or name

    # ── Step 3.1: ATS / Job portals ──────────────────────────────────────────
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
                f"You are a talent intelligence analyst researching '{name}' "
                f"({industry_ctx}, domain: {official_url_ctx}).\n"
                "Discard postings that clearly belong to unrelated businesses sharing the same name.\n"
                "Large enterprises use subsidiaries and sub-brands — count those too.\n"
                "Return ONLY a valid JSON object matching this schema:\n"
                '{\n'
                '  "has_openings": true/false,\n'
                '  "active_job_count": 5,\n'
                '  "job_titles": ["title 1", "title 2"],\n'
                '  "departments": ["Engineering", "Design"]\n'
                '}\n'
                "Enforce strict JSON output adherence. If no openings or titles are found, "
                "set has_openings to false, active_job_count to 0, and job_titles to [].\n"
                "Be specific with role titles (e.g. 'CMF Designer' not 'Designer')."
            ),
            user_content=f"Company: {name}\n\nSearch results:\n{result_31.text_summary}",
            output_key="jobs",
        )

    try:
        validated_31 = JobPostingsExtraction.model_validate(extracted_31)
        extracted_31 = validated_31.model_dump()
    except Exception:
        is_open = extracted_31.get("has_openings") if isinstance(extracted_31.get("has_openings"), bool) else False
        job_count = extracted_31.get("active_job_count")
        if not isinstance(job_count, int):
            try:
                job_count = int(job_count) if job_count is not None else 0
            except ValueError:
                job_count = 0
        titles = extracted_31.get("job_titles")
        if not isinstance(titles, list):
            titles = []
        deps = extracted_31.get("departments")
        if not isinstance(deps, list):
            deps = []
        extracted_31 = {
            "has_openings": is_open,
            "active_job_count": job_count,
            "job_titles": titles,
            "departments": deps
        }

    if extracted_31.get("has_openings"):
        state.profile.active_job_postings = extracted_31.get("job_titles", [])
        state.profile.growth_signals.append(GrowthSignal.HIRING)
        logger.info(f"[{name}] 3.1 active jobs: {len(state.profile.active_job_postings)}")

    state = _log_step(state, "3.1_job_postings", result_31, extracted_31)

    # ── Step 3.2: Expansion & growth news ────────────────────────────────────
    query_32 = (
        f'"{name}" (expansion OR "new office" OR "new market" OR "new headquarters" OR '
        f'"hiring" OR "growing team" OR "series" OR "acqui") after:2024-01-01'
    )
    result_32 = await buscar(query_32, num_results=8)
    state.current_phase = "3.2"

    extracted_32 = {}
    if result_32.success and result_32.organic:
        extracted_32 = await _extract(
            llm,
            system_prompt=(
                f"You are a business intelligence analyst researching '{name}' "
                f"({industry_ctx}, domain: {official_url_ctx}).\n"
                "Discard news about unrelated local businesses sharing the same name.\n"
                "Return ONLY valid JSON:\n"
                '{"has_signals": true/false, "news_headlines": ["headline 1"], '
                '"signal_types": ["expanding", "hiring", "funded", "contracting", "stable"], '
                '"summary": "brief one-line summary"}\n'
                "signal_types must be from: expanding, hiring, funded, contracting, stable.\n"
                "Only apply 'contracting' if there is EXPLICIT evidence of layoffs or office closures.\n"
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
# PHASE 4: Automotive Design Signals (the 8 new ICP signals)
# ─────────────────────────────────────────────────────────────────────────────

async def phase4_automotive_signals(state: AgentState) -> AgentState:
    """
    The core new phase targeting Automotive & Transportation Design companies in Michigan.

    Sub-steps:
      4.1 — Signal 1 & 2: Internal creative team + creative support need
      4.2 — Signal 3 & 4: Creative tech stack + enterprise software budget
      4.3 — Signal 5:     Creative/Design/Engineering job postings
      4.4 — Signal 6:     Upskilling / workforce development programs
      4.5 — Signal 7 & 8: Creative leadership + Michigan local involvement
    """
    llm = _get_llm()
    name = state.company_name
    sigs = state.profile.automotive_signals  # AutomotiveDesignSignals instance
    logger.info(f"[{name}] ── PHASE 4: Automotive Design Signals ──")

    # ── Step 4.1: Internal Creative Team & Creative Support Need ─────────────
    # Search the company site + LinkedIn for studio/design-team mentions.
    query_41 = (
        f'"{name}" Michigan ('
        f'"design studio" OR "creative team" OR "CMF" OR "interior design" '
        f'OR "exterior design" OR "transportation design" OR "creative director" '
        f'OR "design agency" OR "design consultancy" OR "creative services")'
    )
    result_41 = await buscar(query_41, num_results=8)
    state.current_phase = "4.1"

    extracted_41 = {}
    if result_41.success and result_41.organic:
        extracted_41 = await _extract(
            llm,
            system_prompt=(
                "You are a Creative Services Intelligence analyst specializing in automotive design.\n"
                "Analyze the search results and determine:\n"
                "1. Does this company have an INTERNAL creative/design team or studio?\n"
                "2. Does this company show signs of NEEDING external creative support "
                "(RFPs, partnerships with design agencies, outsourcing mentions)?\n"
                "Return ONLY valid JSON:\n"
                '{"has_internal_creative_team": true/false, '
                '"internal_creative_team_evidence": "brief evidence string", '
                '"requires_creative_support": true/false, '
                '"creative_support_evidence": "brief evidence string"}'
            ),
            user_content=f"Company: {name} (Michigan automotive/transportation design)\n\nSearch results:\n{result_41.text_summary}",
            output_key="creative_team",
        )
        sigs.has_internal_creative_team = extracted_41.get("has_internal_creative_team", False)
        sigs.internal_creative_team_evidence = extracted_41.get("internal_creative_team_evidence", "")
        sigs.requires_creative_support = extracted_41.get("requires_creative_support", False)
        sigs.creative_support_evidence = extracted_41.get("creative_support_evidence", "")
        logger.info(f"[{name}] 4.1 internal_team={sigs.has_internal_creative_team} support_need={sigs.requires_creative_support}")

    state = _log_step(state, "4.1_creative_team", result_41, extracted_41)

    # ── Step 4.2: Creative Tech Stack + Enterprise Software Budget ────────────
    # Target keywords: Autodesk Alias, Autodesk VRED, Adobe, Unity, Unreal, ZBrush, CATIA
    query_42 = (
        f'"{name}" ('
        f'"Autodesk Alias" OR "Autodesk VRED" OR "Adobe Creative" OR "Unity" OR '
        f'"Unreal Engine" OR "CATIA" OR "Rhino" OR "ZBrush" OR "SolidWorks" OR '
        f'"enterprise license" OR "software license" OR "digital prototyping" OR "3D visualization")'
    )
    result_42 = await buscar(query_42, num_results=8)
    state.current_phase = "4.2"

    extracted_42 = {}
    if result_42.success and result_42.organic:
        extracted_42 = await _extract(
            llm,
            system_prompt=(
                "You are a Creative Tech Stack analyst for automotive and transportation design.\n"
                "Identify which creative/design software tools this company uses or mentions.\n"
                "Also infer if they purchase enterprise software licenses (job posts requiring tool proficiency, "
                "press releases about software partnerships, digital transformation announcements).\n"
                "Return ONLY valid JSON:\n"
                '{"detected_creative_tools": ["Autodesk Alias", "Unreal Engine"], '
                '"tech_stack_evidence": "brief evidence string", '
                '"has_enterprise_software_budget": true/false, '
                '"budget_evidence": "brief evidence string"}'
            ),
            user_content=f"Company: {name}\n\nSearch results:\n{result_42.text_summary}",
            output_key="tech_stack",
        )
        sigs.detected_creative_tools = extracted_42.get("detected_creative_tools", [])
        sigs.tech_stack_evidence = extracted_42.get("tech_stack_evidence", "")
        sigs.has_enterprise_software_budget = extracted_42.get("has_enterprise_software_budget", False)
        sigs.budget_evidence = extracted_42.get("budget_evidence", "")
        logger.info(f"[{name}] 4.2 tools={sigs.detected_creative_tools} budget={sigs.has_enterprise_software_budget}")

    state = _log_step(state, "4.2_tech_stack", result_42, extracted_42)

    # ── Step 4.3: Creative/Design/Engineering Hiring ──────────────────────────
    # Specifically target creative roles: CMF, Transportation Designer, UX/UI for mobility, etc.
    query_43 = (
        f'"{name}" jobs ('
        f'"transportation designer" OR "automotive designer" OR "CMF designer" '
        f'"industrial designer" OR "exterior designer" OR "interior designer" OR '
        f'"creative director" OR "UX designer" OR "3D artist" OR "visualization engineer" '
        f'OR "design engineer" OR "Alias modeler" OR "surface designer") site:linkedin.com OR site:indeed.com OR site:glassdoor.com'
    )
    result_43 = await buscar(query_43, num_results=10)
    state.current_phase = "4.3"

    extracted_43 = {}
    if result_43.success and result_43.organic:
        extracted_43 = await _extract(
            llm,
            system_prompt=(
                "You are a Talent Intelligence analyst for automotive and transportation design.\n"
                "Identify active job postings specifically for creative, design, or design-engineering roles.\n"
                "Focus on: Transportation Designers, CMF Designers, Industrial Designers, "
                "UX/UI Designers for mobility, 3D Artists, Visualization Engineers, Alias Modelers, "
                "Creative Directors, Design Engineers.\n"
                "Return ONLY valid JSON:\n"
                '{"is_hiring_creative_roles": true/false, '
                '"creative_job_titles": ["CMF Designer", "Transportation Designer"], '
                '"hiring_evidence": "brief evidence string"}'
            ),
            user_content=f"Company: {name}\n\nSearch results:\n{result_43.text_summary}",
            output_key="creative_hiring",
        )
        sigs.is_hiring_creative_roles = extracted_43.get("is_hiring_creative_roles", False)
        sigs.creative_job_titles = extracted_43.get("creative_job_titles", [])
        sigs.hiring_evidence = extracted_43.get("hiring_evidence", "")
        logger.info(f"[{name}] 4.3 hiring_creative={sigs.is_hiring_creative_roles} roles={sigs.creative_job_titles}")

    state = _log_step(state, "4.3_creative_hiring", result_43, extracted_43)

    # ── Step 4.4: Upskilling / Workforce Development ──────────────────────────
    query_44 = (
        f'"{name}" Michigan ('
        f'"workforce development" OR "upskilling" OR "professional development" '
        f'OR "training program" OR "apprenticeship" OR "tuition reimbursement" '
        f'OR "learning & development" OR "college partnership" OR "community college" '
        f'OR "MEDC" OR "workforce training grant")'
    )
    result_44 = await buscar(query_44, num_results=8)
    state.current_phase = "4.4"

    extracted_44 = {}
    if result_44.success and result_44.organic:
        extracted_44 = await _extract(
            llm,
            system_prompt=(
                "You are a Workforce Development analyst specializing in Michigan automotive companies.\n"
                "Determine if the company offers or participates in upskilling, professional development, "
                "or workforce training programs (internal programs, community college partnerships, "
                "MEDC grants, apprenticeship programs, tuition reimbursement, etc.).\n"
                "Return ONLY valid JSON:\n"
                '{"offers_upskilling": true/false, '
                '"upskilling_programs": ["Ford College Graduate Program", "MEDC Training Grant"], '
                '"upskilling_evidence": "brief evidence string"}'
            ),
            user_content=f"Company: {name} (Michigan)\n\nSearch results:\n{result_44.text_summary}",
            output_key="upskilling",
        )
        sigs.offers_upskilling = extracted_44.get("offers_upskilling", False)
        sigs.upskilling_programs = extracted_44.get("upskilling_programs", [])
        sigs.upskilling_evidence = extracted_44.get("upskilling_evidence", "")
        logger.info(f"[{name}] 4.4 upskilling={sigs.offers_upskilling}")

    state = _log_step(state, "4.4_upskilling", result_44, extracted_44)

    # ── Step 4.5: Creative Leadership + Michigan Local Involvement ────────────
    query_45 = (
        f'"{name}" Michigan ('
        f'"Chief Design Officer" OR "VP of Design" OR "Head of Design" OR "Design Director" '
        f'OR "Creative Director" OR "design leadership" OR '
        f'"MEDC" OR "Michigan Economic Development" OR "community college alliance" '
        f'OR "workforce pipeline" OR "preservation registry" OR "local grant" '
        f'OR "regional association" OR "Detroit Design" OR "UM-Dearborn" OR "Macomb")'
    )
    result_45 = await buscar(query_45, num_results=8)
    state.current_phase = "4.5"

    extracted_45 = {}
    if result_45.success and result_45.organic:
        extracted_45 = await _extract(
            llm,
            system_prompt=(
                "You are a Regional Business Intelligence analyst for the Michigan automotive design ecosystem.\n"
                "From the search results, determine:\n"
                "1. Does the company have a recognized leadership position in Creative/Design "
                "(C-suite or VP-level design executive, award-winning design leader, etc.)?\n"
                "2. Does the company have active local Michigan involvement: MEDC grants, "
                "community college alliances, preservation registries, Detroit Design events, "
                "regional workforce pipeline programs, or similar regional associations?\n"
                "Return ONLY valid JSON:\n"
                '{"has_creative_leadership": true/false, '
                '"creative_leadership_titles": ["Chief Design Officer", "VP of Transportation Design"], '
                '"leadership_evidence": "brief evidence string", '
                '"has_michigan_local_involvement": true/false, '
                '"michigan_involvement_details": ["MEDC grant 2024", "Macomb CC partnership"], '
                '"michigan_involvement_evidence": "brief evidence string"}'
            ),
            user_content=f"Company: {name} (Michigan)\n\nSearch results:\n{result_45.text_summary}",
            output_key="leadership_michigan",
        )
        sigs.has_creative_leadership = extracted_45.get("has_creative_leadership", False)
        sigs.creative_leadership_titles = extracted_45.get("creative_leadership_titles", [])
        sigs.leadership_evidence = extracted_45.get("leadership_evidence", "")
        sigs.has_michigan_local_involvement = extracted_45.get("has_michigan_local_involvement", False)
        sigs.michigan_involvement_details = extracted_45.get("michigan_involvement_details", [])
        sigs.michigan_involvement_evidence = extracted_45.get("michigan_involvement_evidence", "")
        logger.info(
            f"[{name}] 4.5 leadership={sigs.has_creative_leadership} "
            f"mi_involvement={sigs.has_michigan_local_involvement}"
        )

    state = _log_step(state, "4.5_leadership_michigan", result_45, extracted_45)

    # Commit the updated signals object back to the profile
    state.profile.automotive_signals = sigs

    return state



# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5: LLM Synthesis — Creative Services & Transportation Design Strategist
# ─────────────────────────────────────────────────────────────────────────────

async def phase5_synthesis(state: AgentState) -> AgentState:
    """
    Generate a final analyst summary from all collected data.
    The LLM acts as a Creative Services & Transportation Design Strategist,
    scoring the company 1-10 as a potential client for high-end creative
    services or 3D/Design enterprise software.
    """
    llm = _get_llm()
    name = state.company_name
    logger.info(f"[{name}] ── PHASE 5: Synthesis ──")

    profile = state.profile
    sigs = profile.automotive_signals

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

== GENERAL GROWTH SIGNALS ==
Growth Signals: {', '.join(s.value for s in profile.growth_signals) or 'none detected'}
Active Job Postings ({len(profile.active_job_postings)} found):
{chr(10).join(f'  - {j}' for j in profile.active_job_postings[:10]) or '  None found'}
Expansion News:
{chr(10).join(f'  - {n}' for n in profile.expansion_news[:5]) or '  None found'}

== AUTOMOTIVE DESIGN SIGNALS (8 ICP Signals) ==
Signal 1 — Internal Creative Team: {sigs.has_internal_creative_team}
  Evidence: {sigs.internal_creative_team_evidence or 'N/A'}

Signal 2 — Requires Creative Support: {sigs.requires_creative_support}
  Evidence: {sigs.creative_support_evidence or 'N/A'}

Signal 3 — Detected Creative Tools: {', '.join(sigs.detected_creative_tools) or 'None detected'}
  Evidence: {sigs.tech_stack_evidence or 'N/A'}

Signal 4 — Enterprise Software Budget: {sigs.has_enterprise_software_budget}
  Evidence: {sigs.budget_evidence or 'N/A'}

Signal 5 — Hiring Creative/Design Roles: {sigs.is_hiring_creative_roles}
  Creative Job Titles: {', '.join(sigs.creative_job_titles) or 'None'}
  Evidence: {sigs.hiring_evidence or 'N/A'}

Signal 6 — Offers Upskilling/Workforce Dev: {sigs.offers_upskilling}
  Programs: {', '.join(sigs.upskilling_programs) or 'None'}
  Evidence: {sigs.upskilling_evidence or 'N/A'}

Signal 7 — Creative/Design Leadership: {sigs.has_creative_leadership}
  Leadership Titles: {', '.join(sigs.creative_leadership_titles) or 'None'}
  Evidence: {sigs.leadership_evidence or 'N/A'}

Signal 8 — Michigan Local Involvement: {sigs.has_michigan_local_involvement}
  Details: {', '.join(sigs.michigan_involvement_details) or 'None'}
  Evidence: {sigs.michigan_involvement_evidence or 'N/A'}

== PROCESSING ERRORS ==
{chr(10).join(profile.processing_errors) or 'None'}
"""

    extracted = await _extract(
        llm,
        system_prompt=(
            "You are a senior Creative Services & Transportation Design Strategist, "
            "specializing in the Michigan automotive and mobility design ecosystem. "
            "Your role is to evaluate whether a target company is an ideal client for:\n"
            "  (a) High-end creative services (design consulting, CMF strategy, concept development), OR\n"
            "  (b) 3D/Design enterprise software (Autodesk Alias, Unreal Engine, Adobe, Unity, etc.).\n\n"
            "Evaluate all 8 ICP signals provided and produce a comprehensive assessment.\n\n"
            "ICP Score Rubric (1–10):\n"
            "  9-10: Has internal design team + enterprise tools + hiring + Michigan presence — prime target\n"
            "  7-8:  Strong signals in 4-6 areas — strong prospect, reach out soon\n"
            "  5-6:  Signals in 2-3 areas — worth monitoring; nurture relationship\n"
            "  3-4:  Weak or indirect signals — low priority, limited fit\n"
            "  1-2:  No meaningful design signals — skip\n\n"
            "Return ONLY valid JSON:\n"
            '{"summary": "3-5 sentence strategic assessment of fit as a creative services / enterprise software client", '
            '"icp_score": 7, '
            '"recommended_action": "reach_out|monitor|skip|research_more", '
            '"key_buying_signals": ["signal 1", "signal 2"], '
            '"strategic_notes": "specific outreach angle or product fit recommendation"}'
        ),
        user_content=context,
        output_key="synthesis",
    )

    state.profile.analyst_summary = extracted.get("summary", "")
    state.profile.icp_score = extracted.get("icp_score", 0)
    state.profile.recommended_action = extracted.get("recommended_action", "")
    state.profile.key_buying_signals = extracted.get("key_buying_signals", [])

    synthesis_log = SearchStepLog(
        company_name=name,
        phase="5.0_synthesis",
        query_or_url="[LLM synthesis - no search]",
        backend_used="llm",
        extracted_data=extracted,
        success=bool(extracted.get("summary")),
        timestamp=datetime.utcnow(),
    )
    state.search_logs.append(synthesis_log)
    state.is_done = True

    logger.info(
        f"[{name}] ✅ Done. ICP Score={extracted.get('icp_score')} "
        f"Action={extracted.get('recommended_action')}"
    )
    return state


# ── Backwards-compatibility alias (graph.py references phase4_synthesis) ──────
# The old phase4 is now split into phase4_automotive_signals + phase5_synthesis.
# graph.py must be updated to add the new node. This alias allows existing
# imports to remain valid during a phased migration.
phase4_synthesis = phase5_synthesis

