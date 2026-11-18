"""ResponderAgent: 추천 근거·제외 사유·계산 기준·건강 도메인 면책 문구 포함 응답 포맷팅 (F3011)."""

from __future__ import annotations

from typing import Any

from app.events.event_bus import AgentEvent, event_bus
from app.models.state import AgentState

MEDICAL_DISCLAIMER = "※ 안내: 본 식단 및 영양 계산 결과는 참고용 정보이며, 의학적 진단이나 전문 영양사의 조언을 대신하지 않습니다."


def _format_reverse_calc(rc: dict[str, Any]) -> list[str]:
    lines = [
        "### 목표 영양 역산 결과",
        f"- 기초대사량(BMR): {rc['bmr_kcal']:.0f}kcal ({rc['bmr_basis']}) / 유지열량(TDEE): {rc['tdee_kcal']:.0f}kcal",
        f"- 일일 권장 목표: **{rc['target_daily_kcal']:.0f}kcal** (단백질 {rc['daily_protein_g']}g / 탄수화물 {rc['daily_carbs_g']}g / 지방 {rc['daily_fat_g']}g)",
        f"- 1끼({rc['meals_per_day']}끼 기준) 목표: **{rc['target_meal_kcal']:.0f}kcal** (단백질 {rc['meal_protein_g']}g)",
    ]
    if rc.get("guardrail_triggered"):
        lines.append(f"- ⚠️ **안전 가드레일 경고**: {rc['guardrail_warning']}")
    if mc := rc.get("mode_c_recovery"):
        lines.append(
            f"- **치팅 복구 보정(모드 C)**: 초과 {mc['excess_kcal']:.0f}kcal ÷ {mc['remain_days']}일 → "
            f"일일 **{mc['target_daily_kcal']:.0f}kcal** (최소 단백질 {mc['min_daily_protein_g']}g 고정, 탄·지 감량)"
        )
        if mc.get("guardrail_triggered"):
            lines.append(f"  - ⚠️ {mc['guardrail_warning']}")
    return lines


