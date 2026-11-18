"""로컬 서버 Tool: 사용자 목표 역산 엔진 + 선형 조합 최적화 + 1:1 품목 스왑 (F3001, F3008)."""

from __future__ import annotations

import itertools
import math
import re
from typing import Any

from pydantic import BaseModel, Field

from app.tools.base import ErrorCode, err, ok, safe_tool

CALORIE_FLOOR = {"male": 1400.0, "female": 1100.0}
_UNIT_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?(?:kg|g|ml|l|팩|봉|개|병|캔|입|구|포|박스|회분)?$", re.IGNORECASE)
_GENERIC_MATCH_WORDS = frozenset(
    {
        "오리지널", "플레인", "무가당", "언스위트", "편의점", "대용량", "기준가",
        "스팀", "훈제", "구운", "반숙", "수비드", "즉석", "저당", "고단백",
        "다이어트", "약", "한판", "한봉", "블랙페퍼", "아이스", "매일", "허닭", "아임닭",
    }
)


class OptimizeComboInput(BaseModel):
    """선형 조합 최적화 입력 스키마."""

    mode: str = Field(
        ...,
        description="online_bulk(대량 장바구니) | cvs_instant(편의점 조합) | cheating_recovery(치팅 복구) | profile_calc | item_swap",
    )
    nutrition_items: list[dict[str, Any]] = Field(default_factory=list)
    shopping_products: list[dict[str, Any]] = Field(default_factory=list)
    profile: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    base_conditions: dict[str, Any] = Field(default_factory=dict)
    previous_combo: dict[str, Any] = Field(default_factory=dict)


def calculate_profile_targets(profile: dict[str, Any]) -> dict[str, Any]:
    """성별/체중/목표 체중/기간으로 BMR·TDEE·일일/끼니별 목표 영양소를 역산하고 안전 하한선을 검사한다 (F3008)."""
    gender = profile.get("gender", "male")
    weight = float(profile["weight_kg"])
    goal_type = profile.get("goal_type", "diet")
    meals_per_day = int(profile.get("meals_per_day") or 3)
    target_weight = float(profile.get("target_weight_kg") or weight)
    target_weeks = int(profile.get("target_weeks") or 4)
    activity = float(profile.get("activity_factor") or 1.375)

    height = profile.get("height_cm")
    age = profile.get("age")
    if height and age:
        bmr = 10 * weight + 6.25 * float(height) - 5 * int(age) + (5 if gender == "male" else -161)
        bmr_basis = "Mifflin-St Jeor 공식"
    else:
        # 키/나이 미입력 시 임의 신장/연령을 지어내지 않고 체중 기반 표준 추정식 사용
        bmr = weight * (24.0 if gender == "male" else 21.6)
        bmr_basis = "신장/나이 미입력으로 체중 기반 표준 기초대사량 공식 적용"

    tdee = round(bmr * activity, 1)
    total_days = max(1, target_weeks * 7)
    daily_delta = ((target_weight - weight) * 7700.0) / total_days
    raw_daily_kcal = round(tdee + daily_delta, 1)

    floor = CALORIE_FLOOR["male" if gender == "male" else "female"]
    guardrail_triggered = raw_daily_kcal < floor
    recommended_weeks: int | None = None
    effective_daily_kcal = raw_daily_kcal
    guardrail_warning = ""

    if guardrail_triggered:
        effective_daily_kcal = floor
        max_safe_deficit = max(150.0, tdee - floor)
        if target_weight < weight:
            recommended_weeks = math.ceil(((weight - target_weight) * 7700.0) / (max_safe_deficit * 7.0))
        guardrail_warning = (
            f"역산된 일일 칼로리({raw_daily_kcal:.0f}kcal)가 안전 하한선({floor:.0f}kcal) 미만입니다. "
            f"근손실·대사 저하 방지를 위해 하한선 {floor:.0f}kcal로 보정했으며"
            + (f", 권장 감량 기간은 최소 {recommended_weeks}주입니다." if recommended_weeks else ".")
        )

    protein_mult = 1.8 if goal_type == "bulk" else 1.6
    daily_protein_g = round(weight * protein_mult, 1)
    daily_fat_g = round(max(weight * 0.6, (effective_daily_kcal * 0.25) / 9.0), 1)
    remaining_kcal = max(80.0, effective_daily_kcal - daily_protein_g * 4.0 - daily_fat_g * 9.0)
    daily_carbs_g = round(remaining_kcal / 4.0, 1)

    return {
        "bmr_kcal": round(bmr, 1),
        "tdee_kcal": tdee,
        "bmr_basis": bmr_basis,
        "raw_target_daily_kcal": raw_daily_kcal,
        "target_daily_kcal": round(effective_daily_kcal, 1),
        "target_meal_kcal": round(effective_daily_kcal / meals_per_day, 1),
        "daily_protein_g": daily_protein_g,
        "meal_protein_g": round(daily_protein_g / meals_per_day, 1),
        "daily_carbs_g": daily_carbs_g,
        "meal_carbs_g": round(daily_carbs_g / meals_per_day, 1),
        "daily_fat_g": daily_fat_g,
        "meal_fat_g": round(daily_fat_g / meals_per_day, 1),
        "meals_per_day": meals_per_day,
        "safety_floor_kcal": floor,
        "guardrail_triggered": guardrail_triggered,
        "recommended_weeks": recommended_weeks,
        "guardrail_warning": guardrail_warning,
    }


