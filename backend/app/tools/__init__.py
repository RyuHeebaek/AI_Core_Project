"""Tool 명세 등록 및 레지스트리 (F3001)."""

from __future__ import annotations

from app.tools.base import ToolSpec
from app.tools.local_tool import NearbyStoreInput, search_nearby_store
from app.tools.nutrition_tool import NutritionSearchInput, search_nutrition_db
from app.tools.optimizer_tool import OptimizeComboInput, optimize_meal_combo
from app.tools.price_tool import PriceSearchInput, search_product_price

TOOL_REGISTRY: dict[str, ToolSpec] = {
    "search_nutrition_db": ToolSpec(
        name="search_nutrition_db",
        description="식품의 열량·단백질·탄수화물·지방·당류·나트륨 등 정확한 영양성분 수치를 확인해야 할 때 사용한다.",
        kind="external_api",
        args_schema=NutritionSearchInput,
        output_summary="검증된 식품 목록(items)과 영양정보 누락 제외 목록(excluded_unverified)",
        fn=search_nutrition_db,
    ),
    "search_product_price": ToolSpec(
        name="search_product_price",
        description="한국소비자원 생필품 가격정보와 Open Food Facts 보조 조회로 편의점/대량 품목의 1회분 환산 단가와 업태별 가격을 구할 때 사용한다.",
        kind="external_api",
        args_schema=PriceSearchInput,
        output_summary="업태별 단가 파싱 완료 상품 목록(products)과 파싱 실패 목록(excluded_unparsed)",
        fn=search_product_price,
    ),
    "search_nearby_store": ToolSpec(
        name="search_nearby_store",
        description="사용자가 특정 역이나 지역에서 편의점 즉시 한 끼를 원할 때 반경 내 매장 위치와 지도 링크를 찾기 위해 사용한다.",
        kind="external_api",
        args_schema=NearbyStoreInput,
        output_summary="반경 내 브랜드 편의점 이름·주소·거리·지도 링크 목록(stores)",
        fn=search_nearby_store,
    ),
    "optimize_meal_combo": ToolSpec(
        name="optimize_meal_combo",
        description="사용자 프로필 목표 역산과 영양·예산·건강 제약을 모두 충족하는 최적 식단 조합 또는 1:1 품목 스왑을 계산할 때 사용한다.",
        kind="local_server",
        args_schema=OptimizeComboInput,
        output_summary="최적 조합(selected_items), 합산 영양성분, 가격, 장보기 요약 및 제외 사유",
        fn=optimize_meal_combo,
    ),
}


def list_tool_specs() -> list[dict]:
    """등록된 모든 Tool 명세 목록을 반환한다."""
    return [spec.to_metadata() for spec in TOOL_REGISTRY.values()]
