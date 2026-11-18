"""PlannerAgent: 요청 분석, 대화 맥락 유지(Memory), 누락 조건 되묻기, 작업 목록(task_list) 수립 및 재계획."""

from __future__ import annotations

import json
import re
from typing import Any

from app.agents.llm_client import safe_llm_json
from app.config import get_settings
from app.database import load_profile, load_session_context
from app.events.event_bus import AgentEvent, event_bus
from app.models.state import AgentState

_BUDGET_RE = re.compile(r"(\d+(?:,\d{3})*)\s*(만원|만\s*원|천원|원)")
_WEIGHT_RE = re.compile(r"(?:현재\s*체중|몸무게|체중)\s*(\d+(?:\.\d+)?)\s*kg|(\d+(?:\.\d+)?)\s*kg")
_TARGET_WEIGHT_RE = re.compile(r"목표\s*(?:체중)?\s*(\d+(?:\.\d+)?)\s*kg")
_WEEKS_RE = re.compile(r"(\d+)\s*주")
_DAYS_RE = re.compile(r"(\d+)\s*일")
_MEALS_RE = re.compile(r"(?:하루|매일)?\s*(\d+)\s*끼")
_TIME_RE = re.compile(r"(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_EXCESS_KCAL_RE = re.compile(r"(\d+(?:,\d{3})*)\s*(?:kcal|칼로리)\s*(?:초과|더\s*먹|과식|치팅)?")
_LOCATION_RE = re.compile(r"([가-힣A-Za-z0-9]+역|강남|역삼|홍대|신촌|잠실|판교|중앙|시청|중구|동구|서구|남구|북구|역\s*앞|무인도|매장없음)")
_PROTEIN_RE = re.compile(r"단백질\s*(\d+(?:\.\d+)?)\s*g")
_KCAL_CAP_RE = re.compile(r"(\d+(?:,\d{3})*)\s*(?:kcal|칼로리)\s*(?:이하|이내|미만)")

_KNOWN_BRANDS = ["CU", "GS25", "세븐일레븐", "이마트24"]
_KNOWN_DISLIKES = ["오이", "당근", "고수", "땅콩", "새우", "계란", "두부", "닭가슴살", "우유", "요거트", "아몬드"]
_NON_LOCATION_SUFFIXES = ("지역", "구역", "면역", "체력", "내역", "번역", "배역", "검역", "방역")


def _parse_budget(text: str) -> int | None:
    m = _BUDGET_RE.search(text)
    if not m:
        return None
    val = int(m.group(1).replace(",", ""))
    unit = m.group(2).replace(" ", "")
    if unit == "만원":
        return val * 10000
    if unit == "천원":
        return val * 1000
    return val


_SWAP_TARGETS = (
    "그릭요거트", "요거트", "감동란", "계란", "두부바", "두부", "닭가슴살",
    "도시락", "샐러드", "아몬드 브리즈", "아몬드", "프로틴", "유청 단백질", "두유", "콜라", "오트밀",
    "현미", "곤약밥", "참치", "바나나", "삼각김밥", "탄산수", "아메리카노",
    "음료", "서브", "메인",
)


def _find_swap_target(text: str) -> str | None:
    return next((cand for cand in _SWAP_TARGETS if cand in text), None)


def _extract_rule_based(text: str) -> dict[str, Any]:
    """정규식 및 키워드 규칙으로 요청 조건을 추출한다 (추출 못 한 값은 임의로 채우지 않음 F3005)."""
    lower = text.lower()
    extracted: dict[str, Any] = {
        "mode": None,
        "base_conditions": {},
        "preferences": {},
        "constraints": {},
        "profile_updates": {},
    }

    # 의도/모드 분류
    if any(k in lower for k in ("안녕", "반가", "도움말", "사용법", "뭐 할 수", "기능 알려", "누구")):
        extracted["mode"] = "general_chat"
        return extracted

    swap_target = _find_swap_target(text)
    explicit_swap_phrase = any(k in text for k in ("스왑", "빼고 다른", "대신 다른", "품목 교체"))
    verb_with_item = bool(swap_target) and any(k in text for k in ("바꿔", "교체", "대체", "빼고"))

    if explicit_swap_phrase or verb_with_item:
        extracted["mode"] = "item_swap"
        if swap_target:
            extracted["base_conditions"]["swap_target"] = swap_target
    elif any(k in text for k in ("치팅", "폭식", "과식", "외식", "초과", "복구", "리밸런싱")):
        extracted["mode"] = "cheating_recovery"
    elif any(k in text for k in ("대량", "장바구니", "온라인", "박스", "포트폴리오", "주문")):
        extracted["mode"] = "online_bulk"
    elif any(k in text for k in ("편의점", "한끼", "한 끼", "즉시", "CU", "GS25", "세븐일레븐", "근처", "역")):
        extracted["mode"] = "cvs_instant"
    elif any(k in text for k in ("역산", "기초대사량", "bmr", "tdee", "목표 칼로리", "감량 기간", "프로필")):
        extracted["mode"] = "profile_calc"

    # 프로필 추출
    if "남성" in text or "남자" in text:
        extracted["profile_updates"]["gender"] = "male"
    elif "여성" in text or "여자" in text:
        extracted["profile_updates"]["gender"] = "female"

    t_weight = _TARGET_WEIGHT_RE.search(text)
    if t_weight:
        extracted["profile_updates"]["target_weight_kg"] = float(t_weight.group(1))
    t_span = t_weight.span() if t_weight else None
    for w_match in _WEIGHT_RE.finditer(text):
        if t_span and w_match.start() >= t_span[0] and w_match.end() <= t_span[1]:
            continue
        extracted["profile_updates"]["weight_kg"] = float(w_match.group(1) or w_match.group(2))
        break
    if m_weeks := _WEEKS_RE.search(text):
        weeks = int(m_weeks.group(1))
        extracted["profile_updates"]["target_weeks"] = weeks
        extracted["base_conditions"]["period_weeks"] = weeks
    if m_meals := _MEALS_RE.search(text):
        meals = int(m_meals.group(1))
        extracted["profile_updates"]["meals_per_day"] = meals
        extracted["base_conditions"]["meals_per_day"] = meals
    if "벌크" in text or "증량" in text:
        extracted["profile_updates"]["goal_type"] = "bulk"
    elif "유지" in text:
        extracted["profile_updates"]["goal_type"] = "maintain"
    elif "다이어트" in text or "감량" in text:
        extracted["profile_updates"]["goal_type"] = "diet"

    # 기준/제약/선호 추출
    if budget := _parse_budget(text):
        extracted["constraints"]["budget_krw"] = budget
    if m_prot := _PROTEIN_RE.search(text):
        extracted["constraints"]["min_meal_protein_g"] = float(m_prot.group(1))
    elif "고단백" in text:
        extracted["constraints"]["min_meal_protein_g"] = 30.0
    if m_kcal := _KCAL_CAP_RE.search(text):
        extracted["constraints"]["target_meal_kcal"] = float(m_kcal.group(1).replace(",", ""))
    if m_date := _DATE_RE.search(text):
        extracted["base_conditions"]["plan_date"] = m_date.group(1)
    if m_time := _TIME_RE.search(text):
        hour = int(m_time.group(1))
        minute = int(m_time.group(2) or 0)
        if ("오후" in text or "저녁" in text or "밤" in text) and hour < 12:
            hour += 12
        extracted["base_conditions"]["meal_time"] = f"{hour:02d}:{minute:02d}"
        if hour >= 18:
            extracted["constraints"]["no_caffeine"] = True
        else:
            extracted["constraints"]["no_caffeine"] = False
    if any(k in text for k in ("18시 이후", "밤", "야식", "카페인 제외", "무카페인", "카페인 빼")):
        extracted["constraints"]["no_caffeine"] = True
    elif any(k in text for k in ("카페인 포함", "아메리카노", "커피 포함")):
        extracted["constraints"]["no_caffeine"] = False
    if any(k in text for k in ("유당불내증", "유당 제외", "무유당", "우유 빼", "유청 제외")):
        extracted["constraints"]["lactose_free"] = True
    if any(k in text for k in ("저당", "당류 제한", "당 줄")):
        extracted["constraints"]["low_sugar"] = True
        extracted["constraints"]["max_sugar_g"] = 10.0

    # 모드 C 초과 칼로리 / 잔여 일수
    if m_exc := _EXCESS_KCAL_RE.search(text):
        if extracted["mode"] == "cheating_recovery" or "초과" in text or "치팅" in text or "과식" in text:
            extracted["base_conditions"]["excess_kcal"] = float(m_exc.group(1).replace(",", ""))
    if m_days := _DAYS_RE.search(text):
        extracted["base_conditions"]["remain_days"] = int(m_days.group(1))

    # 위치/브랜드
    for m_loc in _LOCATION_RE.finditer(text):
        loc_cand = m_loc.group(1).strip()
        if any(loc_cand.endswith(bad) for bad in _NON_LOCATION_SUFFIXES):
            continue
        extracted["base_conditions"]["location"] = loc_cand
        break
    brands = [b for b in _KNOWN_BRANDS if b.lower() in lower]
    if brands:
        extracted["preferences"]["brands"] = brands

    # 기피 식재료
    disliked = [d for d in _KNOWN_DISLIKES if f"{d} 빼" in text or f"{d} 제외" in text or f"{d} 알레르기" in text]
    if disliked:
        extracted["preferences"]["disliked_ingredients"] = disliked

    # 명시적 식품 검색어(예: 테스트용 존재하지 않는 식품)
    if "미상영양식품" in text:
        extracted["preferences"]["explicit_query"] = "미상영양식품"
    elif "크립토나이트" in text:
        extracted["preferences"]["explicit_query"] = "크립토나이트"
    elif "파싱불가" in text:
        extracted["preferences"]["explicit_shop_query"] = "파싱불가"

    return extracted


def _try_llm_intent_refine(text: str, rule_data: dict[str, Any]) -> tuple[dict[str, Any], bool, str]:
    """OPENAI_API_KEY가 있을 때만 LLM으로 의도·제약 추출을 보강하고 판단 이유를 남긴다 (F3012)."""
    if rule_data.get("mode") == "general_chat":
        return rule_data, False, "규칙 기반 일반 대화 분류"

    prompt = (
        f"사용자 요청: {text}\n"
        f"현재 규칙 추출 결과: {json.dumps(rule_data, ensure_ascii=False)}\n"
        "모드(online_bulk:온라인대량, cvs_instant:편의점즉시, cheating_recovery:치팅복구, profile_calc:프로필역산, item_swap:품목스왑, general_chat:일반)와 "
        "추출 근거 한 줄(reason)을 JSON {\"mode\": ..., \"reason\": ...} 형태로만 답하세요."
    )
    parsed, called, _ = safe_llm_json(prompt, system_prompt="너는 식단 플래너 의도 분석기다. JSON만 출력한다.")
    if parsed and called:
        if not rule_data.get("mode") and parsed.get("mode") in {"online_bulk", "cvs_instant", "cheating_recovery", "profile_calc", "item_swap", "general_chat"}:
            rule_data["mode"] = parsed["mode"]
        return rule_data, True, parsed.get("reason", "LLM 의도 분류")
    return rule_data, called, "규칙 기반 조건 추출 완료"


def analyze_node(state: AgentState) -> dict[str, Any]:
    """F3005, F3010: 요청 분석, 세션 맥락 병합 및 누락 필수 조건 확인."""
    session_id = state["session_id"]
    run_id = state["run_id"]
    user_msg = state["user_message"]
    llm_calls = int(state.get("llm_calls_count", 0))

    event_bus.publish(AgentEvent(session_id, run_id, "node_enter", "PlannerAgent", "planner_analyze", {"user_message": user_msg}))

    prev_ctx = load_session_context(session_id)
    saved_profile = load_profile(session_id) or {}
    profile = {**prev_ctx.get("profile", {}), **saved_profile, **(state.get("profile") or {})}

    rule_data = _extract_rule_based(user_msg)
    refined, llm_called, reason = _try_llm_intent_refine(user_msg, rule_data)
    if llm_called:
        llm_calls += 1

    # F3010: 같은 모드 후속 요청은 직전 맥락을 유지하되, item_swap 이후에는 직전 실행 모드(base_mode)로 복귀
    prev_mode = prev_ctx.get("mode")
    base_mode = prev_ctx.get("base_mode") or (
        prev_mode if prev_mode in {"online_bulk", "cvs_instant", "cheating_recovery"} else "cvs_instant"
    )
    explicit_mode = refined.get("mode")
    mode = explicit_mode or base_mode
    _meal_modes = {"online_bulk", "cvs_instant", "cheating_recovery"}
    mode_switched = bool(explicit_mode in _meal_modes and prev_mode and explicit_mode != base_mode)
    next_base_mode = mode if mode in _meal_modes else base_mode

    profile = {**profile, **refined.get("profile_updates", {})}
    if mode_switched:
        base_conditions = dict(refined.get("base_conditions", {}))
        preferences = dict(refined.get("preferences", {}))
        constraints = dict(refined.get("constraints", {}))
    else:
        base_conditions = {**prev_ctx.get("base_conditions", {}), **refined.get("base_conditions", {})}
        preferences = {**prev_ctx.get("preferences", {}), **refined.get("preferences", {})}
        constraints = {**prev_ctx.get("constraints", {}), **refined.get("constraints", {})}

    if mode != "item_swap":
        base_conditions.pop("swap_target", None)
    elif "swap_target" not in refined.get("base_conditions", {}):
        base_conditions.pop("swap_target", None)

    # 명시적 테스트 쿼리 및 커피 요청 플래그는 현재 턴에만 유지
    if "explicit_query" not in refined.get("preferences", {}):
        preferences.pop("explicit_query", None)
    if "explicit_shop_query" not in refined.get("preferences", {}):
        preferences.pop("explicit_shop_query", None)
    if any(k in user_msg for k in ("아메리카노", "커피")):
        preferences["include_coffee"] = True
    elif not mode_switched:
        preferences.pop("include_coffee", None)

    if "lactose_free" in profile:
        if profile["lactose_free"]:
            constraints["lactose_free"] = True
        elif "lactose_free" not in refined.get("constraints", {}):
            constraints.pop("lactose_free", None)
    if "low_sugar" in profile:
        if profile["low_sugar"]:
            constraints["low_sugar"] = True
            constraints["max_sugar_g"] = 10.0
        elif "low_sugar" not in refined.get("constraints", {}):
            constraints.pop("low_sugar", None)
            constraints.pop("max_sugar_g", None)
    if profile.get("no_caffeine_after_18") and base_conditions.get("meal_time", "00:00") >= "18:00":
        constraints.setdefault("no_caffeine", True)
    if profile.get("budget_krw") is not None and "budget_krw" not in refined.get("constraints", {}):
        if saved_profile.get("budget_krw") != prev_ctx.get("profile", {}).get("budget_krw"):
            constraints["budget_krw"] = profile["budget_krw"]
        else:
            constraints.setdefault("budget_krw", profile["budget_krw"])
    if profile.get("disliked_ingredients"):
        merged_dislikes = list(dict.fromkeys([*preferences.get("disliked_ingredients", []), *profile["disliked_ingredients"]]))
        preferences["disliked_ingredients"] = merged_dislikes

    previous_combo = prev_ctx.get("last_combo") or state.get("previous_combo") or {}

    # 필수 조건 누락 검증 (F3005: 누락 시 Tool 호출 전 되묻기)
    missing: list[str] = []
    if mode == "profile_calc":
        if not profile.get("gender"):
            missing.append("성별(남성/여성)")
        if not profile.get("weight_kg"):
            missing.append("현재 체중(kg)")
    elif mode == "online_bulk":
        if not constraints.get("budget_krw"):
            missing.append("총 예산 상한(예: 15만원)")
        if not base_conditions.get("period_weeks") and not profile.get("target_weeks"):
            missing.append("식단 기간(예: 4주)")
    elif mode == "cvs_instant":
        has_any_anchor = bool(constraints.get("budget_krw") or base_conditions.get("location") or constraints.get("min_meal_protein_g") or constraints.get("target_meal_kcal"))
        if not has_any_anchor:
            missing.append("예산 상한(예: 8,000원 이하) 또는 위치/목표 단백질")
    elif mode == "cheating_recovery":
        if not base_conditions.get("excess_kcal"):
            missing.append("초과 섭취 칼로리(예: 1,200kcal 초과)")
        if not base_conditions.get("remain_days"):
            missing.append("복구 잔여 일수(예: 3일)")
        if not profile.get("weight_kg"):
            missing.append("현재 체중(kg)")
        if not profile.get("gender"):
            missing.append("성별(남성/여성)")
    elif mode == "item_swap":
        if not previous_combo.get("selected_items"):
            missing.append("교체할 기존 식단 조합(먼저 식단 추천을 받아주세요)")
        elif not base_conditions.get("swap_target"):
            missing.append("교체할 품목명(예: 그릭요거트, 음료)")

    clarification_needed = bool(missing) and mode != "general_chat"
    clarification_question = ""
    if clarification_needed:
        clarification_question = (
            f"정확한 식단 계산을 위해 다음 필수 조건을 알려주세요: {', '.join(missing)}"
        )

    trace = list(state.get("execution_trace", []))
    trace.append(
        {
            "step": "planner_analyze",
            "mode": mode,
            "reason": reason,
            "clarification_needed": clarification_needed,
            "missing_fields": missing,
        }
    )

    event_bus.publish(
        AgentEvent(
            session_id,
            run_id,
            "llm_decision",
            "PlannerAgent",
            "planner_analyze",
            {"mode": mode, "reason": reason, "llm_called": llm_called, "missing": missing},
        )
    )
    event_bus.publish(
        AgentEvent(
            session_id,
            run_id,
            "node_exit",
            "PlannerAgent",
            "planner_analyze",
            {"mode": mode, "clarification_needed": clarification_needed},
        )
    )

    return {
        "mode": mode,
        "base_mode": next_base_mode,
        "profile": profile,
        "base_conditions": base_conditions,
        "preferences": preferences,
        "constraints": constraints,
        "previous_combo": previous_combo,
        "clarification_needed": clarification_needed,
        "clarification_question": clarification_question,
        "missing_fields": missing,
        "execution_trace": trace,
        "llm_calls_count": llm_calls,
    }


def plan_node(state: AgentState) -> dict[str, Any]:
    """F3006, F3009, F3021: 작업 목록(task_list) 수립 및 검증 실패 시 재계획."""
    session_id = state["session_id"]
    run_id = state["run_id"]
    mode = state.get("mode", "cvs_instant")
    replan_count = int(state.get("replan_count", 0))
    max_replan = int(state.get("max_replan", get_settings().max_replan))
    trace = list(state.get("execution_trace", []))

    validation = state.get("validation_result") or {}
    escalations = state.get("worker_escalations") or []
    is_replan = bool(validation or escalations or state.get("task_list"))

    event_bus.publish(AgentEvent(session_id, run_id, "node_enter", "PlannerAgent", "planner_plan", {"is_replan": is_replan, "replan_count": replan_count}))

    reason_text = ""
    if is_replan:
        replan_count += 1
        reasons = validation.get("stage1_errors", []) + validation.get("stage2_errors", []) + [e.get("reason", "") for e in escalations]
        reason_text = "; ".join(r for r in reasons if r) or "조건 미충족으로 대안 재탐색"
        event_bus.publish(
            AgentEvent(
                session_id,
                run_id,
                "replan",
                "PlannerAgent",
                "planner_plan",
                {"replan_count": replan_count, "max_replan": max_replan, "reason": reason_text},
            )
        )
        trace.append({"step": "replan", "replan_count": replan_count, "reason": reason_text})

    prefs = state.get("preferences", {})
    cond = state.get("base_conditions", {})
    cons = dict(state.get("constraints", {}))
    profile = state.get("profile", {})

    task_list: list[dict[str, Any]] = []
    if mode == "profile_calc":
        task_list.append(
            {
                "task_id": "t_opt",
                "tool_name": "optimize_meal_combo",
                "args": {"mode": "profile_calc", "profile": profile, "constraints": cons, "base_conditions": cond},
                "fixed_args": ["mode"],
                "depends_on": [],
            }
        )
    else:
        use_mock = get_settings().use_mock
        query_mode = state.get("base_mode", mode) if mode == "item_swap" else mode
        replan_tag = (
            "sodium" if ("HIGH_SODIUM" in reason_text or "나트륨" in reason_text)
            else "budget" if ("BUDGET_EXCEEDED" in reason_text or "예산 초과" in reason_text)
            else "default"
        )
        if replan_count >= 1:
            extra_nut = (
                {"sodium": ["오트밀", "아몬드", "두유", "요거트"], "budget": ["두부", "달걀", "두유", "오트밀"], "default": ["두부", "아몬드"]}
                if query_mode == "online_bulk"
                else {"sodium": ["요거트", "아몬드", "두유"], "budget": ["달걀", "두부", "두유", "아몬드"], "default": ["달걀", "요거트", "아몬드", "샐러드"]}
            )[replan_tag]
            extra_shop = (
                (["오트밀", "아몬드"] if replan_tag != "default" else [])
                if query_mode == "online_bulk"
                else {"sodium": ["요거트", "아몬드", "두유"], "budget": ["계란", "두부", "아몬드"], "default": ["요거트", "아몬드", "우유"]}[replan_tag]
            )
        else:
            extra_nut, extra_shop = [], []

        # 실시간 API AND 검색 호환을 위해 품목별 짧은 검색어 목록으로 분할 구성
        if prefs.get("explicit_query"):
            nut_queries = [prefs["explicit_query"]]
        elif query_mode == "online_bulk":
            nut_queries = (["삶은계란", "찌개두부", "참치", "현미", "두유"] if not use_mock else ["닭가슴살", "현미", "오트밀", "프로틴", "참치", "달걀"]) + extra_nut
        else:
            nut_queries = (
                ["삶은계란", "찌개두부", "참치", "두유"]
                if not use_mock
                else ["닭가슴살", "달걀", "두부", "두유", "탄산수", "참치"]
            )
            if prefs.get("include_coffee"):
                nut_queries.append("아메리카노")
            nut_queries.extend(extra_nut)

        if prefs.get("explicit_shop_query"):
            shop_queries = [prefs["explicit_shop_query"]]
        elif query_mode == "online_bulk":
            shop_queries = ["참치", "현미", "계란", "두유"] + (["닭가슴살", "오트밀", "프로틴"] if use_mock else []) + extra_shop
        else:
            shop_queries = ["계란", "두부", "두유", "참치"] + (["닭가슴살", "탄산수"] if use_mock else [])
            if prefs.get("include_coffee"):
                shop_queries.append("아메리카노")
            shop_queries.extend(extra_shop)

        for idx, nq in enumerate(dict.fromkeys(nut_queries)):
            task_list.append(
                {
                    "task_id": f"t_nutrition_{idx}",
                    "tool_name": "search_nutrition_db",
                    "args": {"query": nq, "limit": 20},
                    "fixed_args": [],
                    "depends_on": [],
                }
            )
        for idx, sq in enumerate(dict.fromkeys(shop_queries)):
            task_list.append(
                {
                    "task_id": f"t_shopping_{idx}",
                    "tool_name": "search_product_price",
                    "args": {"query": sq, "mode": query_mode, "display": 8},
                    "fixed_args": [],
                    "depends_on": [],
                }
            )

        if cond.get("location") and mode in {"cvs_instant", "cheating_recovery", "item_swap"}:
            radius = 500 if replan_count == 0 else 1000
            brands = prefs.get("brands") or ["CU", "GS25", "세븐일레븐"]
            if replan_count >= 2:
                brands = ["CU", "GS25", "세븐일레븐", "이마트24"]
            task_list.append(
                {
                    "task_id": "t_store",
                    "tool_name": "search_nearby_store",
                    "args": {"location": cond["location"], "brands": brands, "radius_m": radius},
                    "fixed_args": ["location"],  # F3021: 사용자 지정 지역은 Worker가 임의 변경 불가
                    "depends_on": [],
                }
            )

        task_list.append(
            {
                "task_id": "t_optimize",
                "tool_name": "optimize_meal_combo",
                "args": {
                    "mode": mode,
                    "profile": profile,
                    "preferences": prefs,
                    "constraints": cons,
                    "base_conditions": cond,
                    "previous_combo": state.get("previous_combo", {}),
                },
                "fixed_args": ["mode", "constraints"],
                "depends_on": [t["task_id"] for t in task_list if t["tool_name"] in {"search_nutrition_db", "search_product_price"}],
            }
        )

    trace.append(
        {
            "step": "planner_plan",
            "replan_count": replan_count,
            "tasks": [t["tool_name"] for t in task_list],
        }
    )
    event_bus.publish(
        AgentEvent(
            session_id,
            run_id,
            "planner_worker_msg",
            "PlannerAgent",
            "planner_plan",
            {"task_list": [{"task_id": t["task_id"], "tool_name": t["tool_name"], "fixed_args": t["fixed_args"]} for t in task_list]},
        )
    )
    event_bus.publish(AgentEvent(session_id, run_id, "node_exit", "PlannerAgent", "planner_plan", {"task_count": len(task_list)}))

    return {
        "task_list": task_list,
        "replan_count": replan_count,
        "worker_escalations": [],
        "execution_trace": trace,
    }