def calculate_mode_c_targets(
    excess_kcal: float,
    remain_days: int,
    base_daily_kcal: float,
    weight_kg: float,
    gender: str = "male",
    meals_per_day: int = 3,
) -> dict[str, Any]:
    """모드 C: 초과 칼로리 복구 목표 및 단백질 고정·탄지 감량 타겟을 계산한다."""
    remain = max(1, remain_days)
    raw_adjusted = round(base_daily_kcal - (excess_kcal / remain), 1)
    floor = CALORIE_FLOOR["male" if gender == "male" else "female"]
    guardrail_triggered = raw_adjusted < floor
    adjusted_daily = floor if guardrail_triggered else raw_adjusted

    recommended_days = None
    warning = ""
    if guardrail_triggered:
        safe_daily_cut = max(200.0, base_daily_kcal - floor)
        recommended_days = math.ceil(excess_kcal / safe_daily_cut)
        warning = (
            f"복구 보정 칼로리({raw_adjusted:.0f}kcal)가 안전 하한선({floor:.0f}kcal) 미만이라 "
            f"{floor:.0f}kcal로 보정했습니다. 무리 없는 복구 권장 일수는 최소 {recommended_days}일입니다."
        )

    min_daily_protein = round(weight_kg * 1.6, 1)
    # 근손실 방지를 위해 최소 단백질 고정, 잔여 열량만 탄·지로 배분
    rem_kcal = max(120.0, adjusted_daily - min_daily_protein * 4.0)
    daily_fat = round((rem_kcal * 0.30) / 9.0, 1)
    daily_carbs = round((rem_kcal * 0.70) / 4.0, 1)

    return {
        "base_daily_kcal": base_daily_kcal,
        "excess_kcal": excess_kcal,
        "remain_days": remain,
        "raw_adjusted_daily_kcal": raw_adjusted,
        "target_daily_kcal": adjusted_daily,
        "target_meal_kcal": round(adjusted_daily / meals_per_day, 1),
        "min_daily_protein_g": min_daily_protein,
        "min_meal_protein_g": round(min_daily_protein / meals_per_day, 1),
        "daily_carbs_g": daily_carbs,
        "daily_fat_g": daily_fat,
        "guardrail_triggered": guardrail_triggered,
        "recommended_recovery_days": recommended_days,
        "guardrail_warning": warning,
    }


