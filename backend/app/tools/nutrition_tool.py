"""식품의약품안전처 식품영양성분 DB API Tool (F3002, F3003)."""

from __future__ import annotations

from typing import Any

import requests
from pydantic import BaseModel, Field

from app.config import get_settings
from app.tools.base import ErrorCode, err, http_get, ok, safe_tool


class NutritionSearchInput(BaseModel):
    """영양성분 DB 조회 입력 스키마."""

    query: str = Field(..., min_length=1, description="검색할 식품명 또는 핵심 키워드 (예: '닭가슴살', '삶은계란')")
    limit: int = Field(default=8, ge=1, le=20, description="최대 반환 품목 수")


# 오프라인/키 미설정 시 사용할 검증된 한국 편의점·대량 식단 식품 마스터 (Mock fallback용)
_MOCK_FOOD_DB: list[dict[str, Any]] = [
    # 편의점/즉석 메인
    {
        "food_id": "MF001", "name": "스팀 닭가슴살 오리지널 100g", "category": "main",
        "serving_g": 100.0, "kcal": 110.0, "protein_g": 23.0, "carbs_g": 1.0, "fat_g": 1.5,
        "sugar_g": 0.5, "sodium_mg": 320.0, "has_lactose": False, "has_caffeine": False, "tags": ["닭가슴살", "편의점", "고단백", "메인", "다이어트"],
    },
    {
        "food_id": "MF002", "name": "훈제 닭가슴살 블랙페퍼 100g", "category": "main",
        "serving_g": 100.0, "kcal": 118.0, "protein_g": 22.5, "carbs_g": 2.0, "fat_g": 2.0,
        "sugar_g": 1.0, "sodium_mg": 390.0, "has_lactose": False, "has_caffeine": False, "tags": ["닭가슴살", "훈제", "편의점", "고단백", "메인"],
    },
    {
        "food_id": "MF003", "name": "닭가슴살 현미 도시락", "category": "main",
        "serving_g": 320.0, "kcal": 410.0, "protein_g": 31.0, "carbs_g": 54.0, "fat_g": 7.0,
        "sugar_g": 4.0, "sodium_mg": 780.0, "has_lactose": False, "has_caffeine": False, "tags": ["도시락", "닭가슴살", "편의점", "메인", "한끼"],
    },
    {
        "food_id": "MF004", "name": "리코타 닭가슴살 샐러드", "category": "main",
        "serving_g": 230.0, "kcal": 245.0, "protein_g": 22.0, "carbs_g": 16.0, "fat_g": 9.5,
        "sugar_g": 4.5, "sodium_mg": 460.0, "has_lactose": True, "has_caffeine": False, "tags": ["샐러드", "닭가슴살", "편의점", "치즈", "메인"],
    },
    {
        "food_id": "MF005", "name": "참치마요 삼각김밥", "category": "main",
        "serving_g": 115.0, "kcal": 205.0, "protein_g": 5.5, "carbs_g": 33.0, "fat_g": 5.5,
        "sugar_g": 1.5, "sodium_mg": 380.0, "has_lactose": False, "has_caffeine": False, "tags": ["삼각김밥", "김밥", "편의점", "메인"],
    },
    {
        "food_id": "MF006", "name": "고단백 두부면 샐러드", "category": "main",
        "serving_g": 210.0, "kcal": 210.0, "protein_g": 19.0, "carbs_g": 14.0, "fat_g": 8.0,
        "sugar_g": 3.0, "sodium_mg": 420.0, "has_lactose": False, "has_caffeine": False, "tags": ["두부", "샐러드", "편의점", "메인", "비건"],
    },
    # 서브
    {
        "food_id": "MF010", "name": "감동란 반숙 계란 2구", "category": "sub",
        "serving_g": 100.0, "kcal": 140.0, "protein_g": 12.0, "carbs_g": 1.2, "fat_g": 9.6,
        "sugar_g": 0.5, "sodium_mg": 430.0, "has_lactose": False, "has_caffeine": False, "tags": ["계란", "달걀", "삶은계란", "반숙란", "편의점", "서브", "단백질"],
    },
    {
        "food_id": "MF011", "name": "무가당 그릭요거트 100g", "category": "sub",
        "serving_g": 100.0, "kcal": 95.0, "protein_g": 10.0, "carbs_g": 4.5, "fat_g": 4.0,
        "sugar_g": 3.5, "sodium_mg": 45.0, "has_lactose": True, "has_caffeine": False, "tags": ["요거트", "그릭요거트", "유제품", "편의점", "서브"],
    },
    {
        "food_id": "MF012", "name": "단백질 두부바 오리지널", "category": "sub",
        "serving_g": 70.0, "kcal": 115.0, "protein_g": 12.0, "carbs_g": 4.0, "fat_g": 5.5,
        "sugar_g": 1.0, "sodium_mg": 260.0, "has_lactose": False, "has_caffeine": False, "tags": ["두부바", "두부", "편의점", "서브", "고단백"],
    },
    {
        "food_id": "MF013", "name": "구운 아몬드 한봉 30g", "category": "sub",
        "serving_g": 30.0, "kcal": 178.0, "protein_g": 6.2, "carbs_g": 6.0, "fat_g": 15.0,
        "sugar_g": 1.2, "sodium_mg": 5.0, "has_lactose": False, "has_caffeine": False, "tags": ["견과", "아몬드", "편의점", "서브"],
    },
    {
        "food_id": "MF014", "name": "바나나 1개", "category": "sub",
        "serving_g": 110.0, "kcal": 98.0, "protein_g": 1.2, "carbs_g": 25.0, "fat_g": 0.3,
        "sugar_g": 14.0, "sodium_mg": 1.0, "has_lactose": False, "has_caffeine": False, "tags": ["바나나", "과일", "편의점", "서브"],
    },
    # 음료
    {
        "food_id": "MF020", "name": "프로틴 드링크 초코(유청)", "category": "drink",
        "serving_g": 250.0, "kcal": 145.0, "protein_g": 20.0, "carbs_g": 8.0, "fat_g": 3.0,
        "sugar_g": 2.0, "sodium_mg": 180.0, "has_lactose": True, "has_caffeine": False, "tags": ["프로틴", "단백질음료", "음료", "유청", "편의점"],
    },
    {
        "food_id": "MF021", "name": "아몬드 브리즈 언스위트", "category": "drink",
        "serving_g": 190.0, "kcal": 35.0, "protein_g": 1.2, "carbs_g": 1.5, "fat_g": 2.8,
        "sugar_g": 0.0, "sodium_mg": 130.0, "has_lactose": False, "has_caffeine": False, "tags": ["아몬드", "식물성음료", "음료", "저당", "무유당", "편의점"],
    },
    {
        "food_id": "MF022", "name": "무가당 매일 두유 99.9", "category": "drink",
        "serving_g": 190.0, "kcal": 95.0, "protein_g": 9.5, "carbs_g": 3.0, "fat_g": 5.0,
        "sugar_g": 1.2, "sodium_mg": 110.0, "has_lactose": False, "has_caffeine": False, "tags": ["두유", "음료", "무유당", "저당", "편의점"],
    },
    {
        "food_id": "MF023", "name": "제로 콜라 355ml", "category": "drink",
        "serving_g": 355.0, "kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0,
        "sugar_g": 0.0, "sodium_mg": 25.0, "has_lactose": False, "has_caffeine": True, "tags": ["제로", "콜라", "음료", "카페인", "편의점"],
    },
    {
        "food_id": "MF024", "name": "씨그램 탄산수 플레인 350ml", "category": "drink",
        "serving_g": 350.0, "kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0,
        "sugar_g": 0.0, "sodium_mg": 5.0, "has_lactose": False, "has_caffeine": False, "tags": ["탄산수", "물", "음료", "제로", "무카페인", "편의점"],
    },
    {
        "food_id": "MF025", "name": "아이스 아메리카노 350ml", "category": "drink",
        "serving_g": 350.0, "kcal": 10.0, "protein_g": 0.5, "carbs_g": 2.0, "fat_g": 0.0,
        "sugar_g": 0.0, "sodium_mg": 10.0, "has_lactose": False, "has_caffeine": True, "tags": ["커피", "아메리카노", "카페인", "음료", "편의점"],
    },
    # 대량 구매(모드 A)
    {
        "food_id": "MF030", "name": "수비드 닭가슴살 100g", "category": "bulk_protein",
        "serving_g": 100.0, "kcal": 108.0, "protein_g": 24.0, "carbs_g": 0.8, "fat_g": 1.1,
        "sugar_g": 0.2, "sodium_mg": 280.0, "has_lactose": False, "has_caffeine": False, "tags": ["대량", "닭가슴살", "수비드", "고단백"],
    },
    {
        "food_id": "MF031", "name": "즉석 현미 곤약밥 150g", "category": "bulk_carb",
        "serving_g": 150.0, "kcal": 185.0, "protein_g": 4.2, "carbs_g": 40.0, "fat_g": 1.0,
        "sugar_g": 0.5, "sodium_mg": 15.0, "has_lactose": False, "has_caffeine": False, "tags": ["대량", "현미", "곤약밥", "밥", "탄수화물"],
    },
    {
        "food_id": "MF032", "name": "식물성 완두 프로틴 파우더 1회(30g)", "category": "bulk_protein",
        "serving_g": 30.0, "kcal": 115.0, "protein_g": 24.0, "carbs_g": 2.0, "fat_g": 1.5,
        "sugar_g": 0.5, "sodium_mg": 210.0, "has_lactose": False, "has_caffeine": False, "tags": ["대량", "프로틴", "완두", "무유당", "단백질"],
    },
    {
        "food_id": "MF033", "name": "WPC 유청 단백질 쉐이크 1회(30g)", "category": "bulk_protein",
        "serving_g": 30.0, "kcal": 120.0, "protein_g": 23.0, "carbs_g": 3.5, "fat_g": 1.8,
        "sugar_g": 1.8, "sodium_mg": 120.0, "has_lactose": True, "has_caffeine": False, "tags": ["대량", "프로틴", "유청", "단백질"],
    },
    {
        "food_id": "MF034", "name": "오트밀 롤드오츠 1회(50g)", "category": "bulk_carb",
        "serving_g": 50.0, "kcal": 190.0, "protein_g": 6.5, "carbs_g": 33.0, "fat_g": 3.5,
        "sugar_g": 0.5, "sodium_mg": 2.0, "has_lactose": False, "has_caffeine": False, "tags": ["대량", "오트밀", "귀리", "탄수화물"],
    },
    # F3003 검증용: 영양성분 필수값 누락 샘플
    {
        "food_id": "MF999", "name": "미상영양식품 테스트샘플", "category": "sub",
        "serving_g": 100.0, "kcal": None, "protein_g": None, "carbs_g": None, "fat_g": None,
        "sugar_g": None, "sodium_mg": None, "has_lactose": False, "has_caffeine": False, "tags": ["미상영양식품"],
    },
]


