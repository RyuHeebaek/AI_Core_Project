"""대화 및 프로필 역산 API (F3017)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from app.agents.graph import diet_planner_graph
from app.agents.logging_agent import logging_agent
from app.config import get_api_key_status, get_settings, redact_secrets
from app.database import load_profile, save_profile, save_session_context
from app.models.schemas import ChatRequest, ChatResponse, ProfilePayload
from app.tools.optimizer_tool import calculate_profile_targets

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest) -> ChatResponse:
    """사용자 대화 요청을 LangGraph 멀티 Agent 파이프라인으로 실행한다."""
    run_id = uuid.uuid4().hex[:12]
    settings = get_settings()

    initial_state = {
        "session_id": req.session_id,
        "run_id": run_id,
        "user_message": req.message,
        "profile": req.profile.model_dump() if req.profile else {},
        "replan_count": 0,
        "max_replan": settings.max_replan,
        "llm_calls_count": 0,
        "worker_llm_enabled": req.worker_llm_enabled,
        "execution_trace": [],
    }

    try:
        final_state = diet_planner_graph.invoke(initial_state)
    except Exception as exc:
        # NF3002, NF3003: 내부 오류 상세나 키를 UI에 노출하지 않고 서버 로그에만 기록
        logger.warning("Chat pipeline failed (run_id=%s): %s", run_id, redact_secrets(str(exc)))
        return ChatResponse(
            session_id=req.session_id,
            run_id=run_id,
            mode="cvs_instant",
            reply="요청 처리 중 일시적 오류가 발생했습니다. 입력 조건이나 API 키 설정을 확인한 뒤 다시 시도해 주세요.",
            api_status=get_api_key_status(),
            error_notice="요청 처리 중 서버 내부 오류가 발생했습니다.",
        )

    # F3010: Tool 원본 응답이 아닌 구조화 요약 맥락만 저장
    if final_state.get("profile"):
        save_profile(req.session_id, final_state["profile"])
    ctx_to_save: dict[str, Any] = {
        "mode": final_state.get("mode", "cvs_instant"),
        "profile": final_state.get("profile", {}),
        "base_conditions": final_state.get("base_conditions", {}),
        "preferences": {
            k: v for k, v in (final_state.get("preferences") or {}).items() if not k.startswith("explicit_")
        },
        "constraints": final_state.get("constraints", {}),
        "last_combo": (
            final_state.get("candidate_combo")
            if (final_state.get("validation_result") or {}).get("passed")
            and (final_state.get("candidate_combo") or {}).get("selected_items")
            else final_state.get("previous_combo", {})
        ),
    }
    save_session_context(req.session_id, ctx_to_save)

    # F3020: responder node_exit 이후 전체 실행 로그 기준으로 이상 징후 판정
    anomaly_report = logging_agent.evaluate_session(req.session_id, run_id, use_llm_diagnosis=True)
    trace = list(final_state.get("execution_trace") or [])
    if trace and trace[-1].get("step") == "responder":
        trace[-1] = {**trace[-1], "anomaly_status": anomaly_report.get("status")}

    fatal = final_state.get("fatal_error")
    return ChatResponse(
        session_id=req.session_id,
        run_id=run_id,
        mode=final_state.get("mode", "cvs_instant"),
        reply=final_state.get("final_response", ""),
        clarification_needed=bool(final_state.get("clarification_needed")),
        candidate_combo=final_state.get("candidate_combo") or {},
        validation_result=final_state.get("validation_result") or {},
        anomaly_report=anomaly_report,
        replan_count=int(final_state.get("replan_count", 0)),
        llm_calls_count=int(final_state.get("llm_calls_count", 0)),
        execution_trace=trace,
        api_status=get_api_key_status(),
        error_notice=fatal.get("message") if fatal else None,
    )


@router.get("/profiles/{session_id}")
def get_profile_endpoint(session_id: str) -> dict:
    """저장된 사용자 프로필과 목표 역산 결과를 조회한다."""
    prof = load_profile(session_id)
    if not prof:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    calc = calculate_profile_targets(prof) if prof.get("weight_kg") and prof.get("gender") else None
    return {"session_id": session_id, "profile": prof, "targets": calc}


@router.put("/profiles/{session_id}")
def upsert_profile_endpoint(session_id: str, payload: ProfilePayload) -> dict:
    """사용자 프로필을 저장하고 즉시 목표 영양소 역산 결과를 반환한다."""
    prof = payload.model_dump()
    save_profile(session_id, prof)
    calc = calculate_profile_targets(prof)
    return {"session_id": session_id, "profile": prof, "targets": calc}