def compute_item_metrics(item: dict[str, Any]) -> dict[str, Any]:
    """단백질 밀도(%)와 1,000원당 단백질(g) 가성비 지수를 계산한다."""
    kcal = float(item.get("kcal") or 0.0)
    protein = float(item.get("protein_g") or 0.0)
    price = int(item.get("unit_price_krw") or item.get("price_krw") or 0)
    density = round((protein * 4.0 / kcal) * 100.0, 1) if kcal > 0 else 0.0
    value_idx = round((protein / price) * 1000.0, 2) if price > 0 else 0.0
    return {**item, "protein_density_pct": density, "protein_per_1000krw": value_idx}


def _food_noun_tokens(text: str) -> set[str]:
    cleaned = re.sub(r"[()\[\],/_]", " ", text.lower())
    tokens: set[str] = set()
    for tok in cleaned.split():
        if len(tok) < 2 or _UNIT_TOKEN_RE.match(tok) or tok in _GENERIC_MATCH_WORDS:
            continue
        if tok == "달걀":
            tok = "계란"
        tokens.add(tok)
    return tokens


def _scale_nutrition_to_product(n_item: dict[str, Any], prod: dict[str, Any]) -> dict[str, Any]:
    """실제 상품 1회분 중량과 영양 DB 기준 중량이 다를 경우 영양성분을 비례 환산한다."""
    base_serving = float(n_item.get("serving_g") or 0.0)
    unit_weight = prod.get("unit_weight_g")
    pack_count = int(prod.get("pack_count") or 1)
    servings_per_purchase = float(prod.get("servings_per_purchase") or 1.0)
    if not unit_weight or base_serving <= 0:
        return n_item

    if pack_count == 1 and servings_per_purchase > 1:
        per_serving_g = float(unit_weight) / servings_per_purchase
    elif pack_count > 1 or float(unit_weight) <= 500.0:
        per_serving_g = float(unit_weight)
    else:
        return n_item

    scale = per_serving_g / base_serving
    if abs(scale - 1.0) <= 0.05 or not (0.2 <= scale <= 6.0):
        return n_item

    scaled = dict(n_item)
    scaled["serving_g"] = round(per_serving_g, 1)
    for key in ("kcal", "protein_g", "carbs_g", "fat_g", "sugar_g", "sodium_mg"):
        if scaled.get(key) is not None:
            scaled[key] = round(float(scaled[key]) * scale, 1)
    return scaled