def _to_float(val: Any) -> float | None:
    if val in (None, "", "-", "N/A", "null"):
        return None
    try:
        cleaned = str(val).replace(",", "").replace("g", "").replace("mg", "").replace("kcal", "").strip()
        return float(cleaned)
    except ValueError:
        return None


_SNACK_BAKERY_WORDS = (
    "쿠키", "비스킷", "마카롱", "케이크", "롤케이크", "웨이퍼", "과자",
    "캔디", "초콜릿", "모닝빵", "식빵", "크림빵", "찹쌀떡", "구미", "젤리", "라면",
)
_DISH_WORDS = ("찜", "볶음", "찌개", "탕", "샐러드", "샌드위치", "김밥", "도시락", "조림", "구이", "전", "부침")


def _infer_food_category(name: str) -> str:
    """실시간 식약처 API 품목명에서 조합용 카테고리(main/sub/drink/bulk_carb/snack)를 추론한다."""
    lower = name.lower()
    if any(k in lower for k in _SNACK_BAKERY_WORDS):
        return "snack"
    if any(k in lower for k in _DISH_WORDS):
        return "main"
    if any(k in lower for k in ("현미", "곤약밥", "오트밀", "귀리", "고구마")):
        return "bulk_carb"
    if any(k in lower for k in ("계란", "달걀", "반숙란", "감동란", "구운란", "요거트", "두부바", "아몬드", "견과", "바나나")):
        return "sub"
    if any(k in lower for k in ("음료", "두유", "아몬드 브리즈", "콜라", "탄산수", "아메리카노", "커피", "드링크", "쉐이크", "주스", "우유", "라떼")):
        return "drink"
    return "main"


