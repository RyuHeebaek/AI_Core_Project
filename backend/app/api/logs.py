"""실행 로그 목록·상세·삭제 및 이상 징후 분석 API (F3014, F3020). 수정(PUT/PATCH) API 없음."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException

from app.agents.logging_agent import logging_agent
from app.database import get_conn
from app.models.schemas import LogDetailResponse, LogSessionSummary

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("", response_model=list[LogSessionSummary])
def list_log_sessions() -> list[LogSessionSummary]:
    """세션 단위 실행 로그 목록을 단일 SQL 조인으로 조회한다 (F3014)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                e.session_id,
                COUNT(*) AS event_count,
                SUM(CASE WHEN e.event_type = 'replan' THEN 1 ELSE 0 END) AS replan_count,
                MAX(e.created_at) AS last_event_at,
                ar.status AS latest_anomaly_status
            FROM execution_logs e
            LEFT JOIN anomaly_reports ar
                ON ar.id = (
                    SELECT MAX(a2.id)
                    FROM anomaly_reports a2
                    WHERE a2.session_id = e.session_id
                )
            GROUP BY e.session_id
            ORDER BY MAX(e.id) DESC
            """
        ).fetchall()
    return [
        LogSessionSummary(
            session_id=r["session_id"],
            event_count=r["event_count"],
            replan_count=r["replan_count"] or 0,
            last_event_at=r["last_event_at"],
            latest_anomaly_status=r["latest_anomaly_status"],
        )
        for r in rows
    ]


@router.get("/{session_id}", response_model=LogDetailResponse)
def get_log_detail(session_id: str) -> LogDetailResponse:
    """특정 세션의 실행 로그를 발생 순서대로 조회한다 (F3014)."""
    events = logging_agent.get_session_events(session_id)
    if not events:
        raise HTTPException(status_code=404, detail="해당 세션의 실행 로그가 없습니다.")

    with get_conn() as conn:
        rep = conn.execute(
            "SELECT * FROM anomaly_reports WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()

    latest_anomaly = None
    if rep:
        latest_anomaly = {
            "status": rep["status"],
            "warnings": json.loads(rep["warnings_json"]),
            "diagnosis": rep["diagnosis"],
            "llm_called": bool(rep["llm_called"]),
            "created_at": rep["created_at"],
        }
    return LogDetailResponse(session_id=session_id, events=events, latest_anomaly=latest_anomaly)


@router.post("/{session_id}/analyze")
def analyze_session_logs(session_id: str) -> dict[str, Any]:
    """F3020: 규칙 기반 이상 징후 판정 및 이상 시 LLM 3문장 원인 진단을 실행한다."""
    return logging_agent.evaluate_session(session_id, use_llm_diagnosis=True)


@router.delete("/{session_id}")
def delete_session_logs(session_id: str) -> dict[str, Any]:
    """특정 세션의 실행 로그와 이상 진단 기록을 삭제한다 (F3014)."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM execution_logs WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM anomaly_reports WHERE session_id = ?", (session_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="삭제할 실행 로그가 없습니다.")
    return {"deleted": True, "session_id": session_id, "deleted_events": cur.rowcount}
