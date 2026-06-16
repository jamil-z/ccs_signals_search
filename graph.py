"""
graph.py — LangGraph orchestration for the ICP Search Pipeline.

Graph topology (per company):
  START → phase1 → phase2 → phase3 → phase4 → END

Each node is one phase function from pipeline_phases.py.
The graph runs sequentially but multiple companies run concurrently
via asyncio.gather() in main.py.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from pipeline_phases import (
    phase1_identity,
    phase2_scale,
    phase3_signals,
    phase4_synthesis,
)
from schemas import AgentState, CompanyProfile

logger = logging.getLogger(__name__)


# ── Node wrappers (LangGraph expects dict updates, not full state replacement) ─

async def node_phase1(state: AgentState) -> dict:
    updated = await phase1_identity(state)
    return {
        "profile": updated.profile,
        "search_logs": updated.search_logs,
        "current_phase": updated.current_phase,
    }


async def node_phase2(state: AgentState) -> dict:
    updated = await phase2_scale(state)
    return {
        "profile": updated.profile,
        "search_logs": updated.search_logs,
        "current_phase": updated.current_phase,
    }


async def node_phase3(state: AgentState) -> dict:
    updated = await phase3_signals(state)
    return {
        "profile": updated.profile,
        "search_logs": updated.search_logs,
        "current_phase": updated.current_phase,
    }


async def node_phase4(state: AgentState) -> dict:
    updated = await phase4_synthesis(state)
    return {
        "profile": updated.profile,
        "search_logs": updated.search_logs,
        "is_done": updated.is_done,
    }


# ── Build the graph ───────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Construct and compile the ICP search pipeline graph.

    Returns a compiled LangGraph that accepts an AgentState dict
    and processes a single company through all 4 phases.
    """
    builder = StateGraph(AgentState)

    # Register nodes
    builder.add_node("phase1_identity", node_phase1)
    builder.add_node("phase2_scale", node_phase2)
    builder.add_node("phase3_signals", node_phase3)
    builder.add_node("phase4_synthesis", node_phase4)

    # Sequential edges
    builder.add_edge(START, "phase1_identity")
    builder.add_edge("phase1_identity", "phase2_scale")
    builder.add_edge("phase2_scale", "phase3_signals")
    builder.add_edge("phase3_signals", "phase4_synthesis")
    builder.add_edge("phase4_synthesis", END)

    return builder.compile()


# ── Per-company entry point ───────────────────────────────────────────────────

async def run_company(company_name: str) -> AgentState:
    """
    Run the full ICP pipeline for a single company.

    Parameters
    ----------
    company_name : str
        The company name to research (e.g. "Moodbit", "Salesforce").

    Returns
    -------
    AgentState with profile and search_logs fully populated.
    """
    logger.info(f"🔍 Starting pipeline for: {company_name}")

    graph = build_graph()

    initial_state = AgentState(
        company_name=company_name,
        profile=CompanyProfile(company_name=company_name),
        search_logs=[],
        messages=[],
        current_phase="1.1",
        is_done=False,
    )

    try:
        final_state_dict = await graph.ainvoke(initial_state)
        # LangGraph returns a dict; convert back to AgentState
        if isinstance(final_state_dict, dict):
            final_state = AgentState(**final_state_dict)
        else:
            final_state = final_state_dict
        logger.info(f"✅ Pipeline complete for: {company_name}")
        return final_state
    except Exception as exc:
        logger.error(f"❌ Pipeline failed for {company_name}: {exc}")
        initial_state.profile.processing_errors.append(f"Pipeline error: {exc}")
        initial_state.is_done = True
        return initial_state
