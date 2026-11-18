"""카카오 로컬 API 기반 주변 편의점/매장 검색 Tool (F3004)."""

from __future__ import annotations

from typing import Any

import requests
from pydantic import BaseModel, Field

from app.config import get_settings
from app.tools.base import ErrorCode, err, ok, safe_tool


class NearbyStoreInput(BaseModel):
    """주변 매장 검색 입력 스키마."""

    location: str = Field(..., min_length=1, description="기준 지역 또는 역명 (예: '강남역', '역삼역')")
    brands: list[str] = Field(default_factory=lambda: ["CU", "GS25", "세븐일레븐"], description="선호 편의점 브랜드 목록")
    radius_m: int = Field(default=500, ge=100, le=2000, description="검색 반경(m), 기본 500m")


_AMBIGUOUS_LOCATIONS = {"중앙", "중앙역", "시청", "시청역", "역 앞", "중구", "동구", "서구", "남구", "북구"}

_MOCK_STORES: dict[str, list[dict[str, Any]]] = {
    "강남역": [
        {"name": "CU 강남역삼점", "brand": "CU", "address": "서울 강남구 강남대로 390", "distance_m": 120, "place_url": "https://place.map.kakao.com/mock-cu-gangnam"},
        {"name": "GS25 강남역점", "brand": "GS25", "address": "서울 강남구 테헤란로 105", "distance_m": 180, "place_url": "https://place.map.kakao.com/mock-gs-gangnam"},
        {"name": "세븐일레븐 강남대로점", "brand": "세븐일레븐", "address": "서울 서초구 강남대로 415", "distance_m": 260, "place_url": "https://place.map.kakao.com/mock-7eleven-gangnam"},
    ],
    "역삼역": [
        {"name": "CU 역삼역점", "brand": "CU", "address": "서울 강남구 테헤란로 156", "distance_m": 95, "place_url": "https://place.map.kakao.com/mock-cu-yeoksam"},
        {"name": "GS25 테헤란로점", "brand": "GS25", "address": "서울 강남구 테헤란로 212", "distance_m": 210, "place_url": "https://place.map.kakao.com/mock-gs-yeoksam"},
    ],
    "홍대입구역": [
        {"name": "GS25 홍대입구역점", "brand": "GS25", "address": "서울 마포구 양화로 160", "distance_m": 110, "place_url": "https://place.map.kakao.com/mock-gs-hongdae"},
        {"name": "CU 홍대정문점", "brand": "CU", "address": "서울 마포구 홍익로 10", "distance_m": 320, "place_url": "https://place.map.kakao.com/mock-cu-hongdae"},
    ],
}


def _search_mock_stores(location: str, brands: list[str], radius_m: int) -> dict[str, Any]:
    loc = location.strip()
    if any(bad in loc for bad in ("무인도", "매장없음")):
        return err(ErrorCode.NO_STORE_FOUND, f"'{loc}' 반경 {radius_m}m 내 편의점을 찾지 못했습니다.")

    candidates = _MOCK_STORES.get(loc)
    if not candidates:
        candidates = [
            {
                "name": f"{b} {loc}점",
                "brand": b,
                "address": f"{loc} 인근 도보권",
                "distance_m": 150 + idx * 110,
                "place_url": f"https://map.kakao.com/link/search/{loc}%20{b}",
            }
            for idx, b in enumerate(brands or ["CU", "GS25"])
        ]

    brand_set = {b.upper() for b in brands} if brands else set()
    filtered = [
        s for s in candidates
        if s["distance_m"] <= radius_m and (not brand_set or s["brand"].upper() in brand_set)
    ]
    if not filtered:
        return err(ErrorCode.NO_STORE_FOUND, f"'{loc}' 반경 {radius_m}m 내 조건에 맞는 편의점이 없습니다.")

    return ok(
        {
            "location": loc,
            "radius_m": radius_m,
            "stores": filtered,
            "stock_disclaimer": "카카오 로컬 API는 매장 위치와 지도 링크만 제공하며 매장별 실시간 재고는 임의로 보장하지 않습니다.",
            "source_mode": "mock_fallback",
            "is_estimated": True,
        }
    )


@safe_tool
def search_nearby_store(
    location: str,
    brands: list[str] | None = None,
    radius_m: int = 500,
) -> dict[str, Any]:
    """카카오 로컬 API로 기준 위치 반경 내 지정 브랜드 편의점 이름·주소·지도 링크를 조회한다."""
    loc = (location or "").strip()
    if not loc:
        return err(ErrorCode.INVALID_INPUT, "검색할 위치(location)가 비어 있습니다.")
    if loc in _AMBIGUOUS_LOCATIONS:
        return err(ErrorCode.AMBIGUOUS_LOCATION, f"'{loc}'은(는) 여러 지역에 존재합니다. 시/구 또는 구체적인 역명을 입력해 주세요.")

    target_brands = brands or ["CU", "GS25", "세븐일레븐"]
    settings = get_settings()
    if not settings.kakao_rest_api_key:
        if settings.use_mock:
            return _search_mock_stores(loc, target_brands, radius_m)
        return err(ErrorCode.MISSING_API_KEY, "KAKAO_REST_API_KEY가 설정되지 않았습니다.")

    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_api_key}"}
    try:
        geo_resp = requests.get(
            settings.kakao_local_api_url,
            headers=headers,
            params={"query": loc, "size": 1},
            timeout=6,
        )
        geo_resp.raise_for_status()
        geo_docs = geo_resp.json().get("documents", [])
        if not geo_docs:
            return err(ErrorCode.NO_STORE_FOUND, f"'{loc}' 위치를 좌표로 찾지 못했습니다.")

        x, y = geo_docs[0]["x"], geo_docs[0]["y"]
        stores: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for brand in target_brands:
            store_resp = requests.get(
                settings.kakao_local_api_url,
                headers=headers,
                params={
                    "query": brand,
                    "category_group_code": "CS2",
                    "x": x,
                    "y": y,
                    "radius": radius_m,
                    "sort": "distance",
                    "size": 5,
                },
                timeout=6,
            )
            store_resp.raise_for_status()
            for doc in store_resp.json().get("documents", []):
                doc_id = str(doc.get("id", ""))
                if doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)
                stores.append(
                    {
                        "name": doc.get("place_name", ""),
                        "brand": brand,
                        "address": doc.get("road_address_name") or doc.get("address_name", ""),
                        "distance_m": int(doc["distance"]) if doc.get("distance") else None,
                        "place_url": doc.get("place_url", ""),
                    }
                )
    except requests.Timeout:
        return _search_mock_stores(loc, target_brands, radius_m) if settings.use_mock else err(ErrorCode.API_TIMEOUT, "카카오 로컬 API 시간 초과")
    except Exception:
        return _search_mock_stores(loc, target_brands, radius_m) if settings.use_mock else err(ErrorCode.API_ERROR, "카카오 로컬 API 호출 실패")

    stores.sort(key=lambda s: s["distance_m"] if s["distance_m"] is not None else 999999)
    if not stores:
        return err(ErrorCode.NO_STORE_FOUND, f"'{loc}' 반경 {radius_m}m 내 지정 브랜드 매장이 없습니다.")

    return ok(
        {
            "location": loc,
            "radius_m": radius_m,
            "stores": stores[:8],
            "stock_disclaimer": "카카오 로컬 API는 매장 위치와 지도 링크만 제공하며 매장별 실시간 재고는 임의로 보장하지 않습니다.",
            "source_mode": "live",
            "is_estimated": False,
        }
    )
