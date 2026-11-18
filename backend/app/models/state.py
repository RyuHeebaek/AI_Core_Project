"""LangGraph AgentState 및 에이전트 간 교환 구조 정의."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

ModeType = Literal[
    "online_bulk",
    "cvs_instant",
    "cheating_recovery",
    "item_swap",
    "profile_calc",
    "general_chat",
]


class AgentState(TypedDict, total=False):
    """Planner · Worker · Validator · Responder 간 공유 상태."""

    session_id: str
    run_id: str
    user_message: str
    mode: ModeType

    profile: dict[str, Any]
    base_conditions: dict[str, Any]
    preferences: dict[str, Any]
    constraints: dict[str, Any]

    clarification_needed: bool
    clarification_question: str
    missing_fields: list[str]

    task_list: list[dict[str, Any]]
    worker_results: dict[str, Any]
    worker_escalations: list[dict[str, Any]]

    # F3009: 재계획 시 이미 조회한 Tool 결과 캐시 재사용
    nutrition_cache: dict[str, Any]
    shopping_cache: dict[str, Any]
    store_cache: dict[str, Any]

    candidate_combo: dict[str, Any]
    validation_result: dict[str, Any]
    anomaly_report: dict[str, Any]

    replan_count: int
    max_replan: int
    fatal_error: dict[str, Any] | None

    execution_trace: list[dict[str, Any]]
    final_response: str
    llm_calls_count: int
    worker_llm_enabled: bool
    previous_combo: dict[str, Any]
