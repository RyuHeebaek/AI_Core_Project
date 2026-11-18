"""LangGraph StateGraph 조립: 분석 → 계획 → 실행 → 검증(재계획 루프) → 응답 (F3005~F3011, F3021)."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agents.planner import analyze_node, plan_node
from app.agents.responder import responder_node
from app.agents.validator import validator_node
from app.agents.worker import worker_node
from app.models.state import AgentState


def _route_after_analyze(state: AgentState) -> str:
    if state.get("mode") == "general_chat" or state.get("clarification_needed"):
        return "responder"
    return "planner_plan"


def _route_after_plan(state: AgentState) -> str:
    if not state.get("task_list"):
        return "responder"
    return "worker_execute"


def _route_after_validate(state: AgentState) -> str:
    val = state.get("validation_result") or {}
    if val.get("passed") or val.get("fatal") or state.get("fatal_error"):
        return "responder"
    replan_count = int(state.get("replan_count", 0))
    max_replan = int(state.get("max_replan", 3))
    if replan_count < max_replan:
        return "planner_plan"
    return "responder"


def build_diet_planner_graph():
    """분석 → 계획 → 실행 → 검증 → 응답 StateGraph를 컴파일해 반환한다."""
    graph = StateGraph(AgentState)
    graph.add_node("planner_analyze", analyze_node)
    graph.add_node("planner_plan", plan_node)
    graph.add_node("worker_execute", worker_node)
    graph.add_node("validator_check", validator_node)
    graph.add_node("responder", responder_node)

    graph.set_entry_point("planner_analyze")
    graph.add_conditional_edges(
        "planner_analyze",
        _route_after_analyze,
        {"planner_plan": "planner_plan", "responder": "responder"},
    )
    graph.add_conditional_edges(
        "planner_plan",
        _route_after_plan,
        {"worker_execute": "worker_execute", "responder": "responder"},
    )
    graph.add_edge("worker_execute", "validator_check")
    graph.add_conditional_edges(
        "validator_check",
        _route_after_validate,
        {"planner_plan": "planner_plan", "responder": "responder"},
    )
    graph.add_edge("responder", END)
    return graph.compile()


diet_planner_graph = build_diet_planner_graph()
