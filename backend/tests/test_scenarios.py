"""명세서 4.7절 대표 시나리오 5종 통합 검증 스크립트 (pytest 및 단독 실행 지원)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _setup_isolated_db(tmp_dir: Path) -> str:
    db_file = str(tmp_dir / "scenarios.db")
    os.environ["DB_PATH"] = db_file
    os.environ["USE_MOCK"] = "true"
    os.environ.setdefault("OPENAI_API_KEY", "")
    os.environ.setdefault("DATA_GO_KR_API_KEY", "")
    os.environ.setdefault("KAKAO_REST_API_KEY", "")
    os.environ["MAX_REPLAN"] = "3"
    os.environ["MAX_WORKER_RETRY"] = "2"

    from app.agents.logging_agent import logging_agent
    from app.database import init_db
    from app.events.event_bus import event_bus
    from app.tools.price_tool import clear_price_caches

    clear_price_caches()
    logging_agent.db_path = db_file
    init_db(db_file)
    event_bus.clear()
    logging_agent.attach(event_bus)
    return db_file


def _summarize(label: str, data: dict[str, Any], client: TestClient) -> None:
    sid = data["session_id"]
    logs_res = client.get(f"/api/logs/{sid}")
    event_cnt = len(logs_res.json()["events"]) if logs_res.status_code == 200 else 0
    steps = [t["step"] for t in data.get("execution_trace", [])]
    print(
        f"[{label}] mode={data['mode']} | passed={data.get('validation_result', {}).get('passed')} "
        f"| replan={data['replan_count']} | llm_calls={data['llm_calls_count']} "
        f"| log_events={event_cnt} | anomaly={data.get('anomaly_report', {}).get('status')} "
        f"| trace={' -> '.join(steps)}"
    )


def run_all_scenarios(export_dir: Path | None = None) -> list[dict[str, Any]]:
    """5가지 대표 시나리오 및 F3019 무키 안전 처리를 순차 실행하고 결과 요약을 반환한다."""
    import app.agents.logging_agent as logging_agent_mod
    from app.main import app
    from app.tools import TOOL_REGISTRY
    from app.tools.base import ok

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        _setup_isolated_db(Path(tmp))
        from app.agents.logging_agent import logging_agent
        from app.events.event_bus import AgentEvent, event_bus

        try:
            client = TestClient(app)

            # 1) 제약을 한 번에 충족하는 요청
            r1 = client.post("/api/chat", json={"session_id": "sc1", "message": "강남역 근처 편의점 8000원 이하 고단백 한끼 추천해줘"})
            d1 = r1.json()
            assert r1.status_code == 200
            assert d1["replan_count"] == 0 and d1["validation_result"]["passed"] is True
            _summarize("Scenario 1: 단번 통과", d1, client)
            results.append(d1)

            # 2) 재계획이 일어나는 요청 (1차 나트륨 상한 초과 -> Validator 반려 -> Replan 성공)
            orig_opt_spec = TOOL_REGISTRY["optimize_meal_combo"]
            calls = {"n": 0}

            def _high_sodium_once(**kwargs: Any) -> dict[str, Any]:
                calls["n"] += 1
                if calls["n"] == 1:
                    bad_item = {
                        "food_id": "BAD_NA",
                        "name": "초고염 닭가슴살 도시락",
                        "category": "main",
                        "serving_g": 300.0,
                        "kcal": 380.0,
                        "protein_g": 32.0,
                        "carbs_g": 45.0,
                        "fat_g": 6.0,
                        "sugar_g": 3.0,
                        "sodium_mg": 1680.0,
                        "unit_price_krw": 5500,
                        "unit_basis": "1개",
                        "protein_density_pct": 33.7,
                        "protein_per_1000krw": 5.82,
                    }
                    return ok(
                        {
                            "selected_items": [bad_item],
                            "nutrition_summary": {"total_kcal": 380.0, "total_protein_g": 32.0, "total_carbs_g": 45.0, "total_fat_g": 6.0, "total_sugar_g": 3.0, "total_sodium_mg": 1680.0, "total_price_krw": 5500},
                            "total_price_krw": 5500,
                            "excluded_candidates": [],
                            "excluded_items": [],
                        }
                    )
                return orig_opt_spec.fn(**kwargs)

            TOOL_REGISTRY["optimize_meal_combo"] = replace(orig_opt_spec, fn=_high_sodium_once)
            try:
                r2 = client.post("/api/chat", json={"session_id": "sc2", "message": "강남역 근처 편의점 8000원 이하 고단백 한끼 추천해줘"})
                d2 = r2.json()
            finally:
                TOOL_REGISTRY["optimize_meal_combo"] = orig_opt_spec

            assert r2.status_code == 200
            assert d2["replan_count"] >= 1 and d2["validation_result"]["passed"] is True
            assert any(t.get("step") == "replan" for t in d2["execution_trace"])
            _summarize("Scenario 2: 재계획 후 성공", d2, client)
            results.append(d2)

            # 3) 재계획 상한(3회) 도달 요청 (불가능 예산/단백질 -> 대안 제시 종료)
            r3 = client.post("/api/chat", json={"session_id": "sc3", "message": "강남역 편의점 2000원 이하 단백질 60g 고단백 한끼"})
            d3 = r3.json()
            assert r3.status_code == 200
            assert d3["replan_count"] == 3 and d3["validation_result"]["passed"] is False
            assert "예산 상한 상향" in d3["reply"] and "최소 단백질 완화" in d3["reply"]
            _summarize("Scenario 3: 재계획 상한 3회", d3, client)
            results.append(d3)

            # 4) Tool 하나 실패 시(NF3003) 사유 남기고 나머지 추천 완료
            r4 = client.post("/api/chat", json={"session_id": "sc4", "message": "무인도 근처 편의점 8000원 이하 고단백 한끼 추천해줘"})
            d4 = r4.json()
            assert r4.status_code == 200
            assert d4["validation_result"]["passed"] is True
            assert d4["candidate_combo"]["selected_items"]
            assert "무인도" in d4["reply"] and "보조 조회 제한 안내" in d4["reply"]
            _summarize("Scenario 4: 부분 Tool 실패 복구", d4, client)
            results.append(d4)

            # 5) Tool 불필요 일반 대화 요청
            r5 = client.post("/api/chat", json={"session_id": "sc5", "message": "안녕, 너 어떤 기능 있어?"})
            d5 = r5.json()
            assert r5.status_code == 200
            assert d5["mode"] == "general_chat"
            assert not any(t.get("step") == "tool_call" for t in d5["execution_trace"])
            _summarize("Scenario 5: Tool 미호출 일반응답", d5, client)
            results.append(d5)

            # F3019/NF3003: USE_MOCK=false + API 키 누락 시 서버 비정상 종료 없이 MISSING_API_KEY 반환
            prev_mock = os.environ.get("USE_MOCK", "true")
            os.environ["USE_MOCK"] = "false"
            try:
                r_nokey = client.post("/api/chat", json={"session_id": "sc_nokey", "message": "강남역 편의점 8000원 이하 한끼"})
                d_nokey = r_nokey.json()
                assert r_nokey.status_code == 200
                assert "MISSING_API_KEY" in d_nokey["reply"]
                assert "MISSING_API_KEY" in (d_nokey.get("error_notice") or "")
                print(f"[F3019: 무키 안전응답] status={r_nokey.status_code} | error_notice={d_nokey['error_notice']}")
            finally:
                os.environ["USE_MOCK"] = prev_mock

            if export_dir is not None:
                export_dir.mkdir(parents=True, exist_ok=True)
                full_log = client.get("/api/logs/sc1").json()
                full_log["chat_response_summary"] = {
                    "mode": d1["mode"],
                    "replan_count": d1["replan_count"],
                    "llm_calls_count": d1["llm_calls_count"],
                    "validation_result": d1["validation_result"],
                }
                (export_dir / "full_execution_log.json").write_text(
                    json.dumps(full_log, ensure_ascii=False, indent=2), encoding="utf-8"
                )

                replan_log = client.get("/api/logs/sc2").json()
                replan_log["chat_response_summary"] = {
                    "mode": d2["mode"],
                    "replan_count": d2["replan_count"],
                    "llm_calls_count": d2["llm_calls_count"],
                    "validation_result": d2["validation_result"],
                }
                (export_dir / "replan_execution_log.json").write_text(
                    json.dumps(replan_log, ensure_ascii=False, indent=2), encoding="utf-8"
                )

                # F3020 이상 징후 + LLM 원인 진단 로그 샘플 추출
                event_bus.publish(AgentEvent("sc_anomaly", "run_anom", "node_enter", "PlannerAgent", "planner_plan", {"mode": "cvs_instant"}))
                event_bus.publish(AgentEvent("sc_anomaly", "run_anom", "planner_worker_msg", "PlannerAgent", "planner_plan", {"task_list": [{"task_id": "t1", "tool_name": "search_nutrition_db"}]}))
                event_bus.publish(AgentEvent("sc_anomaly", "run_anom", "tool_call_start", "WorkerAgent", "worker_execute", {"tool_name": "search_nutrition_db", "args": {"query": "닭가슴살"}}))
                event_bus.publish(AgentEvent("sc_anomaly", "run_anom", "tool_call_start", "WorkerAgent", "worker_execute", {"tool_name": "search_nutrition_db", "args": {"query": "닭가슴살"}}))
                event_bus.publish(AgentEvent("sc_anomaly", "run_anom", "tool_call_start", "WorkerAgent", "worker_execute", {"tool_name": "search_nearby_store", "args": {"location": "강남역"}}))
                for idx in range(3):
                    event_bus.publish(AgentEvent("sc_anomaly", "run_anom", "replan", "PlannerAgent", "planner_plan", {"replan_count": idx + 1, "reason": "BUDGET_EXCEEDED: 예산 초과"}))
                event_bus.publish(AgentEvent("sc_anomaly", "run_anom", "node_exit", "NutritionValidatorAgent", "validator_check", {"passed": False, "stage1_errors": ["BUDGET_EXCEEDED: 예산 초과"]}))

                orig_llm = logging_agent_mod.safe_llm_invoke
                if not os.getenv("OPENAI_API_KEY"):
                    logging_agent_mod.safe_llm_invoke = lambda prompt, system_prompt="": (
                        "WorkerAgent가 동일 파라미터로 search_nutrition_db를 중복 호출하고 계획 외 search_nearby_store를 실행했으며, PlannerAgent가 예산 불가 조건에서 재계획 상한(3회)에 도달했습니다. "
                        "Worker 캐시 키 검증과 Planner의 task_list·예산 사전 가드레일을 점검하세요.",
                        True,
                        None,
                    )
                try:
                    client.post("/api/logs/sc_anomaly/analyze")
                finally:
                    logging_agent_mod.safe_llm_invoke = orig_llm

                anom_log = client.get("/api/logs/sc_anomaly").json()
                (export_dir / "anomaly_diagnosis_log.json").write_text(
                    json.dumps(anom_log, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        finally:
            logging_agent.detach(event_bus)

    return results


def test_five_representative_scenarios():
    """pytest용 대표 시나리오 5종 및 F3019 무키 처리 검증 진입점."""
    results = run_all_scenarios()
    assert len(results) == 5


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parents[2]
    run_all_scenarios(export_dir=project_root / "logs_sample")
    print("\n모든 대표 시나리오 5종 검증 및 logs_sample/ JSON 추출을 완료했습니다.")
