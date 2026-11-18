"""LoggingSubAgent: EventBus 구독 로그 수집(무 LLM) 및 F3020 이상 징후 판정/진단."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.agents.llm_client import safe_llm_invoke
from app.config import get_settings, redact_secrets
from app.database import get_conn, utc_now_iso
from app.events.event_bus import AgentEvent, EventBus, event_bus


class LoggingSubAgent:
    """실행 이벤트를 세션별로 저장(LLM 미사용)하고 F3020 이상 징후를 판정한다."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self._attached_bus: EventBus | None = None

    def attach(self, bus: EventBus = event_bus) -> None:
        """EventBus에 구독자로 연결한다."""
        bus.subscribe(self.on_event)
        self._attached_bus = bus

    def detach(self, bus: EventBus = event_bus) -> None:
        """EventBus 구독을 해제한다 (격리 테스트용)."""
        bus.unsubscribe(self.on_event)
        if self._attached_bus is bus:
            self._attached_bus = None

    def on_event(self, event: AgentEvent) -> None:
        """수신한 이벤트를 LLM 호출 없이 즉시 SQLite에 순서대로 저장한다 (F3013)."""
        sanitized_payload = json.loads(redact_secrets(json.dumps(event.payload, ensure_ascii=False, default=str)))
        with get_conn(self.db_path, immediate=True) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM execution_logs WHERE session_id = ? AND run_id = ?",
                (event.session_id, event.run_id),
            ).fetchone()
            next_seq = int(row["max_seq"]) + 1 if row else 1
            conn.execute(
                """
                INSERT INTO execution_logs
                    (session_id, run_id, seq, event_type, agent_name, node_name, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.session_id,
                    event.run_id,
                    next_seq,
                    event.event_type,
                    event.agent_name,
                    event.node_name,
                    json.dumps(sanitized_payload, ensure_ascii=False),
                    event.created_at,
                ),
            )

    def get_session_events(self, session_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
        """세션의 실행 로그를 발생 순서대로 조회한다 (F3014)."""
        with get_conn(self.db_path) as conn:
            if run_id:
                rows = conn.execute(
                    "SELECT * FROM execution_logs WHERE session_id = ? AND run_id = ? ORDER BY id ASC",
                    (session_id, run_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM execution_logs WHERE session_id = ? ORDER BY id ASC",
                    (session_id,),
                ).fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "run_id": r["run_id"],
                "seq": r["seq"],
                "event_type": r["event_type"],
                "agent_name": r["agent_name"],
                "node_name": r["node_name"],
                "payload": json.loads(r["payload_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def evaluate_session(self, session_id: str, run_id: str | None = None, use_llm_diagnosis: bool = True) -> dict[str, Any]:
        """F3020: 규칙 기반 이상 징후 판정(무 LLM) 후 이상 발견 시에만 LLM 3문장 진단."""
        events = self.get_session_events(session_id, run_id)
        if not events:
            report = {
                "session_id": session_id,
                "run_id": run_id,
                "status": "판정 불가",
                "warnings": [],
                "diagnosis": None,
                "llm_called": False,
                "reason": "근거가 되는 실행 이벤트가 없어 판정 불가로 기록합니다.",
            }
            self._save_report(report)
            return report

        target_run_id = run_id or events[-1]["run_id"]
        run_events = [e for e in events if e["run_id"] == target_run_id]
        warnings: list[dict[str, str]] = []

        # 1) 동일 Tool 동일 입력 반복 호출 탐지
        tool_call_sigs: Counter[tuple[str, str]] = Counter()
        planned_tools: set[str] = set()
        replan_count = 0
        last_validation_passed: bool | None = None

        for ev in run_events:
            etype = ev["event_type"]
            payload = ev["payload"]
            if etype == "planner_worker_msg":
                for task in payload.get("task_list", []):
                    if task.get("tool_name"):
                        planned_tools.add(task["tool_name"])
            elif etype == "tool_call_start":
                if not payload.get("is_retry", False):
                    sig = (
                        str(payload.get("tool_name", "")),
                        json.dumps(payload.get("args", {}), sort_keys=True, ensure_ascii=False),
                    )
                    tool_call_sigs[sig] += 1
                if planned_tools and payload.get("tool_name") not in planned_tools:
                    warnings.append(
                        {
                            "code": "UNPLANNED_TOOL_CALL",
                            "message": f"계획(task_list)에 없던 Tool '{payload.get('tool_name')}' 호출이 감지되었습니다.",
                        }
                    )
            elif etype == "replan":
                replan_count += 1
            elif etype == "node_exit" and ev["node_name"] == "validator_check":
                last_validation_passed = payload.get("passed")

        for (tool_name, args_str), cnt in tool_call_sigs.items():
            if cnt >= 2:
                warnings.append(
                    {
                        "code": "DUPLICATE_TOOL_CALL",
                        "message": f"동일 Tool '{tool_name}'이(가) 같은 입력({args_str[:80]})으로 {cnt}회 반복 호출되었습니다.",
                    }
                )

        max_replan = get_settings().max_replan
        if replan_count >= max_replan:
            warnings.append(
                {
                    "code": "EXCESSIVE_REPLAN",
                    "message": f"재계획이 상한({replan_count}/{max_replan}회)에 도달했습니다.",
                }
            )

        if last_validation_passed is False:
            warnings.append(
                {
                    "code": "PLAN_RESULT_MISMATCH",
                    "message": "최종 검증(NutritionValidatorAgent)을 통과하지 못한 상태로 종료되었습니다.",
                }
            )

        has_anomaly = len(warnings) > 0
        status = "이상 탐지" if has_anomaly else "이상 없음"
        diagnosis: str | None = None
        llm_called = False

        # 정상 실행에는 절대 LLM을 호출하지 않는다 (F3020)
        if has_anomaly and use_llm_diagnosis:
            summary_lines = [
                f"- [{w['code']}] {w['message']}" for w in warnings
            ]
            tail_events = [
                f"{e['seq']}:{e['event_type']}:{e['node_name'] or e['agent_name']}"
                for e in run_events[-12:]
            ]
            prompt = (
                "다음 실행 로그 경고와 이벤트 요약을 보고 어느 Agent(Planner/Worker/Validator) 쪽 문제인지와 "
                "무엇을 고치면 되는지 한국어 3문장 이내로 진단하세요.\n"
                + "\n".join(summary_lines)
                + "\n최근 이벤트: "
                + ", ".join(tail_events)
            )
            diag_text, called, _ = safe_llm_invoke(
                prompt,
                system_prompt="너는 멀티 에이전트 실행 로그 진단가다. 반드시 3문장 이내로 핵심 원인과 해결책만 답한다.",
            )
            llm_called = called
            if diag_text:
                diagnosis = diag_text

        report = {
            "session_id": session_id,
            "run_id": target_run_id,
            "status": status,
            "has_anomaly": has_anomaly,
            "warnings": warnings,
            "diagnosis": diagnosis,
            "llm_called": llm_called,
            "event_count": len(run_events),
        }
        self._save_report(report)
        return report

    def _save_report(self, report: dict[str, Any]) -> None:
        with get_conn(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO anomaly_reports
                    (session_id, run_id, status, warnings_json, diagnosis, llm_called, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report["session_id"],
                    report.get("run_id"),
                    report["status"],
                    json.dumps(report.get("warnings", []), ensure_ascii=False),
                    report.get("diagnosis"),
                    1 if report.get("llm_called") else 0,
                    utc_now_iso(),
                ),
            )


logging_agent = LoggingSubAgent()
