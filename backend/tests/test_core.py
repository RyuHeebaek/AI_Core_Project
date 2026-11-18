"""핵심 도메인, Tool, LangGraph 멀티 Agent, 로깅/이상탐지, API 통합 테스트 (F3001~F3021, NF3001~NF3004)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """각 테스트마다 독립된 임시 SQLite DB와 안전한 Mock 모드를 사용한다."""
    db_file = tmp_path / "test_diet.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("USE_MOCK", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "")
    monkeypatch.setenv("KAKAO_REST_API_KEY", "")
    monkeypatch.setenv("MAX_REPLAN", "3")
    monkeypatch.setenv("MAX_WORKER_RETRY", "2")

    from app.agents.logging_agent import logging_agent
    from app.database import init_db
    from app.events.event_bus import event_bus
    from app.tools.price_tool import clear_price_caches

    clear_price_caches()
    logging_agent.db_path = str(db_file)
    init_db(str(db_file))
    event_bus.clear()
    logging_agent.attach(event_bus)
    yield
    logging_agent.detach(event_bus)


def test_tool_specs_and_failure_contracts(monkeypatch):
    """F3001~F3004: Tool 명세, 공통 실패 포맷, 영양 누락 미보간, 단위 파싱, 모호 위치 검증."""
    from app.tools import list_tool_specs
    from app.tools.local_tool import search_nearby_store
    from app.tools.nutrition_tool import search_nutrition_db
    from app.tools.price_tool import parse_bundle_unit, search_product_price

    specs = list_tool_specs()
    names = {s["name"] for s in specs}
    assert names == {"search_nutrition_db", "search_product_price", "search_nearby_store", "optimize_meal_combo"}

    # F3003: 필수 영양성분 누락 시 임의로 0 채우지 않고 실패/제외
    res_missing = search_nutrition_db("미상영양식품")
    assert res_missing["ok"] is False
    assert res_missing["error"]["code"] == "MISSING_NUTRIENT_FIELDS"

    # 묶음 단가 파싱 검증 (10+10, 10팩+10팩, kg 환산, 실패 케이스 포함)
    u1 = parse_bundle_unit("허닭 스팀 닭가슴살 100g 30팩")
    assert u1["parsed_ok"] is True and u1["servings_per_purchase"] == 30.0
    u2 = parse_bundle_unit("오트밀 롤드오츠 1kg")
    assert u2["parsed_ok"] is True and u2["servings_per_purchase"] == 10.0
    u3 = parse_bundle_unit("스팀 닭가슴살 100g 10팩 + 10팩")
    assert u3["parsed_ok"] is True and u3["servings_per_purchase"] == 20.0
    u4 = parse_bundle_unit("스팀 닭가슴살 100g 10+10")
    assert u4["parsed_ok"] is True and u4["servings_per_purchase"] == 20.0
    u_fail = search_product_price("파싱불가")
    assert u_fail["ok"] is False and u_fail["error"]["code"] == "UNIT_PARSE_FAILED"

    # 모호 위치 및 API 키 누락 테스트 (NF3002)
    amb = search_nearby_store("중앙역")
    assert amb["ok"] is False and amb["error"]["code"] == "AMBIGUOUS_LOCATION"

    monkeypatch.setenv("USE_MOCK", "false")
    no_key_store = search_nearby_store("강남역")
    assert no_key_store["ok"] is False and no_key_store["error"]["code"] == "MISSING_API_KEY"
    no_key_price = search_product_price("닭가슴살")
    assert no_key_price["ok"] is False and no_key_price["error"]["code"] == "MISSING_API_KEY"
    no_key_nut = search_nutrition_db("닭가슴살")
    assert no_key_nut["ok"] is False and no_key_nut["error"]["code"] == "MISSING_API_KEY"


def test_profile_guardrail_and_mode_c_math():
    """목표 역산 안전 하한선 가드레일 및 모드 C 치팅 복구 수식 검증."""
    from app.tools.optimizer_tool import calculate_mode_c_targets, calculate_profile_targets

    # 남성 70kg -> 50kg 4주 극단 감량: 하한선 1400kcal 경고 + 권장 기간 역산
    extreme = calculate_profile_targets(
        {"gender": "male", "weight_kg": 70, "target_weight_kg": 50, "target_weeks": 4, "goal_type": "diet", "meals_per_day": 3}
    )
    assert extreme["guardrail_triggered"] is True
    assert extreme["target_daily_kcal"] == 1400.0
    assert extreme["recommended_weeks"] and extreme["recommended_weeks"] > 4

    # 모드 C: 초과 칼로리 복구 + 최소 단백질 1.6g/kg 고정
    rec = calculate_mode_c_targets(excess_kcal=1200, remain_days=3, base_daily_kcal=2000, weight_kg=75, gender="male", meals_per_day=3)
    assert rec["target_daily_kcal"] == 1600.0
    assert rec["min_daily_protein_g"] == 120.0


def test_validator_stage1_and_stage2():
    """NutritionValidatorAgent 1단계 수학 검증 & 2단계 상식 오류(INVALID_FOOD_MATCH) 차단."""
    from app.agents.validator import _stage1_deterministic, _stage2_semantic_check

    bad_lactose_combo = {
        "selected_items": [
            {"name": "무가당 그릭요거트 100g", "category": "sub", "kcal": 95, "protein_g": 10, "carbs_g": 4.5, "fat_g": 4, "sugar_g": 3.5, "unit_price_krw": 2600, "has_lactose": True, "has_caffeine": False}
        ]
    }
    ok1, errs1, _ = _stage1_deterministic(bad_lactose_combo, {"lactose_free": True, "budget_krw": 5000}, {}, "cvs_instant")
    assert ok1 is False and any("유당" in e for e in errs1)

    snack_as_main = {
        "selected_items": [
            {"name": "초콜릿 과자 세트", "category": "main", "kcal": 500, "protein_g": 4, "carbs_g": 65, "fat_g": 24, "sugar_g": 35, "unit_price_krw": 3000}
        ]
    }
    ok2, errs2, _ = _stage2_semantic_check(snack_as_main, "cvs_instant")
    assert ok2 is False and any("INVALID_FOOD_MATCH" in e for e in errs2)


def test_chat_api_modes_memory_replan_and_swap():
    """대화 API: 일반대화, 누락 되묻기, 모드 A/B/C, 맥락 유지(F3010), 1:1 스왑, 재계획 상한(NF3004)."""
    from app.main import app

    client = TestClient(app)

    # 1) Tool 불필요 일반 대화 (F3006)
    r_chat = client.post("/api/chat", json={"session_id": "s1", "message": "안녕 사용법 알려줘"})
    assert r_chat.status_code == 200
    assert r_chat.json()["mode"] == "general_chat"
    assert not any(t["step"] == "tool_call" for t in r_chat.json()["execution_trace"])

    # 2) 필수 조건 누락 시 Tool 호출 전 되묻기 (F3005)
    r_clarify = client.post("/api/chat", json={"session_id": "s2", "message": "편의점 식단 추천해줘"})
    d_clarify = r_clarify.json()
    assert d_clarify["clarification_needed"] is True
    assert not any(t["step"] == "tool_call" for t in d_clarify["execution_trace"])

    # 3) 모드 B(cvs_instant) 정상 실행 + 0 LLM 호출 + 편의점 기준가 안내 확인 (F3008, F3021)
    r_b = client.post("/api/chat", json={"session_id": "s3", "message": "강남역 근처 편의점 8000원 이하 고단백 한끼 추천해줘"})
    d_b = r_b.json()
    assert d_b["mode"] == "cvs_instant"
    assert d_b["validation_result"]["passed"] is True
    assert d_b["llm_calls_count"] == 0
    assert "전문 영양사의 조언을 대신하지 않습니다" in d_b["reply"]
    assert "1+1/2+1 행사" in d_b["reply"]
    assert d_b["candidate_combo"]["stores"]

    # 4) F3010 후속 맥락 유지: 18시 이후 + 유당불내증 + 저당 조건만 추가
    r_b2 = client.post("/api/chat", json={"session_id": "s3", "message": "저녁 7시 이후로 먹고 유당불내증 제외 저당으로 바꿔줘"})
    d_b2 = r_b2.json()
    assert d_b2["validation_result"]["passed"] is True
    for it in d_b2["candidate_combo"]["selected_items"]:
        assert not it.get("has_lactose")
        assert not it.get("has_caffeine")
    assert d_b2["candidate_combo"]["nutrition_summary"]["total_sugar_g"] <= 10.0

    # 5) 1:1 품목 스왑 (item_swap) 및 스왑 이후 후속 턴 복귀 확인
    first_sub = next((i["name"] for i in d_b2["candidate_combo"]["selected_items"] if i["category"] == "sub"), None)
    if first_sub:
        keyword = "계란" if "계란" in first_sub else "두부바" if "두부바" in first_sub else "아몬드"
        r_swap = client.post("/api/chat", json={"session_id": "s3", "message": f"{keyword} 빼고 다른 걸로 바꿔줘"})
        d_swap = r_swap.json()
        assert d_swap["mode"] == "item_swap"
        assert d_swap["validation_result"]["passed"] is True
        r_after_swap = client.post("/api/chat", json={"session_id": "s3", "message": "저당 무카페인으로 다시 추천해줘"})
        assert r_after_swap.json()["mode"] == "cvs_instant"
        assert r_after_swap.json()["validation_result"]["passed"] is True

    # 6) 모드 A(online_bulk) 대량 장바구니 + 저장 프로필(75kg 2끼) + bulk 스왑
    client.put("/api/profiles/s4", json={"gender": "male", "weight_kg": 75, "target_weight_kg": 70, "target_weeks": 4, "goal_type": "diet", "meals_per_day": 3})
    r_a = client.post("/api/chat", json={"session_id": "s4", "message": "4주 하루 2끼 15만원 이하 온라인 대량 장바구니 짜줘"})
    d_a = r_a.json()
    assert d_a["mode"] == "online_bulk"
    assert d_a["validation_result"]["passed"] is True
    assert d_a["candidate_combo"]["nutrition_summary"]["total_protein_g"] >= 51.0
    assert d_a["candidate_combo"]["shopping_summary"]["type"] == "online_bulk"
    second_item = d_a["candidate_combo"]["selected_items"][1]["name"]
    swap_kw = "계란" if "계란" in second_item else "오트밀" if "오트밀" in second_item else "현미"
    r_a_swap = client.post("/api/chat", json={"session_id": "s4", "message": f"{swap_kw} 빼고 다른 걸로 바꿔줘"})
    assert r_a_swap.json()["mode"] == "item_swap"
    assert r_a_swap.json()["validation_result"]["passed"] is True
    assert r_a_swap.json()["candidate_combo"]["shopping_summary"]["type"] == "online_bulk"

    # 7) 모드 C(cheating_recovery) 치팅데이 복구 — 성별 누락 시 되묻기, 포함 시 실제 복구 역산 검증
    r_c_missing = client.post(
        "/api/chat",
        json={"session_id": "s5_missing", "message": "체중 75kg인데 어제 치팅으로 1200kcal 초과했어 3일 복구 식단 짜줘"},
    )
    assert r_c_missing.json()["clarification_needed"] is True
    r_c = client.post(
        "/api/chat",
        json={"session_id": "s5", "message": "남성 체중 75kg인데 어제 치팅으로 1200kcal 초과했어 3일 복구 식단 짜줘"},
    )
    d_c = r_c.json()
    assert d_c["mode"] == "cheating_recovery"
    assert d_c["validation_result"]["passed"] is True
    assert d_c["candidate_combo"]["reverse_calc"]["mode_c_recovery"]["min_daily_protein_g"] == 120.0

    # 8) 재계획 상한 도달 테스트 (NF3004): 500원 예산 불가
    r_cap = client.post("/api/chat", json={"session_id": "s6", "message": "강남역 편의점 500원 이하 고단백 한끼"})
    d_cap = r_cap.json()
    assert d_cap["validation_result"]["passed"] is False
    assert d_cap["replan_count"] == 3


def test_plans_crud_and_overlap_warning():
    """F3015, F3016: 식단 계획 CRUD 및 동일 날짜 시간 중복 경고(저장 차단 안 함)."""
    from app.main import app

    client = TestClient(app)

    p1 = client.post(
        "/api/plans",
        json={
            "session_id": "s1",
            "title": "점심 닭가슴살 세트",
            "mode": "cvs_instant",
            "plan_date": "2026-11-20",
            "start_time": "12:00",
            "end_time": "13:00",
            "items": [{"name": "스팀 닭가슴살 100g"}],
            "nutrition_summary": {"total_kcal": 250, "total_protein_g": 35},
            "total_price_krw": 5300,
        },
    )
    assert p1.status_code == 201
    assert p1.json()["overlap_warnings"] == []
    p1_id = p1.json()["plan"]["id"]

    # 겹치는 시간대 저장 -> 저장 성공(201) + overlap_warnings 반환
    p2 = client.post(
        "/api/plans",
        json={
            "session_id": "s1",
            "title": "중복 테스트 식단",
            "mode": "cvs_instant",
            "plan_date": "2026-11-20",
            "start_time": "12:30",
            "end_time": "13:30",
            "items": [],
            "nutrition_summary": {},
            "total_price_krw": 4000,
        },
    )
    assert p2.status_code == 201
    assert len(p2.json()["overlap_warnings"]) == 1

    # PUT / PATCH 수정 & 삭제
    upd = client.put(f"/api/plans/{p1_id}", json={"title": "수정된 식단"})
    assert upd.status_code == 200 and upd.json()["plan"]["title"] == "수정된 식단"
    upd2 = client.patch(f"/api/plans/{p1_id}", json={"title": "패치된 식단"})
    assert upd2.status_code == 200 and upd2.json()["plan"]["title"] == "패치된 식단"
    assert client.delete(f"/api/plans/{p1_id}").status_code == 200


def test_logging_agent_isolation_no_update_api_and_f3020():
    """F3013, F3014, F3020: 로깅 Agent 분리 동작, 수정 API 부재, 이상 판정 및 판정 불가 검증."""
    from app.agents.logging_agent import logging_agent
    from app.events.event_bus import AgentEvent, event_bus
    from app.main import app

    client = TestClient(app)

    # 1) 로깅 Agent 미연결 상태에서도 메인 대화 정상 동작 확인 (F3013)
    logging_agent.detach(event_bus)
    r_detached = client.post("/api/chat", json={"session_id": "s_detached", "message": "강남역 8000원 이하 한끼"})
    assert r_detached.status_code == 200
    assert r_detached.json()["validation_result"]["passed"] is True
    # 이벤트가 없으므로 '판정 불가' (F3020)
    assert r_detached.json()["anomaly_report"]["status"] == "판정 불가"

    # 2) 다시 연결 후 정상 실행 -> '이상 없음', LLM 미호출
    logging_agent.attach(event_bus)
    r_norm = client.post("/api/chat", json={"session_id": "s_norm", "message": "강남역 8000원 이하 고단백 한끼"})
    assert r_norm.json()["anomaly_report"]["status"] == "이상 없음"
    assert r_norm.json()["anomaly_report"]["llm_called"] is False

    # 3) 동일 Tool 동일 입력 반복 + 계획 외 Tool 호출 인위적 생성 -> '이상 탐지'
    event_bus.publish(AgentEvent("s_bad", "run1", "planner_worker_msg", "PlannerAgent", "planner_plan", {"task_list": [{"tool_name": "search_nutrition_db"}]}))
    event_bus.publish(AgentEvent("s_bad", "run1", "tool_call_start", "WorkerAgent", "worker_execute", {"tool_name": "search_nutrition_db", "args": {"query": "닭"}}))
    event_bus.publish(AgentEvent("s_bad", "run1", "tool_call_start", "WorkerAgent", "worker_execute", {"tool_name": "search_nutrition_db", "args": {"query": "닭"}}))
    event_bus.publish(AgentEvent("s_bad", "run1", "tool_call_start", "WorkerAgent", "worker_execute", {"tool_name": "search_nearby_store", "args": {"location": "강남"}}))

    res_anom = client.post("/api/logs/s_bad/analyze").json()
    codes = {w["code"] for w in res_anom["warnings"]}
    assert res_anom["status"] == "이상 탐지"
    assert "DUPLICATE_TOOL_CALL" in codes
    assert "UNPLANNED_TOOL_CALL" in codes

    # 4) F3014: 실행 로그 수정 API 미존재 확인(405) 및 삭제 동작 확인
    assert client.put("/api/logs/s_norm", json={}).status_code == 405
    assert client.patch("/api/logs/s_norm", json={}).status_code == 405
    assert client.get("/api/logs/s_norm").status_code == 200
    assert client.delete("/api/logs/s_norm").status_code == 200
    assert client.get("/api/logs/s_norm").status_code == 404


def test_worker_retry_and_fixed_args_guard():
    """F3021: 검색 0건(NO_NUTRITION_DATA) 시 Worker 키워드 보정 재시도, 모호 위치(AMBIGUOUS_LOCATION) 시 무재시도."""
    from app.main import app

    client = TestClient(app)

    # 1) 검색 0건 -> Worker가 대체 키워드로 재시도하여 복구
    r_retry = client.post("/api/chat", json={"session_id": "s_w1", "message": "강남역 편의점 8000원 이하 크립토나이트 한끼"})
    d_retry = r_retry.json()
    assert d_retry["validation_result"]["passed"] is True
    assert any(t.get("is_retry") for t in d_retry["execution_trace"] if t.get("step") == "tool_call")

    # 2) 모호 위치(중앙역) -> Worker가 임의로 지명을 고치지 않고 즉시 안내
    r_amb = client.post("/api/chat", json={"session_id": "s_w2", "message": "중앙역 편의점 8000원 이하 한끼"})
    d_amb = r_amb.json()
    assert d_amb["error_notice"]
    assert "여러 지역에 존재" in d_amb["reply"]
    assert not any(t.get("is_retry") for t in d_amb["execution_trace"] if t.get("step") == "tool_call")


def test_live_parsers_scaling_stopword_and_profile_endpoints(monkeypatch):
    """실시간 API 파서(식약처 신/구, 한국소비자원 XML EUC-KR/UTF-8, Open Food Facts, 카카오), 중량 환산, 프로필 API 검증."""
    from app.main import app
    from app.tools.local_tool import search_nearby_store
    from app.tools.nutrition_tool import search_nutrition_db
    from app.tools.optimizer_tool import _merge_nutrition_and_price
    from app.tools.price_tool import CVS_DISCLAIMER, search_product_price

    # 1) 오매칭 방지 & 320g 중량 비례 환산 검증
    nut_items = [
        {"food_id": "X1", "name": "오리지널 치즈케이크 100g", "category": "main", "serving_g": 100.0, "kcal": 350.0, "protein_g": 6.0, "carbs_g": 30.0, "fat_g": 22.0},
        {"food_id": "X2", "name": "닭가슴살 도시락", "category": "main", "serving_g": 100.0, "kcal": 125.0, "protein_g": 10.0, "carbs_g": 15.0, "fat_g": 2.5},
    ]
    shop_items = [
        {"product_id": "P1", "title": "스팀 닭가슴살 오리지널 100g 10팩", "unit_price_krw": 1500, "bundle_price_krw": 15000, "pack_count": 10, "unit_weight_g": 100.0, "servings_per_purchase": 10.0, "unit_basis": "10개/팩", "mall_name": "할인점", "link": "http://x"},
        {"product_id": "P2", "title": "한끼 닭가슴살 도시락 320g 1개", "unit_price_krw": 5500, "bundle_price_krw": 5500, "pack_count": 1, "unit_weight_g": 320.0, "servings_per_purchase": 1.0, "unit_basis": "1개", "mall_name": "편의점", "link": "http://y"},
    ]
    merged, excluded = _merge_nutrition_and_price(nut_items, shop_items)
    assert any(e["name"] == "오리지널 치즈케이크 100g" for e in excluded)
    lunchbox = next(m for m in merged if m["food_id"] == "X2")
    assert lunchbox["serving_g"] == 320.0
    assert lunchbox["kcal"] == 400.0 and lunchbox["protein_g"] == 32.0

    # 2) 실시간 식약처 / 소비자원(XML, EUC-KR) / Open Food Facts / 카카오 응답 파싱 검증
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "test-datagokr-key")
    monkeypatch.setenv("KAKAO_REST_API_KEY", "test-kakao-key")
    monkeypatch.setenv("USE_MOCK", "false")

    class _FakeResp:
        def __init__(self, payload=None, raw_bytes: bytes = b""):
            self._payload = payload
            self.content = raw_bytes

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        if "FoodNtrCpntDb" in url:
            return _FakeResp(
                {
                    "body": {
                        "items": [
                            {"FOOD_CD": "F1", "FOOD_NM_KR": "아이스 아메리카노", "SERVING_SIZE": "100g", "AMT_NUM1": "10", "AMT_NUM3": "0.5", "AMT_NUM4": "0", "AMT_NUM6": "2", "AMT_NUM7": "0", "AMT_NUM13": "5"},
                            {"FOOD_CD": "F2", "FOOD_NM_KR": "구운 계란", "SERVING_SIZE": "100g", "NUTR_CONT1": "140", "NUTR_CONT2": "1.2", "NUTR_CONT3": "12", "NUTR_CONT4": "9.6", "NUTR_CONT5": "0.5", "NUTR_CONT6": "200"},
                            {"FOOD_CD": "F3", "FOOD_NM_KR": "영양미상", "SERVING_SIZE": "100g", "AMT_NUM1": ""},
                        ]
                    }
                }
            )
        if "getProductInfoSvc.do" in url:
            xml_str = (
                "<?xml version='1.0' encoding='euc-kr'?>"
                "<response><result>"
                "<item><goodId>G100</goodId><goodName>스팀 닭가슴살 100g 10팩 + 10팩</goodName><goodTotalCnt></goodTotalCnt><goodUnitDivCode></goodUnitDivCode></item>"
                "<item><goodId>G200</goodId><goodName>단위없는상품</goodName><goodTotalCnt></goodTotalCnt><goodUnitDivCode></goodUnitDivCode></item>"
                "</result></response>"
            )
            return _FakeResp(raw_bytes=xml_str.encode("euc-kr"))
        if "getStoreInfoSvc.do" in url:
            xml_str = (
                "<?xml version='1.0' encoding='utf-8'?>"
                "<response><result>"
                "<iros.openapi.service.vo.entpInfoVO><entpId>E1</entpId><entpName>이마트</entpName><entpTypeCode>LM</entpTypeCode></iros.openapi.service.vo.entpInfoVO>"
                "<iros.openapi.service.vo.entpInfoVO><entpId>E2</entpId><entpName>CU(본사)</entpName><entpTypeCode>CS</entpTypeCode></iros.openapi.service.vo.entpInfoVO>"
                "</result></response>"
            )
            return _FakeResp(raw_bytes=xml_str.encode("utf-8"))
        if "getProductPriceInfoSvc" in url:
            gid = kwargs.get("params", {}).get("goodId")
            if gid == "G100":
                xml_str = (
                    "<?xml version='1.0' encoding='utf-8'?>"
                    "<response><result>"
                    "<iros.openapi.service.vo.goodPriceVO><goodId>G100</goodId><entpId>E1</entpId><goodPrice>28000</goodPrice></iros.openapi.service.vo.goodPriceVO>"
                    "<iros.openapi.service.vo.goodPriceVO><goodId>G100</goodId><entpId>E2</entpId><goodPrice>30000</goodPrice></iros.openapi.service.vo.goodPriceVO>"
                    "</result></response>"
                )
            else:
                xml_str = (
                    "<?xml version='1.0' encoding='utf-8'?>"
                    "<response><result>"
                    "<iros.openapi.service.vo.goodPriceVO><goodId>G200</goodId><entpId>E2</entpId><goodPrice>5000</goodPrice></iros.openapi.service.vo.goodPriceVO>"
                    "</result></response>"
                )
            return _FakeResp(raw_bytes=xml_str.encode("utf-8"))
        if "openfoodfacts.org" in url:
            return _FakeResp(
                {
                    "products": [
                        {"product_name": "프로틴 드링크 바닐라", "brands": "OFFBrand", "quantity": "250ml", "url": "https://world.openfoodfacts.org/product/1", "nutriments": {"energy-kcal_100g": 60, "proteins_100g": 8}}
                    ]
                }
            )
        if "dapi.kakao.com" in url:
            q = kwargs.get("params", {}).get("query")
            if q == "강남역":
                return _FakeResp({"documents": [{"x": "127.027", "y": "37.497"}]})
            return _FakeResp({"documents": [{"id": "K1", "place_name": f"{q} 강남점", "road_address_name": "서울 강남구", "distance": "120", "place_url": "https://place.map.kakao.com/1"}]})
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("requests.get", fake_get)

    n_res = search_nutrition_db("아메리카노")
    assert n_res["ok"] is True
    assert n_res["data"]["items"][0]["category"] == "drink"
    assert n_res["data"]["items"][1]["category"] == "sub"
    assert len(n_res["data"]["excluded_unverified"]) == 1

    # 편의점 모드: CU편의점 업태가 1순위 + 기준가 안내 문구 포함
    p_cvs = search_product_price("닭가슴살", mode="cvs_instant")
    assert p_cvs["ok"] is True
    assert p_cvs["data"]["products"][0]["retail_type"] == "cvs"
    assert p_cvs["data"]["products"][0]["unit_price_krw"] == 1500
    assert CVS_DISCLAIMER in p_cvs["data"]["basis_note"]

    # 대량 모드: 대형마트(이마트) 최저가 우선
    p_bulk = search_product_price("닭가슴살", mode="online_bulk")
    assert p_bulk["ok"] is True
    assert p_bulk["data"]["products"][0]["retail_type"] == "mart_bulk"
    assert p_bulk["data"]["products"][0]["unit_price_krw"] == 1400

    # 단위 파싱 실패 상품
    p_unparsed = search_product_price("단위없는상품", mode="cvs_instant")
    assert p_unparsed["ok"] is False and p_unparsed["error"]["code"] == "UNIT_PARSE_FAILED"

    # 소비자원 DB에 없는 품목 -> Open Food Facts 보조 호출 확인 (임의 KRW 가격 미생성)
    p_off = search_product_price("바닐라프로틴쉐이크", mode="cvs_instant")
    assert p_off["ok"] is False and p_off["error"]["code"] == "NO_SHOPPING_RESULT"
    assert "Open Food Facts" in p_off["error"]["message"]

    k_res = search_nearby_store("강남역", brands=["CU"])
    assert k_res["ok"] is True and k_res["data"]["stores"][0]["name"] == "CU 강남점"

    # 3) 프로필 API (PUT/GET) 검증
    client = TestClient(app)
    put_r = client.put("/api/profiles/prof1", json={"gender": "female", "weight_kg": 60, "target_weight_kg": 56, "target_weeks": 4, "goal_type": "diet", "meals_per_day": 3})
    assert put_r.status_code == 200 and put_r.json()["targets"]["target_daily_kcal"] >= 1100.0
    get_r = client.get("/api/profiles/prof1")
    assert get_r.status_code == 200 and get_r.json()["profile"]["weight_kg"] == 60


def test_multiturn_caffeine_reset_and_safe_crash_and_f3020_diagnosis(monkeypatch):
    """시간 기반 카페인 해제, 모드 전환 리셋, 미처리 예외 마스킹(NF3002), F3020 LLM 진단 및 동시성 검증."""
    from concurrent.futures import ThreadPoolExecutor

    from app.agents.logging_agent import logging_agent
    from app.events.event_bus import AgentEvent, event_bus
    from app.main import app

    client = TestClient(app)

    # 1) 저녁 19시(무카페인) -> 점심 12시 아메리카노 포함 요청 시 카페인 제한 해제 확인
    r1 = client.post("/api/chat", json={"session_id": "s_caf", "message": "강남역 저녁 7시 편의점 8000원 이하 한끼"})
    assert r1.json()["validation_result"]["passed"] is True
    r2 = client.post("/api/chat", json={"session_id": "s_caf", "message": "점심 12시 편의점 8000원 이하 아메리카노 포함 한끼 추천해줘"})
    assert r2.json()["validation_result"]["passed"] is True

    # 2) responder 종료 이벤트 이후 로그 저장 및 F3020 EXCESSIVE_REPLAN + LLM 진단 분기 검증
    events_caf = client.get("/api/logs/s_caf").json()["events"]
    assert events_caf[-1]["node_name"] == "responder" and events_caf[-1]["event_type"] == "node_exit"

    for i in range(3):
        event_bus.publish(AgentEvent("s_diag", "run_diag", "replan", "PlannerAgent", "planner_plan", {"replan_count": i + 1}))

    monkeypatch.setattr(
        "app.agents.logging_agent.safe_llm_invoke",
        lambda prompt, system_prompt="": ("Planner 재계획 상한 도달. 예산을 완화하세요.", True, None),
    )
    diag_rep = logging_agent.evaluate_session("s_diag", "run_diag", use_llm_diagnosis=True)
    assert diag_rep["status"] == "이상 탐지"
    assert any(w["code"] == "EXCESSIVE_REPLAN" for w in diag_rep["warnings"])
    assert diag_rep["llm_called"] is True and "Planner" in diag_rep["diagnosis"]

    # 3) 동시 계획 저장 시 경합 없이 모두 저장되고 중복 경고 감지 확인
    def save_overlap(idx):
        return client.post(
            "/api/plans",
            json={
                "session_id": "s_conc",
                "title": f"동시계획-{idx}",
                "mode": "cvs_instant",
                "plan_date": "2026-11-25",
                "start_time": "12:00",
                "end_time": "13:00",
                "items": [],
                "nutrition_summary": {},
                "total_price_krw": 5000,
            },
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(save_overlap, range(4)))
    assert all(r.status_code == 201 for r in results)
    assert sum(1 for r in results if r.json()["overlap_warnings"]) == 3

    # 4) 미처리 예외 시 원시 오류/경로 문자열이 UI로 노출되지 않는지 검증 (NF3002)
    def boom(_state):
        raise RuntimeError("SECRET_INTERNAL_PATH_C:/db/crash")

    monkeypatch.setattr("app.api.chat.diet_planner_graph.invoke", boom)
    crash_r = client.post("/api/chat", json={"session_id": "s_crash", "message": "안녕"})
    assert crash_r.status_code == 200
    assert "SECRET_INTERNAL_PATH" not in (crash_r.json().get("error_notice") or "")


def test_audit_regressions_parsers_matching_regex_and_cache(monkeypatch):
    """감사 백로그 #4~#10 회귀 검증: 100+20g·550g·빈 XML·오매칭·중복조합·정규식·실패캐시."""
    from app.agents.planner import _extract_rule_based
    from app.agents.worker import worker_node
    from app.tools.nutrition_tool import search_nutrition_db
    from app.tools.optimizer_tool import _merge_nutrition_and_price, _optimize_meal_set, _scale_nutrition_to_product
    from app.tools.price_tool import clear_price_caches, parse_bundle_unit, search_product_price

    # #5: "100+20g" 백트래킹 오파싱 방지 & 500~600g 경계 통일
    p_plus_weight = parse_bundle_unit("닭가슴살 100+20g")
    assert p_plus_weight["servings_per_purchase"] == 1.0
    p_550 = parse_bundle_unit("닭가슴살 파우치 550g")
    assert p_550["servings_per_purchase"] == 5.5
    scaled_550 = _scale_nutrition_to_product(
        {"food_id": "F550", "name": "파우치", "serving_g": 100.0, "kcal": 120.0, "protein_g": 20.0, "carbs_g": 2.0, "fat_g": 2.0},
        {"unit_weight_g": 550.0, "pack_count": 1, "servings_per_purchase": 5.5},
    )
    assert scaled_550["kcal"] == 120.0 and scaled_550["serving_g"] == 100.0

    # #6: "두부" ↔ "두부바" 오매칭 차단, 조리법("스팀" vs "훈제") 유지, 빈 mains 중복 방지
    merged, excluded = _merge_nutrition_and_price(
        [
            {"food_id": "N1", "name": "두부", "category": "main", "serving_g": 100.0, "kcal": 80.0, "protein_g": 9.0, "carbs_g": 2.0, "fat_g": 4.0},
            {"food_id": "N2", "name": "스팀 닭가슴살 100g", "category": "main", "serving_g": 100.0, "kcal": 110.0, "protein_g": 23.0, "carbs_g": 1.0, "fat_g": 1.5},
        ],
        [
            {"product_id": "SP1", "title": "편의점 단백질 두부바 70g 1개", "unit_price_krw": 2200, "bundle_price_krw": 2200, "pack_count": 1, "unit_weight_g": 70.0, "servings_per_purchase": 1.0, "unit_basis": "1개", "mall_name": "CU", "link": "http://a"},
            {"product_id": "SP2", "title": "편의점 훈제 닭가슴살 100g 1팩", "unit_price_krw": 3200, "bundle_price_krw": 3200, "pack_count": 1, "unit_weight_g": 100.0, "servings_per_purchase": 1.0, "unit_basis": "1팩", "mall_name": "CU", "link": "http://b"},
            {"product_id": "SP3", "title": "편의점 스팀 닭가슴살 100g 1팩", "unit_price_krw": 2900, "bundle_price_krw": 2900, "pack_count": 1, "unit_weight_g": 100.0, "servings_per_purchase": 1.0, "unit_basis": "1팩", "mall_name": "CU", "link": "http://c"},
        ],
    )
    assert any(e["name"] == "두부" for e in excluded)
    assert merged[0]["unit_price_krw"] == 2900

    # 빈 mains & snack 유입 차단: 동일 food_id 중복 조합 금지
    no_main_pool = [
        {"food_id": "S1", "name": "초코 쿠키", "category": "snack", "serving_g": 100.0, "kcal": 450.0, "protein_g": 4.0, "carbs_g": 60.0, "fat_g": 20.0, "sugar_g": 30.0, "unit_price_krw": 2000, "protein_density_pct": 3.5, "protein_per_1000krw": 2.0},
        {"food_id": "SUB1", "name": "감동란 반숙 계란", "category": "sub", "serving_g": 100.0, "kcal": 140.0, "protein_g": 12.0, "carbs_g": 1.2, "fat_g": 9.6, "sugar_g": 0.5, "unit_price_krw": 2400, "protein_density_pct": 34.3, "protein_per_1000krw": 5.0},
        {"food_id": "DRK1", "name": "무가당 두유", "category": "drink", "serving_g": 190.0, "kcal": 95.0, "protein_g": 9.5, "carbs_g": 3.0, "fat_g": 5.0, "sugar_g": 1.2, "unit_price_krw": 1600, "protein_density_pct": 40.0, "protein_per_1000krw": 5.9},
    ]
    best_no_main, _ = _optimize_meal_set(no_main_pool, {"budget_krw": 8000, "min_meal_protein_g": 20.0}, "cvs_instant")
    assert best_no_main is not None
    picked_ids = [i["food_id"] for i in best_no_main["selected_items"]]
    assert len(set(picked_ids)) == len(picked_ids) and "S1" not in picked_ids

    # #7, #8: 목표체중 선입력 & ~역 비지명 단어 오추출 방지
    ext_w = _extract_rule_based("목표 체중 65kg 현재 75kg 남성 4주 감량 역산해줘")
    assert ext_w["profile_updates"]["target_weight_kg"] == 65.0
    assert ext_w["profile_updates"]["weight_kg"] == 75.0
    ext_loc = _extract_rule_based("면역 높이는 서울지역 편의점 8000원 이하 한끼")
    assert "location" not in ext_loc["base_conditions"]

    # #4, #5(소비자원), #6(snack 제외): 빈 XML 오류 응답 비캐싱 & "12팩"+190ml 결합 & snack 제외
    clear_price_caches()
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "test-key")
    monkeypatch.setenv("USE_MOCK", "false")
    call_stage = {"n": 0}

    class _FakeResp:
        def __init__(self, payload=None, raw_bytes: bytes = b""):
            self._payload = payload
            self.content = raw_bytes

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, **kwargs):
        if "FoodNtrCpntDb" in url:
            return _FakeResp({"body": {"items": [
                {"FOOD_CD": "SN1", "FOOD_NM_KR": "초코 쿠키", "SERVING_SIZE": "100g", "AMT_NUM1": "500", "AMT_NUM3": "5", "AMT_NUM4": "25", "AMT_NUM6": "65"},
                {"FOOD_CD": "OK1", "FOOD_NM_KR": "매일두유 99.9", "SERVING_SIZE": "100g", "AMT_NUM1": "50", "AMT_NUM3": "5", "AMT_NUM4": "2.6", "AMT_NUM6": "1.5"},
            ]}})
        if "getProductInfoSvc.do" in url:
            call_stage["n"] += 1
            if call_stage["n"] == 1:
                return _FakeResp(raw_bytes=b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>22</returnReasonCode><returnAuthMsg>LIMITED</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>")
            return _FakeResp(raw_bytes="<response><result><item><goodId>G300</goodId><goodName>매일두유 99.9(12팩)</goodName><goodTotalCnt>190</goodTotalCnt><goodUnitDivCode>ml</goodUnitDivCode></item></result></response>".encode("utf-8"))
        if "getStoreInfoSvc.do" in url:
            return _FakeResp(raw_bytes=b"<response><result><item><entpId>E1</entpId><entpName>GS25</entpName><entpTypeCode>CS</entpTypeCode></item></result></response>")
        if "getProductPriceInfoSvc" in url:
            return _FakeResp(raw_bytes=b"<response><result><item><goodId>G300</goodId><entpId>E1</entpId><goodPrice>14400</goodPrice></item></result></response>")
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("requests.get", fake_get)
    n_check = search_nutrition_db("두유")
    assert n_check["ok"] is True and [i["food_id"] for i in n_check["data"]["items"]] == ["OK1"]

    err_first = search_product_price("두유", mode="cvs_instant")
    assert err_first["ok"] is False and err_first["error"]["code"] == "API_ERROR"
    ok_second = search_product_price("두유", mode="cvs_instant")
    assert ok_second["ok"] is True
    assert ok_second["data"]["products"][0]["pack_count"] == 12
    assert ok_second["data"]["products"][0]["unit_weight_g"] == 190.0

    # #9: 실패한 검색 결과(NO_SHOPPING_RESULT) 재계획 시 캐시 히트 검증
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "")
    monkeypatch.setenv("USE_MOCK", "true")
    w_state = {
        "session_id": "s_cache_fail",
        "run_id": "run_cf",
        "worker_llm_enabled": False,
        "task_list": [
            {"task_id": "t1", "tool_name": "search_product_price", "args": {"query": "존재안함상품999", "mode": "cvs_instant", "display": 8}, "fixed_args": [], "depends_on": []}
        ],
    }
    res1 = worker_node(w_state)
    assert res1["execution_trace"][0]["step"] == "tool_call"
    res2 = worker_node({**w_state, "shopping_cache": res1["shopping_cache"]})
    assert res2["execution_trace"][0]["step"] == "tool_cache_hit" and res2["execution_trace"][0]["ok"] is False