def _merge_nutrition_and_price(
    nutrition_items: list[dict[str, Any]],
    shopping_products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """영양 DB 항목과 쇼핑 단가 항목을 핵심 식품명 토큰 일치로 매칭하고 중량을 맞춘다."""
    merged: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []

    for n_item in nutrition_items:
        n_name = n_item["name"]
        n_tokens = _food_noun_tokens(n_name)
        best_prod = None
        best_overlap = 0
        for prod in shopping_products:
            p_tokens = _food_noun_tokens(prod["title"])
            # 정확한 핵심 식품명 교집합 또는 복합어 포함(예: "현미"+"곤약밥", "계란" in "구운계란")만 허용
            overlap = sum(
                1 for nt in n_tokens
                if nt in p_tokens or any(nt in pt or pt in nt for pt in p_tokens)
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_prod = prod

        if not best_prod or best_overlap == 0:
            excluded.append({"name": n_name, "reason": "단가 정보 매칭 불가로 조합 후보에서 제외"})
            continue

        scaled_nutrition = _scale_nutrition_to_product(n_item, best_prod)
        combined = compute_item_metrics(
            {
                **scaled_nutrition,
                "product_title": best_prod["title"],
                "unit_price_krw": best_prod["unit_price_krw"],
                "bundle_price_krw": best_prod["bundle_price_krw"],
                "servings_per_purchase": best_prod["servings_per_purchase"],
                "unit_basis": best_prod["unit_basis"],
                "mall_name": best_prod["mall_name"],
                "link": best_prod["link"],
            }
        )
        merged.append(combined)
    return merged, excluded


def _apply_health_filters(
    items: list[dict[str, Any]],
    constraints: dict[str, Any],
    preferences: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """유당불내증·18시 이후 카페인·기피/알레르기·저당 단일품목 필터를 적용한다."""
    lactose_free = bool(constraints.get("lactose_free"))
    no_caffeine = bool(constraints.get("no_caffeine"))
    low_sugar = bool(constraints.get("low_sugar"))
    disliked = [d.lower() for d in (preferences or {}).get("disliked_ingredients", []) if d]

    passed: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []

    for item in items:
        name_lower = item["name"].lower()
        if lactose_free and item.get("has_lactose"):
            excluded.append({"name": item["name"], "reason": "유당 포함(유당불내증 제약)"})
            continue
        if no_caffeine and item.get("has_caffeine"):
            excluded.append({"name": item["name"], "reason": "카페인 포함(야간/무카페인 제약)"})
            continue
        if low_sugar and item.get("sugar_g") is not None and float(item["sugar_g"]) > 10.0:
            excluded.append({"name": item["name"], "reason": f"당류 {item['sugar_g']}g > 10g(저당 모드 초과)"})
            continue
        matched_dislike = next((d for d in disliked if d in name_lower), None)
        if matched_dislike:
            excluded.append({"name": item["name"], "reason": f"기피/알레르기 식재료('{matched_dislike}') 포함"})
            continue
        passed.append(item)

    return passed, excluded


def summarize_combo(items: list[dict[str, Any]]) -> dict[str, Any]:
    """선택된 품목들의 합산 영양성분·가격·효율 지표를 계산한다."""
    total_kcal = round(sum(float(i["kcal"]) for i in items), 1)
    total_protein = round(sum(float(i["protein_g"]) for i in items), 1)
    total_carbs = round(sum(float(i["carbs_g"]) for i in items), 1)
    total_fat = round(sum(float(i["fat_g"]) for i in items), 1)
    sugars = [float(i["sugar_g"]) for i in items if i.get("sugar_g") is not None]
    sodiums = [float(i["sodium_mg"]) for i in items if i.get("sodium_mg") is not None]
    total_sugar = round(sum(sugars), 1)
    total_sodium = round(sum(sodiums), 1)
    total_price = int(sum(int(i["unit_price_krw"]) for i in items))
    protein_density = round((total_protein * 4.0 / total_kcal) * 100.0, 1) if total_kcal > 0 else 0.0
    protein_per_1000 = round((total_protein / total_price) * 1000.0, 2) if total_price > 0 else 0.0

    return {
        "total_kcal": total_kcal,
        "total_protein_g": total_protein,
        "total_carbs_g": total_carbs,
        "total_fat_g": total_fat,
        "total_sugar_g": total_sugar,
        "total_sodium_mg": total_sodium,
        "total_price_krw": total_price,
        "protein_density_pct": protein_density,
        "protein_per_1000krw": protein_per_1000,
    }


def check_meal_constraints(
    items: list[dict[str, Any]],
    summary: dict[str, Any],
    constraints: dict[str, Any],
    preferences: dict[str, Any] | None = None,
    total_price_override: int | None = None,
    default_min_protein: float | None = None,
) -> list[str]:
    """최적화기와 검증기에서 공통으로 사용하는 단일 제약 검사 함수."""
    errors: list[str] = []
    budget = constraints.get("budget_krw")
    price = total_price_override if total_price_override is not None else summary["total_price_krw"]
    if budget is not None and int(price) > int(budget):
        errors.append(f"예산 초과: {int(price):,}원 > 상한 {int(budget):,}원")

    min_protein = constraints.get("min_meal_protein_g")
    if min_protein is None:
        min_protein = default_min_protein
    if min_protein is not None and summary["total_protein_g"] < float(min_protein) * 0.85:
        errors.append(f"단백질 달성률 미달(85% 미만): {summary['total_protein_g']}g < {float(min_protein)*0.85:.1f}g")

    target_kcal = constraints.get("target_meal_kcal")
    if target_kcal is not None and summary["total_kcal"] > float(target_kcal) * 1.10:
        errors.append(f"다이어트/목표 칼로리 상한(110%) 초과: {summary['total_kcal']}kcal > {float(target_kcal)*1.10:.0f}kcal")

    if constraints.get("lactose_free"):
        bad = [i["name"] for i in items if i.get("has_lactose")]
        if bad:
            errors.append(f"유당불내증 제약 위반 품목 포함: {', '.join(bad)}")

    if constraints.get("no_caffeine"):
        bad = [i["name"] for i in items if i.get("has_caffeine")]
        if bad:
            errors.append(f"18시 이후/무카페인 제약 위반 품목 포함: {', '.join(bad)}")

    max_sugar = constraints.get("max_sugar_g")
    if constraints.get("low_sugar") or max_sugar is not None:
        sugar_limit = float(max_sugar if max_sugar is not None else 10.0)
        if summary["total_sugar_g"] > sugar_limit:
            errors.append(f"저당 모드 당류 상한 초과: {summary['total_sugar_g']}g > {sugar_limit}g")

    disliked = [d.lower() for d in (preferences or {}).get("disliked_ingredients", []) if d]
    for it in items:
        for d in disliked:
            if d in it["name"].lower():
                errors.append(f"기피 식재료('{d}') 포함: {it['name']}")

    return errors


def _optimize_mode_a(
    items: list[dict[str, Any]],
    constraints: dict[str, Any],
    base_conditions: dict[str, Any],
    preferences: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """모드 A: 설정 기간(총 끼니 수) 예산 내 온라인 대량 장바구니 포트폴리오 탐색."""
    weeks = int(base_conditions.get("period_weeks") or 4)
    meals_per_day = int(base_conditions.get("meals_per_day") or 2)
    total_meals = max(1, int(base_conditions.get("total_meals") or weeks * 7 * meals_per_day))
    total_budget = int(constraints.get("budget_krw") or 250000)

    proteins = [i for i in items if i.get("category") in {"bulk_protein", "main", "drink"}]
    carbs = [i for i in items if i.get("category") in {"bulk_carb", "sub"}]
    if not proteins:
        proteins = items

    best_portfolio = None
    best_score = -1e9
    excluded: list[dict[str, str]] = []

    pairs = list(itertools.product(proteins, carbs)) if carbs else [(p, None) for p in proteins]
    for p_item, c_item in pairs:
        combo_items = [p_item] + ([c_item] if c_item else [])
        summary = summarize_combo(combo_items)
        line_orders = []
        checkout_total = 0
        for item in combo_items:
            servings_per_box = float(item.get("servings_per_purchase") or 1.0)
            boxes = math.ceil(total_meals / servings_per_box)
            cost = boxes * int(item["bundle_price_krw"])
            checkout_total += cost
            line_orders.append(
                {
                    "name": item["name"],
                    "product_title": item["product_title"],
                    "boxes_needed": boxes,
                    "servings_per_box": servings_per_box,
                    "bundle_price_krw": item["bundle_price_krw"],
                    "line_total_krw": cost,
                    "unit_price_krw": item["unit_price_krw"],
                    "link": item["link"],
                }
            )
        errs = check_meal_constraints(
            combo_items,
            summary,
            constraints,
            preferences=preferences,
            total_price_override=checkout_total,
            default_min_protein=20.0,
        )
        if errs:
            excluded.append({"name": " + ".join(i["name"] for i in combo_items), "reason": "; ".join(errs)})
            continue

        score = summary["protein_per_1000krw"] * 10.0 + summary["protein_density_pct"] - (checkout_total / max(1, total_budget)) * 20.0
        if score > best_score:
            best_score = score
            best_portfolio = {
                "selected_items": combo_items,
                "nutrition_summary": summary,
                "total_price_krw": checkout_total,
                "per_meal_price_krw": round(checkout_total / total_meals),
                "shopping_summary": {
                    "type": "online_bulk",
                    "period_weeks": weeks,
                    "total_meals": total_meals,
                    "total_checkout_krw": checkout_total,
                    "orders": line_orders,
                },
            }
    return best_portfolio, excluded


def _optimize_meal_set(
    items: list[dict[str, Any]],
    constraints: dict[str, Any],
    mode: str,
    preferences: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """모드 B/C: [메인 + 서브 + 음료] 2~3품목 선형 조합 최적화."""
    target_kcal = constraints.get("target_meal_kcal")
    mains = [i for i in items if i.get("category") in {"main", "bulk_protein"}]
    subs = [i for i in items if i.get("category") == "sub"]
    drinks = [i for i in items if i.get("category") == "drink"]
    if not mains:
        mains = items

    combos: list[list[dict[str, Any]]] = []
    for m in mains:
        for s in subs:
            combos.append([m, s])
            for d in drinks:
                combos.append([m, s, d])
        for d in drinks:
            combos.append([m, d])
        if not subs and not drinks:
            combos.append([m])

    best: dict[str, Any] | None = None
    best_score = -1e9
    excluded: list[dict[str, str]] = []

    for combo in combos:
        summary = summarize_combo(combo)
        errs = check_meal_constraints(
            combo,
            summary,
            constraints,
            preferences=preferences,
            default_min_protein=25.0,
        )
        if errs:
            excluded.append({"name": " + ".join(c["name"] for c in combo), "reason": "; ".join(errs)})
            continue

        # 목적함수: 단백질 효율 + 가성비 + (cheating_recovery 모드는 초저칼로리·고단백 가중)
        kcal_penalty = (summary["total_kcal"] / 100.0) * (4.0 if mode == "cheating_recovery" else 0.8)
        target_fit = -abs(summary["total_kcal"] - float(target_kcal)) * 0.05 if target_kcal else 0.0
        score = summary["protein_density_pct"] * 1.2 + summary["protein_per_1000krw"] * 4.0 + target_fit - kcal_penalty + len(combo) * 2.5

        if score > best_score:
            best_score = score
            best = {
                "selected_items": combo,
                "nutrition_summary": summary,
                "total_price_krw": summary["total_price_krw"],
                "shopping_summary": {
                    "type": "convenience_checklist",
                    "checklist": [
                        {
                            "name": c["name"],
                            "category": c["category"],
                            "unit_price_krw": c["unit_price_krw"],
                            "protein_g": c["protein_g"],
                            "kcal": c["kcal"],
                        }
                        for c in combo
                    ],
                },
            }

    return best, excluded[:6]


def _swap_single_item(
    previous_combo: dict[str, Any],
    swap_target: str,
    pool: list[dict[str, Any]],
    constraints: dict[str, Any],
    preferences: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """기존 조합에서 특정 품목만 1:1 핀셋 교체한다 (차액 영양소/예산 보전)."""
    prev_items = previous_combo.get("selected_items", [])
    if not prev_items:
        return None, [{"name": swap_target, "reason": "이전 추천 조합이 없어 스왑 불가"}]

    target_lower = swap_target.lower()
    idx_to_replace = next(
        (idx for idx, it in enumerate(prev_items) if target_lower in it["name"].lower() or target_lower == it.get("category", "").lower()),
        None,
    )
    if idx_to_replace is None:
        return None, [{"name": swap_target, "reason": f"기존 조합에서 '{swap_target}' 품목을 찾지 못함"}]

    removed = prev_items[idx_to_replace]
    kept = [it for idx, it in enumerate(prev_items) if idx != idx_to_replace]
    kept_names = {it["name"] for it in kept} | {removed["name"]}

    candidates = [p for p in pool if p["name"] not in kept_names and p.get("category") == removed.get("category")]
    if not candidates:
        candidates = [p for p in pool if p["name"] not in kept_names]

    best = None
    best_score = -1e9
    excluded: list[dict[str, str]] = []
    for cand in candidates:
        new_items = [*kept, cand]
        summary = summarize_combo(new_items)
        errs = check_meal_constraints(
            new_items,
            summary,
            constraints,
            preferences=preferences,
            default_min_protein=20.0,
        )
        if errs:
            excluded.append({"name": cand["name"], "reason": "; ".join(errs)})
            continue

        score = cand["protein_density_pct"] + cand["protein_per_1000krw"] * 5.0
        if score > best_score:
            best_score = score
            best = {
                "selected_items": new_items,
                "nutrition_summary": summary,
                "total_price_krw": summary["total_price_krw"],
                "swapped_from": removed["name"],
                "swapped_to": cand["name"],
                "shopping_summary": {
                    "type": "swap_update",
                    "swapped_from": removed["name"],
                    "swapped_to": cand["name"],
                    "checklist": [{"name": c["name"], "unit_price_krw": c["unit_price_krw"]} for c in new_items],
                },
            }
    return best, excluded


@safe_tool
def optimize_meal_combo(
    mode: str,
    nutrition_items: list[dict[str, Any]] | None = None,
    shopping_products: list[dict[str, Any]] | None = None,
    profile: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
    base_conditions: dict[str, Any] | None = None,
    previous_combo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """프로필 목표 역산 및 영양·가격·건강제약을 만족하는 최적 식단 조합을 계산하는 로컬 서버 Tool."""
    prof = profile or {}
    prefs = dict(preferences or {})
    if prof.get("disliked_ingredients"):
        prefs["disliked_ingredients"] = list(dict.fromkeys([*prefs.get("disliked_ingredients", []), *prof["disliked_ingredients"]]))
    cons = dict(constraints or {})
    cond = base_conditions or {}

    reverse_calc = None
    if prof.get("weight_kg") and prof.get("gender"):
        reverse_calc = calculate_profile_targets(prof)
        if mode == "cheating_recovery" and cond.get("excess_kcal") and cond.get("remain_days"):
            mode_c = calculate_mode_c_targets(
                excess_kcal=float(cond["excess_kcal"]),
                remain_days=int(cond["remain_days"]),
                base_daily_kcal=reverse_calc["target_daily_kcal"],
                weight_kg=float(prof["weight_kg"]),
                gender=prof.get("gender", "male"),
                meals_per_day=int(prof.get("meals_per_day") or 3),
            )
            reverse_calc["mode_c_recovery"] = mode_c
            cons.setdefault("target_meal_kcal", mode_c["target_meal_kcal"])
            cons.setdefault("min_meal_protein_g", mode_c["min_meal_protein_g"])
        else:
            cons.setdefault("target_meal_kcal", reverse_calc["target_meal_kcal"])
            cons.setdefault("min_meal_protein_g", reverse_calc["meal_protein_g"])

    if mode == "profile_calc":
        if not reverse_calc:
            return err(ErrorCode.INVALID_INPUT, "프로필 역산에는 성별과 현재 체중(kg)이 필요합니다.")
        return ok({"reverse_calc": reverse_calc, "selected_items": [], "nutrition_summary": {}, "excluded_candidates": []})

    merged, merge_excluded = _merge_nutrition_and_price(nutrition_items or [], shopping_products or [])
    filtered, health_excluded = _apply_health_filters(merged, cons, prefs)
    all_excluded = merge_excluded + health_excluded

    if not filtered:
        return err(ErrorCode.NO_FEASIBLE_COMBO, "영양/가격 매칭 및 건강 제약을 통과한 후보 품목이 없습니다.")

    if mode == "item_swap":
        swap_target = str(cond.get("swap_target") or "")
        result, combo_excluded = _swap_single_item(previous_combo or {}, swap_target, filtered, cons, prefs)
    elif mode == "online_bulk":
        result, combo_excluded = _optimize_mode_a(filtered, cons, cond, prefs)
    else:
        result, combo_excluded = _optimize_meal_set(filtered, cons, mode, prefs)

    all_excluded.extend(combo_excluded)
    if not result:
        return err(
            ErrorCode.NO_FEASIBLE_COMBO,
            f"예산/단백질/열량/건강 제약 조건을 동시에 만족하는 조합을 찾지 못했습니다. (제외 {len(all_excluded)}건)",
        )

    return ok(
        {
            **result,
            "reverse_calc": reverse_calc,
            "applied_constraints": cons,
            "excluded_candidates": all_excluded[:8],
        }
    )