def _parse_api_item(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """식약처 신/구 API 응답 필드를 정규화하고 필수 영양소 누락 시 제외한다 (F3003)."""
    name = raw.get("FOOD_NM_KR") or raw.get("DESC_KOR") or ""
    food_id = raw.get("FOOD_CD") or name
    serving_g = _to_float(raw.get("SERVING_SIZE") or raw.get("SERVING_WT")) or 100.0

    kcal = _to_float(raw.get("AMT_NUM1") if "AMT_NUM1" in raw else raw.get("NUTR_CONT1"))
    protein_g = _to_float(raw.get("AMT_NUM3") if "AMT_NUM3" in raw else raw.get("NUTR_CONT3"))
    fat_g = _to_float(raw.get("AMT_NUM4") if "AMT_NUM4" in raw else raw.get("NUTR_CONT4"))
    carbs_g = _to_float(raw.get("AMT_NUM6") if "AMT_NUM6" in raw else raw.get("NUTR_CONT2"))
    sugar_g = _to_float(raw.get("AMT_NUM7") if "AMT_NUM7" in raw else raw.get("NUTR_CONT5"))
    sodium_mg = _to_float(raw.get("AMT_NUM13") if "AMT_NUM13" in raw else raw.get("NUTR_CONT6"))

    # F3003: 판단에 필요한 속성(열량, 단백질)이 없으면 임의로 0을 채우지 않고 제외
    if kcal is None or protein_g is None or carbs_g is None or fat_g is None:
        return None, f"{name or food_id}: 필수 영양성분(kcal/탄단지) 누락으로 제외"

    lower_name = name.lower()
    has_lactose = any(k in lower_name for k in ("우유", "요거트", "치즈", "유청", "라떼", "크림", "milk", "whey"))
    has_caffeine = any(k in lower_name for k in ("커피", "아메리카노", "에스프레소", "콜라", "에너지", "녹차", "말차"))

    return {
        "food_id": str(food_id),
        "name": name,
        "category": _infer_food_category(name),
        "serving_g": serving_g,
        "kcal": round(kcal, 1),
        "protein_g": round(protein_g, 1),
        "carbs_g": round(carbs_g, 1),
        "fat_g": round(fat_g, 1),
        "sugar_g": round(sugar_g, 1) if sugar_g is not None else None,
        "sodium_mg": round(sodium_mg, 1) if sodium_mg is not None else None,
        "has_lactose": has_lactose,
        "has_caffeine": has_caffeine,
    }, None


def _search_mock(query: str, limit: int) -> dict[str, Any]:
    q = query.strip().lower()
    tokens = [t for t in q.replace(",", " ").split() if t]
    verified: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []

    for row in _MOCK_FOOD_DB:
        haystack = " ".join([row["name"], row["category"], *row["tags"]]).lower()
        if not all(tok in haystack for tok in tokens):
            continue
        if row.get("category") == "snack":
            excluded.append({"name": row["name"], "reason": "과자/빵/디저트류 제외"})
            continue
        if row["kcal"] is None or row["protein_g"] is None:
            excluded.append({"name": row["name"], "reason": "MISSING_NUTRIENT_FIELDS: 열량/단백질 정보 없음(임의 채움 금지)"})
            continue
        item = {k: v for k, v in row.items() if k != "tags"}
        verified.append(item)
        if len(verified) >= limit:
            break

    if not verified and excluded:
        return err(ErrorCode.MISSING_NUTRIENT_FIELDS, f"'{query}' 조회 결과에 필수 영양성분이 비어 있어 판단할 수 없습니다.")
    if not verified:
        return err(ErrorCode.NO_NUTRITION_DATA, f"'{query}'에 해당하는 식품 영양성분 데이터를 찾지 못했습니다.")

    return ok(
        {
            "requested_query": query,
            "items": verified,
            "excluded_unverified": excluded,
            "source_mode": "mock_fallback",
            "is_estimated": True,
            "basis_note": "API 키 미설정 또는 Mock 모드 활성화로 로컬 기준 영양 DB 사용(추정 포함)",
        }
    )


@safe_tool
def search_nutrition_db(query: str, limit: int = 8) -> dict[str, Any]:
    """식약처 식품영양성분 DB API로 식품의 열량·탄단지·당류·나트륨을 조회한다."""
    if not query or not query.strip():
        return err(ErrorCode.INVALID_INPUT, "검색어(query)가 비어 있습니다.")

    settings = get_settings()
    if not settings.data_go_kr_api_key:
        if settings.use_mock:
            return _search_mock(query, limit)
        return err(ErrorCode.MISSING_API_KEY, "DATA_GO_KR_API_KEY가 설정되지 않았습니다.")

    try:
        resp = http_get(
            settings.nutrition_api_url,
            params={
                "serviceKey": settings.data_go_kr_api_key,
                "pageNo": 1,
                "numOfRows": limit,
                "type": "json",
                "FOOD_NM_KR": query.strip(),
            },
            timeout=6,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.Timeout:
        return _search_mock(query, limit) if settings.use_mock else err(ErrorCode.API_TIMEOUT, "식약처 API 응답 시간 초과")
    except Exception:
        return _search_mock(query, limit) if settings.use_mock else err(ErrorCode.API_ERROR, "식약처 API 호출 실패")

    body = payload.get("body", payload)
    raw_items = body.get("items", [])
    if isinstance(raw_items, dict):
        raw_items = raw_items.get("item", [])
    if not isinstance(raw_items, list):
        raw_items = [raw_items] if raw_items else []

    verified: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for raw in raw_items:
        parsed, reason = _parse_api_item(raw)
        if parsed:
            if parsed.get("category") == "snack":
                excluded.append({"name": parsed["name"], "reason": "과자/빵/디저트류 제외"})
            else:
                verified.append(parsed)
        elif reason:
            excluded.append({"name": raw.get("FOOD_NM_KR", "unknown"), "reason": reason})

    if not verified:
        if settings.use_mock:
            return _search_mock(query, limit)
        code = ErrorCode.MISSING_NUTRIENT_FIELDS if excluded else ErrorCode.NO_NUTRITION_DATA
        return err(code, f"'{query}'에 대한 유효한 영양성분 데이터를 찾지 못했습니다.")

    return ok(
        {
            "requested_query": query,
            "items": verified,
            "excluded_unverified": excluded,
            "source_mode": "live",
            "is_estimated": False,
            "basis_note": "식품의약품안전처 식품영양성분 DB 실시간 조회 결과",
        }
    )
