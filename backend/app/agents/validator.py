"""NutritionValidatorAgent: 1단계 결정론적 수학/규칙 검증 + 2단계 LLM 상식 교차 검증."""

from __future__ import annotations

import json
from typing import Any

from app.agents.llm_client import safe_llm_json
from app.events.event_bus import AgentEvent, event_bus
from app.models.state import AgentState
from app.tools.optimizer_tool import check_meal_constraints, summarize_combo

_BANNED_MAIN_SNACKS = ("과자", "초콜릿", "사탕", "젤리", "케이크", "아이스크림", "탄산음료")


def _stage1_deterministic(
    combo: dict[str, Any],
    constraints: dict[str, Any],
    preferences: dict[str, Any],
    mode: str,
) -> tuple[bool, list[str], dict[str, Any]]:
    """1단계: 개별 품목 합계 재검산(오차 보정) 및 예산·단백질·칼로리·건강제약 검증 (LLM 미사용)."""
    if mode == "profile_calc":
        has_calc = bool(combo.get("reverse_calc"))
        return (has_calc, [] if has_calc else ["목표 역산 결과가 비어 있습니다."], {})

    items = combo.get("selected_items", [])
    if not items:
        return False, ["추천 가능한 식단 품목 조합이 없습니다."], {}

    recalculated = summarize_combo(items)
    is_bulk = mode == "online_bulk" or (combo.get("shopping_summary") or {}).get("type") == "online_bulk"
    price_override = combo.get("total_price_krw") if is_bulk else None
    errors = check_meal_constraints(
        items,
        recalculated,
        constraints,
        preferences=preferences,
        total_price_override=price_override,
    )
    return len(errors) == 0, errors, recalculated


def _stage2_semantic_check(combo: dict[str, Any], mode: str) -> tuple[bool, list[str], bool]:
    """2단계: 1단계 통과 시 상품명-영양성분-가격 매칭의 상식 오류 교차 검증 (INVALID_FOOD_MATCH)."""
    if mode == "profile_calc":
        return True, [], False

    items = combo.get("selected_items", [])
    for it in items:
        if it.get("category") in {"main", "bulk_protein"} and any(bad in it["name"] for bad in _BANNED_MAIN_SNACKS):
            return False, [f"INVALID_FOOD_MATCH: '{it['name']}'은(는) 단백질 메인 식단으로 부적절합니다."], False
        macro_kcal = float(it["protein_g"]) * 4 + float(it["carbs_g"]) * 4 + float(it["fat_g"]) * 9
        reported_kcal = float(it["kcal"])
        if reported_kcal > 30 and abs(macro_kcal - reported_kcal) > max(70.0, reported_kcal * 0.45):
            return False, [f"INVALID_FOOD_MATCH: '{it['name']}'의 탄단지 합산 열량({macro_kcal:.0f})과 표기 열량({reported_kcal:.0f}) 괴리가 과도합니다."], False

    brief = [
        {
            "name": i["name"],
            "product_title": i.get("product_title"),
            "category": i.get("category"),
            "serving_g": i.get("serving_g"),
            "unit_basis": i.get("unit_basis"),
            "kcal": i["kcal"],
            "protein_g": i["protein_g"],
            "unit_price_krw": i.get("unit_price_krw"),
        }
        for i in items
    ]
    prompt = (
        "식약처 영양 DB 품목(name)과 한국소비자원 가격 DB 상품(product_title)이 매칭된 식단 조합입니다.\n"
        "중량(serving_g)과 단위(unit_basis)를 함께 보고, 과자/빵류를 닭가슴살 메인으로 매칭하는 등 "
        "서로 다른 식품이 잘못 연결된 명백한 오매칭(INVALID_FOOD_MATCH)만 valid=false로 반려하세요. "
        "두부(300g)·계란·참치·두유 등 실제 식재료의 정상적인 시중 가격과 영양성분은 valid=true로 통과시키세요.\n"
        "JSON {\"valid\": true|false, \"reason\": \"...\"}만 답하세요.\n"
        f"{json.dumps(brief, ensure_ascii=False)}"
    )
    parsed, called, _ = safe_llm_json(prompt, system_prompt="너는 식단 데이터 교차 검증 전문가다. JSON만 답한다.")
    if parsed and called and parsed.get("valid") is False:
        return False, [f"INVALID_FOOD_MATCH: {parsed.get('reason', '상품명과 영양/가격 매칭 오류')}"], True
    return True, [], called


def validator_node(state: AgentState) -> dict[str, Any]:
    """2단계 검증을 수행하고 재검산 수치로 보정한다."""
    session_id = state["session_id"]
    run_id = state["run_id"]
    mode = state.get("mode", "cvs_instant")
    combo = dict(state.get("candidate_combo") or {})
    cons = state.get("constraints", {})
    prefs = state.get("preferences", {})
    fatal_error = state.get("fatal_error")
    trace = list(state.get("execution_trace", []))
    llm_calls = int(state.get("llm_calls_count", 0))

    event_bus.publish(AgentEvent(session_id, run_id, "node_enter", "NutritionValidatorAgent", "validator_check", {"mode": mode}))

    if fatal_error:
        val_result = {"passed": False, "stage1_errors": [fatal_error["message"]], "stage2_errors": [], "fatal": True}
        trace.append({"step": "validator_check", "passed": False, "fatal": True, "errors": val_result["stage1_errors"]})
        event_bus.publish(AgentEvent(session_id, run_id, "node_exit", "NutritionValidatorAgent", "validator_check", val_result))
        return {"validation_result": val_result, "execution_trace": trace}

    s1_ok, s1_errors, corrected = _stage1_deterministic(combo, cons, prefs, mode)
    if corrected:
        combo["nutrition_summary"] = corrected

    s2_ok = True
    s2_errors: list[str] = []
    if s1_ok:
        s2_ok, s2_errors, llm_called = _stage2_semantic_check(combo, mode)
        if llm_called:
            llm_calls += 1

    passed = s1_ok and s2_ok
    val_result = {
        "passed": passed,
        "stage1_errors": s1_errors,
        "stage2_errors": s2_errors,
        "corrected_totals": corrected,
        "fatal": False,
    }

    trace.append(
        {
            "step": "validator_check",
            "passed": passed,
            "stage1_errors": s1_errors,
            "stage2_errors": s2_errors,
        }
    )
    event_bus.publish(
        AgentEvent(
            session_id,
            run_id,
            "node_exit",
            "NutritionValidatorAgent",
            "validator_check",
            {"passed": passed, "stage1_errors": s1_errors, "stage2_errors": s2_errors},
        )
    )

    return {
        "candidate_combo": combo,
        "validation_result": val_result,
        "execution_trace": trace,
        "llm_calls_count": llm_calls,
    }