def responder_node(state: AgentState) -> dict[str, Any]:
    """F3011: 최종 응답 텍스트만 생성하고 종료 이벤트를 발행한다."""
    session_id = state["session_id"]
    run_id = state["run_id"]
    mode = state.get("mode", "cvs_instant")
    trace = list(state.get("execution_trace", []))

    event_bus.publish(AgentEvent(session_id, run_id, "node_enter", "ResponderAgent", "responder", {"mode": mode}))

    lines: list[str] = []
    if mode == "general_chat":
        lines.append(
            "안녕하세요! **영양성분 선형 조합 + 가성비 역산 하이브리드 식단 플래너**입니다.\n"
            "- **모드 A (온라인 대량 장바구니)**: 예) `4주 하루 2끼 15만원 이하 대량 식단 장바구니 짜줘`\n"
            "- **모드 B (편의점 한 끼)**: 예) `강남역 근처 편의점 8000원 이하 고단백 한끼 추천해줘`\n"
            "- **모드 C (치팅/외식 복구)**: 예) `체중 72kg인데 어제 1200kcal 초과했어 3일 복구 식단 짜줘`\n"
            "- **1:1 스왑 / 건강 필터**: `그릭요거트 다른 걸로 바꿔줘`, `유당불내증·저당·18시 이후 카페인 제외`"
        )
    elif state.get("clarification_needed"):
        lines.append(state.get("clarification_question", "필요한 조건을 조금 더 구체적으로 입력해 주세요."))
    elif fatal := state.get("fatal_error"):
        code = fatal.get("code") or "FATAL_ERROR"
        lines.append(f"요청을 처리할 수 없습니다 ([{code}]): **{fatal.get('message')}**")
        if fatal.get("code") == "MISSING_API_KEY":
            lines.append("`.env` 파일에 해당 API 키를 설정하거나 `USE_MOCK=true`로 실행해 주세요.")
    else:
        combo = state.get("candidate_combo") or {}
        val = state.get("validation_result") or {}
        replan_count = int(state.get("replan_count", 0))
        max_replan = int(state.get("max_replan", 3))

        if not val.get("passed"):
            reasons = val.get("stage1_errors", []) + val.get("stage2_errors", [])
            cons = state.get("constraints") or {}
            lines.append(f"제약 조건을 모두 충족하는 조합을 찾지 못했습니다 (재계획 {replan_count}/{max_replan}회).")
            if reasons:
                lines.append("- 미충족 사유: " + "; ".join(reasons))
            alts: list[str] = []
            if cons.get("budget_krw"):
                cur_b = int(cons["budget_krw"])
                sug_b = max(cur_b + 3000, int(round(cur_b * 1.5, -2))) if mode != "online_bulk" else max(cur_b + 10000, int(round(cur_b * 1.3, -3)))
                alts.append(f"예산 상한 상향 ({cur_b:,}원 → 약 {sug_b:,}원 이상)")
            if cons.get("min_meal_protein_g"):
                cur_p = float(cons["min_meal_protein_g"])
                alts.append(f"끼니당 최소 단백질 완화 ({cur_p:.0f}g → {max(15.0, cur_p * 0.8):.0f}g)")
            alt_text = ", ".join(alts) if alts else "예산 상한, 최소 단백질 기준 또는 기피/건강 제약"
            lines.append(f"- 조정 가능한 대안: {alt_text}으로 조정해 다시 요청해 주세요.")
        else:
            if rc := combo.get("reverse_calc"):
                lines.extend(_format_reverse_calc(rc))
                lines.append("")

            if items := combo.get("selected_items"):
                ns = combo.get("nutrition_summary", {})
                if combo.get("swapped_from"):
                    lines.append(f"### 1:1 품목 스왑 결과 (`{combo['swapped_from']}` → `{combo['swapped_to']}`)")
                else:
                    lines.append(f"### 추천 식단 조합 (모드 {mode})")

                for idx, it in enumerate(items, 1):
                    lines.append(
                        f"{idx}. **{it['name']}** — {it['unit_price_krw']:,}원 ({it.get('unit_basis', '1회분')}) | "
                        f"{it['kcal']}kcal, 단백질 {it['protein_g']}g, 탄수 {it['carbs_g']}g, 지방 {it['fat_g']}g, 당류 {it.get('sugar_g', 0)}g "
                        f"| 단백질밀도 {it.get('protein_density_pct', 0)}%, 천원당 {it.get('protein_per_1000krw', 0)}g"
                    )
                    if it.get("link"):
                        lines.append(f"   - 구매/상품 참고: {it['link']}")

                lines.append(
                    f"\n**합산 수치**: **{ns.get('total_price_krw', 0):,}원** | "
                    f"{ns.get('total_kcal', 0)}kcal | 단백질 **{ns.get('total_protein_g', 0)}g** | "
                    f"탄수 {ns.get('total_carbs_g', 0)}g | 지방 {ns.get('total_fat_g', 0)}g | 당류 {ns.get('total_sugar_g', 0)}g "
                    f"(단백질 밀도 {ns.get('protein_density_pct', 0)}%, 천원당 {ns.get('protein_per_1000krw', 0)}g)"
                )

            if shop_sum := combo.get("shopping_summary"):
                if shop_sum.get("type") == "online_bulk":
                    lines.append(
                        f"\n### 대량 장바구니 요약 ({shop_sum['period_weeks']}주 총 {shop_sum['total_meals']}끼, "
                        f"결제 예상 **{shop_sum['total_checkout_krw']:,}원** / 끼니당 약 {combo.get('per_meal_price_krw', 0):,}원)"
                    )
                    for ord_item in shop_sum.get("orders", []):
                        lines.append(
                            f"- {ord_item['product_title']}: **{ord_item['boxes_needed']}묶음** "
                            f"({ord_item['line_total_krw']:,}원) — {ord_item['link']}"
                        )

            if stores := combo.get("stores"):
                lines.append("\n### 주변 편의점 안내 (반경 내)")
                for st in stores[:4]:
                    dist = f" ({st['distance_m']}m)" if st.get("distance_m") is not None else ""
                    lines.append(f"- **{st['name']}**{dist} — {st['address']} | {st['place_url']}")

            if escalations := state.get("worker_escalations"):
                lines.append("\n### 일부 보조 조회 제한 안내")
                for esc in escalations:
                    lines.append(f"- {esc.get('tool_name', 'Tool')}: {esc.get('reason', '조회 실패')} (나머지 식단 추천은 정상 완료)")

            if excluded := combo.get("excluded_candidates"):
                lines.append("\n### 제외된 후보 및 사유")
                for ex in excluded[:5]:
                    label = ex.get("name") or ex.get("title") or "후보"
                    lines.append(f"- {label}: {ex.get('reason', '제약 미충족')}")

            if notes := combo.get("basis_notes"):
                lines.append("\n### 데이터 출처 및 계산 근거")
                for n in notes:
                    lines.append(f"- {n}")

        lines.append(f"\n{MEDICAL_DISCLAIMER}")

    reply = "\n".join(lines).strip()
    trace.append({"step": "responder"})
    event_bus.publish(AgentEvent(session_id, run_id, "node_exit", "ResponderAgent", "responder", {"reply_length": len(reply)}))

    return {
        "final_response": reply,
        "execution_trace": trace,
    }
