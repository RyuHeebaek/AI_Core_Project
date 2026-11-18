"""한국소비자원 생필품 가격정보 API(XML) + Open Food Facts 보조 연동 및 단가 환산 Tool (F3003, F3008)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Any

import requests
from pydantic import BaseModel, Field

from app.config import get_settings
from app.tools.base import ErrorCode, err, ok, safe_tool

CVS_DISCLAIMER = "조사 기준가 기반 역산 금액이며 매장별 1+1/2+1 행사 적용 시 실제 결제액이 달라질 수 있습니다."
OPEN_FOOD_FACTS_URL = "https://world.openfoodfacts.org/cgi/search.pl"


class PriceSearchInput(BaseModel):
    """상품 가격 및 1회분 단가 조회 입력 스키마."""

    query: str = Field(..., min_length=1, description="검색할 상품 키워드 (예: '닭가슴살 10팩', '감동란')")
    mode: str = Field(default="cvs_instant", description="실행 모드: cvs_instant(편의점) | online_bulk(대량) | cheating_recovery | item_swap")
    display: int = Field(default=10, ge=1, le=30, description="조회 결과 개수")
    default_serving_g: float = Field(default=100.0, gt=0, description="벌크 중량 상품 환산용 1회분 기본 중량(g)")


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_PACK_RE = re.compile(r"(\d+)\s*(팩|봉|개|병|캔|입|구|포|박스)")
_PLUS_PACK_RE = re.compile(
    r"(\d+)(?!\s*(?:kg|g|ml|l)\b)\s*(?:팩|봉|개|병|캔|입|구|포|박스)?\s*\+\s*(\d+)(?!\s*(?:kg|g|ml|l)\b)\s*(?:팩|봉|개|병|캔|입|구|포|박스)?",
    re.IGNORECASE,
)
_MUL_PACK_RE = re.compile(r"(\d+)\s*[xX×*]\s*(\d+)\s*(?:팩|봉|개|병|캔|입|구|포|박스)?")
_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b", re.IGNORECASE)

_CVS_CHANNEL_KEYWORDS = ("cu", "gs25", "세븐일레븐", "이마트24", "미니스톱", "편의점", "cvs")
_MART_CHANNEL_KEYWORDS = ("이마트", "홈플러스", "롯데마트", "하나로", "대형마트", "할인점", "마트", "백화점")

_MOCK_PRICE_DB: list[dict[str, Any]] = [
    # 대형마트/온라인 대량 (online_bulk)
    {
        "product_id": "CP001", "title": "허닭 스팀 닭가슴살 오리지널 100g 30팩",
        "price": 41900, "channel": "대형마트/할인점", "retail_type": "mart_bulk",
        "link": "https://www.price.go.kr/mock/cp001", "category": "bulk_protein",
        "tags": ["닭가슴살", "스팀", "대량", "100g", "30팩"],
    },
    {
        "product_id": "CP002", "title": "아임닭 수비드 닭가슴살 100g 10팩",
        "price": 15900, "channel": "대형마트/할인점", "retail_type": "mart_bulk",
        "link": "https://www.price.go.kr/mock/cp002", "category": "bulk_protein",
        "tags": ["닭가슴살", "수비드", "대량", "100g", "10팩"],
    },
    {
        "product_id": "CP003", "title": "즉석 현미 곤약밥 150g 12개",
        "price": 14400, "channel": "대형마트/할인점", "retail_type": "mart_bulk",
        "link": "https://www.price.go.kr/mock/cp003", "category": "bulk_carb",
        "tags": ["현미", "곤약밥", "밥", "대량", "12개"],
    },
    {
        "product_id": "CP004", "title": "오트밀 롤드오츠 대용량 1kg",
        "price": 6900, "channel": "대형마트/할인점", "retail_type": "mart_bulk",
        "link": "https://www.price.go.kr/mock/cp004", "category": "bulk_carb",
        "tags": ["오트밀", "귀리", "대량", "1kg"],
    },
    {
        "product_id": "CP005", "title": "식물성 완두 프로틴 파우더 1kg (약 33회분)",
        "price": 33000, "channel": "대형마트/할인점", "retail_type": "mart_bulk",
        "link": "https://www.price.go.kr/mock/cp005", "category": "bulk_protein",
        "tags": ["프로틴", "완두", "무유당", "대량", "단백질"],
    },
    {
        "product_id": "CP006", "title": "WPC 유청 단백질 프로틴 2kg",
        "price": 46000, "channel": "대형마트/할인점", "retail_type": "mart_bulk",
        "link": "https://www.price.go.kr/mock/cp006", "category": "bulk_protein",
        "tags": ["프로틴", "유청", "단백질", "대량"],
    },
    {
        "product_id": "CP007", "title": "구운계란 한판 30구",
        "price": 11900, "channel": "대형마트/할인점", "retail_type": "mart_bulk",
        "link": "https://www.price.go.kr/mock/cp007", "category": "bulk_protein",
        "tags": ["계란", "구운계란", "30구", "대량"],
    },
    {
        "product_id": "CP008", "title": "아몬드 브리즈 언스위트 190ml 24팩",
        "price": 16800, "channel": "대형마트/할인점", "retail_type": "mart_bulk",
        "link": "https://www.price.go.kr/mock/cp008", "category": "drink",
        "tags": ["아몬드", "무유당", "저당", "24팩", "음료"],
    },
    # 편의점 업태 기준가 (cvs_instant)
    {
        "product_id": "CP101", "title": "편의점 스팀 닭가슴살 100g 1팩",
        "price": 2900, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp101", "category": "main",
        "tags": ["닭가슴살", "스팀", "편의점"],
    },
    {
        "product_id": "CP102", "title": "편의점 훈제 닭가슴살 100g 1팩",
        "price": 3200, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp102", "category": "main",
        "tags": ["닭가슴살", "훈제", "편의점"],
    },
    {
        "product_id": "CP103", "title": "편의점 닭가슴살 현미 도시락 320g 1개",
        "price": 5500, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp103", "category": "main",
        "tags": ["도시락", "닭가슴살", "편의점"],
    },
    {
        "product_id": "CP104", "title": "편의점 리코타 닭가슴살 샐러드 230g 1팩",
        "price": 4900, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp104", "category": "main",
        "tags": ["샐러드", "닭가슴살", "편의점"],
    },
    {
        "product_id": "CP105", "title": "편의점 참치마요 삼각김밥 115g 1개",
        "price": 1500, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp105", "category": "main",
        "tags": ["삼각김밥", "김밥", "편의점"],
    },
    {
        "product_id": "CP106", "title": "편의점 고단백 두부면 샐러드 210g 1팩",
        "price": 4500, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp106", "category": "main",
        "tags": ["두부면", "두부", "샐러드", "편의점"],
    },
    {
        "product_id": "CP110", "title": "편의점 감동란 반숙 계란 2구 1팩",
        "price": 2400, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp110", "category": "sub",
        "tags": ["감동란", "계란", "삶은계란", "편의점"],
    },
    {
        "product_id": "CP111", "title": "편의점 무가당 그릭요거트 100g 1개",
        "price": 2600, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp111", "category": "sub",
        "tags": ["그릭요거트", "요거트", "편의점"],
    },
    {
        "product_id": "CP112", "title": "편의점 단백질 두부바 70g 1개",
        "price": 2200, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp112", "category": "sub",
        "tags": ["두부바", "두부", "편의점"],
    },
    {
        "product_id": "CP113", "title": "편의점 구운 아몬드 한봉 30g 1봉",
        "price": 1500, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp113", "category": "sub",
        "tags": ["아몬드", "견과", "편의점"],
    },
    {
        "product_id": "CP114", "title": "편의점 바나나 1개",
        "price": 1200, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp114", "category": "sub",
        "tags": ["바나나", "과일", "편의점"],
    },
    {
        "product_id": "CP120", "title": "편의점 프로틴 드링크 초코 250ml 1병",
        "price": 2900, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp120", "category": "drink",
        "tags": ["프로틴", "단백질음료", "편의점"],
    },
    {
        "product_id": "CP121", "title": "편의점 아몬드 브리즈 언스위트 190ml 1팩",
        "price": 1500, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp121", "category": "drink",
        "tags": ["아몬드", "식물성", "음료", "편의점"],
    },
    {
        "product_id": "CP122", "title": "편의점 무가당 매일 두유 190ml 1팩",
        "price": 1600, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp122", "category": "drink",
        "tags": ["두유", "음료", "편의점"],
    },
    {
        "product_id": "CP123", "title": "편의점 제로 콜라 355ml 1캔",
        "price": 1700, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp123", "category": "drink",
        "tags": ["제로", "콜라", "편의점"],
    },
    {
        "product_id": "CP124", "title": "편의점 씨그램 탄산수 플레인 350ml 1병",
        "price": 1500, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp124", "category": "drink",
        "tags": ["탄산수", "음료", "편의점"],
    },
    {
        "product_id": "CP125", "title": "편의점 아이스 아메리카노 350ml 1병",
        "price": 2000, "channel": "CU/GS25 편의점 기준가", "retail_type": "cvs",
        "link": "https://www.price.go.kr/mock/cp125", "category": "drink",
        "tags": ["아메리카노", "커피", "편의점"],
    },
    # 단위 파싱 실패 테스트용
    {
        "product_id": "CP999", "title": "파싱불가 특선 다이어트 패키지",
        "price": 29900, "channel": "테스트매장", "retail_type": "mart_bulk",
        "link": "https://www.price.go.kr/mock/cp999", "category": "main",
        "tags": ["파싱불가"],
    },
]


def strip_html(text: str) -> str:
    """HTML 태그를 제거한다."""
    return _HTML_TAG_RE.sub("", text).strip()


def _decode_xml_bytes(raw: bytes) -> ET.Element:
    """UTF-8 또는 EUC-KR/CP949 인코딩 XML 바이트를 안전하게 파싱한다."""
    for encoding in ("utf-8", "euc-kr", "cp949"):
        try:
            text = raw.decode(encoding)
            return ET.fromstring(text)
        except (UnicodeDecodeError, ET.ParseError):
            continue
    raise ValueError("소비자원 XML 응답 디코딩/파싱 실패")


def _recent_inspect_days(weeks: int = 4) -> list[str]:
    """한국소비자원 주간 조사일(최근 금요일부터 최대 N주 전까지 YYYYMMDD) 목록을 반환한다."""
    today = date.today()
    offset = (today.weekday() - 4) % 7
    latest_friday = today - timedelta(days=offset)
    return [(latest_friday - timedelta(days=7 * w)).strftime("%Y%m%d") for w in range(weeks)]


_GOODS_CATALOG_CACHE: dict[tuple[str, str], list[tuple[str, str]]] = {}
_STORE_CATALOG_CACHE: dict[tuple[str, str], dict[str, tuple[str, str]]] = {}


def clear_price_caches() -> None:
    """소비자원 상품/매장 마스터 캐시를 초기화한다."""
    _GOODS_CATALOG_CACHE.clear()
    _STORE_CATALOG_CACHE.clear()


_ENTP_TYPE_MAP = {"CS": "cvs", "LM": "mart_bulk", "SM": "mart_bulk"}


def _classify_retail_channel(channel_text: str) -> str:
    lower = channel_text.lower()
    if any(k in lower for k in _CVS_CHANNEL_KEYWORDS):
        return "cvs"
    if any(k in lower for k in _MART_CHANNEL_KEYWORDS):
        return "mart_bulk"
    return "other"


def parse_bundle_unit(title: str, default_serving_g: float = 100.0) -> dict[str, Any]:
    """상품명에서 묶음 수량(10팩, 30구, 10+10, 10팩+10팩)과 중량(100g, 1kg)을 파싱해 1회분 수량/단위를 계산한다."""
    clean_title = strip_html(title)

    pack_count: int | None = None
    plus_match = _PLUS_PACK_RE.search(clean_title)
    mul_match = _MUL_PACK_RE.search(clean_title)
    pack_matches = _PACK_RE.findall(clean_title)

    if plus_match:
        pack_count = int(plus_match.group(1)) + int(plus_match.group(2))
    elif mul_match and (pack_matches or not _WEIGHT_RE.search(mul_match.group(0))):
        pack_count = int(mul_match.group(1)) * int(mul_match.group(2))
    elif pack_matches:
        pack_count = int(pack_matches[-1][0])

    weight_g: float | None = None
    w_match = _WEIGHT_RE.search(clean_title)
    if w_match:
        num = float(w_match.group(1))
        unit = w_match.group(2).lower()
        weight_g = num * 1000.0 if unit in {"kg", "l"} else num

    if pack_count and pack_count >= 1:
        return {
            "parsed_ok": True,
            "pack_count": pack_count,
            "unit_weight_g": weight_g,
            "servings_per_purchase": float(pack_count),
            "unit_basis": f"{pack_count}개/팩 구성",
        }

    if weight_g and weight_g > 0:
        is_powder = any(k in clean_title for k in ("프로틴", "파우더", "단백질"))
        is_bulk_grain = any(k in clean_title for k in ("오트밀", "귀리", "현미")) or weight_g >= 600.0
        if is_powder:
            servings = max(1.0, round(weight_g / 30.0, 1))
            basis = f"총 {weight_g:.0f}g (30g/회 기준 약 {servings}회분)"
        elif is_bulk_grain:
            servings = max(1.0, round(weight_g / default_serving_g, 1))
            basis = f"총 {weight_g:.0f}g ({default_serving_g:.0f}g/회 기준 약 {servings}회분)"
        else:
            servings = 1.0
            basis = f"단품 {weight_g:.0f}g/ml (1회분)"
        return {
            "parsed_ok": True,
            "pack_count": 1,
            "unit_weight_g": weight_g,
            "servings_per_purchase": servings,
            "unit_basis": basis,
        }

    return {
        "parsed_ok": False,
        "pack_count": None,
        "unit_weight_g": None,
        "servings_per_purchase": None,
        "unit_basis": "수량/중량 단위 파싱 불가",
    }


def _enrich_product(raw: dict[str, Any], default_serving_g: float) -> tuple[dict[str, Any] | None, str | None]:
    title = strip_html(raw.get("title", ""))
    try:
        price = int(raw.get("price", 0))
    except (TypeError, ValueError):
        price = 0
    if price <= 0:
        return None, f"{title}: 가격 정보 누락"

    parsed = parse_bundle_unit(title, default_serving_g)
    if not parsed["parsed_ok"]:
        return None, f"{title}: {parsed['unit_basis']}"

    unit_price = round(price / parsed["servings_per_purchase"])
    channel = raw.get("channel", "")
    return {
        "product_id": str(raw.get("product_id") or title),
        "title": title,
        "bundle_price_krw": price,
        "unit_price_krw": unit_price,
        "pack_count": parsed["pack_count"],
        "unit_weight_g": parsed["unit_weight_g"],
        "servings_per_purchase": parsed["servings_per_purchase"],
        "unit_basis": parsed["unit_basis"],
        "mall_name": channel,
        "retail_type": raw.get("retail_type") or _classify_retail_channel(channel),
        "link": raw.get("link", ""),
    }, None


def _search_open_food_facts(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """2순위 보조: API 키 없이 Open Food Facts에서 가공식품 메타데이터/영양성분을 보완 조회한다."""
    try:
        resp = requests.get(
            OPEN_FOOD_FACTS_URL,
            params={"search_terms": query.strip(), "search_simple": 1, "action": "process", "json": 1, "page_size": limit},
            headers={"User-Agent": "DietPlannerAgent/1.0"},
            timeout=2.5,
        )
        resp.raise_for_status()
        raw_products = resp.json().get("products", [])
    except Exception:
        return []

    items: list[dict[str, Any]] = []
    for p in raw_products:
        name = (p.get("product_name") or p.get("product_name_ko") or "").strip()
        if not name:
            continue
        quantity = (p.get("quantity") or "").strip()
        nut = p.get("nutriments") or {}
        items.append(
            {
                "product_name": name,
                "brands": p.get("brands", ""),
                "quantity": quantity,
                "parsed_unit": parse_bundle_unit(f"{name} {quantity}"),
                "kcal_100g": nut.get("energy-kcal_100g"),
                "protein_100g": nut.get("proteins_100g"),
                "url": p.get("url", ""),
            }
        )
    return items


def _load_consumer_goods(base: str, api_key: str) -> list[tuple[str, str]]:
    ckey = (base, api_key)
    if ckey in _GOODS_CATALOG_CACHE:
        return _GOODS_CATALOG_CACHE[ckey]
    resp = requests.get(f"{base}/getProductInfoSvc.do", params={"serviceKey": api_key}, timeout=8)
    resp.raise_for_status()
    root = _decode_xml_bytes(resp.content)
    goods: list[tuple[str, str]] = []
    for item in root.iter():
        if item.tag.lower().endswith(("item", "productinfovo")):
            gid = (item.findtext("goodId") or "").strip()
            gname = (item.findtext("goodName") or "").strip()
            cnt = (item.findtext("goodTotalCnt") or "").strip()
            unit = (item.findtext("goodUnitDivCode") or item.findtext("goodTotalDivCode") or "").strip()
            has_unit = bool(_PACK_RE.search(gname) or _WEIGHT_RE.search(gname))
            full_name = gname if (has_unit or not cnt) else f"{gname} {cnt}{unit}".strip()
            if gid and gname:
                goods.append((gid, full_name))
    _GOODS_CATALOG_CACHE[ckey] = goods
    return goods


def _load_consumer_stores(base: str, api_key: str) -> dict[str, tuple[str, str]]:
    ckey = (base, api_key)
    if ckey in _STORE_CATALOG_CACHE:
        return _STORE_CATALOG_CACHE[ckey]
    try:
        resp = requests.get(f"{base}/getStoreInfoSvc.do", params={"serviceKey": api_key}, timeout=8)
        resp.raise_for_status()
        root = _decode_xml_bytes(resp.content)
        stores: dict[str, tuple[str, str]] = {}
        for vo in root.iter():
            if vo.tag.lower().endswith(("item", "entpinfovo")):
                eid = (vo.findtext("entpId") or "").strip()
                ename = (vo.findtext("entpName") or "").strip()
                etype = _ENTP_TYPE_MAP.get((vo.findtext("entpTypeCode") or "").strip().upper(), "other")
                if eid:
                    stores[eid] = (ename or f"매장-{eid}", etype)
        _STORE_CATALOG_CACHE[ckey] = stores
        return stores
    except Exception:
        return {}


def _fetch_consumer_price_live(query: str, mode: str, display: int, default_serving_g: float) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """1순위: 한국소비자원 생필품 가격정보 API(XML)로 상품 코드 매칭 후 업태별 가격을 조회한다."""
    s = get_settings()
    base = s.consumer_price_api_base_url
    tokens = [t for t in query.lower().replace(",", " ").split() if t]

    all_goods = _load_consumer_goods(base, s.data_go_kr_api_key)
    matched_goods = [(gid, gname) for gid, gname in all_goods if all(tok in gname.lower() for tok in tokens)]
    if not matched_goods:
        return [], []

    store_map = _load_consumer_stores(base, s.data_go_kr_api_key)
    inspect_days = _recent_inspect_days(4)
    products: list[dict[str, Any]] = []
    unparsed: list[dict[str, str]] = []

    for gid, gname in matched_goods[:5]:
        price_nodes: list[ET.Element] = []
        for day in inspect_days:
            price_resp = requests.get(
                f"{base}/getProductPriceInfoSvc",
                params={"serviceKey": s.data_go_kr_api_key, "goodId": gid, "goodInspectDay": day},
                timeout=8,
            )
            price_resp.raise_for_status()
            price_root = _decode_xml_bytes(price_resp.content)
            found = [p for p in price_root.iter() if p.tag.lower().endswith(("item", "goodpricevo")) and p.findtext("goodPrice")]
            if found:
                price_nodes = found
                break

        for p_item in price_nodes:
            raw_price = (p_item.findtext("goodPrice") or "").strip()
            entp_id = (p_item.findtext("entpId") or "").strip()
            entp_name, mapped_type = store_map.get(entp_id, ("", "other"))
            entp_name = (p_item.findtext("entpName") or p_item.findtext("entpTypeName") or entp_name or entp_id or "한국소비자원 조사매장").strip()
            plusone = (p_item.findtext("plusoneYn") or "").strip().upper()
            channel_label = f"{entp_name}{' (1+1)' if plusone == 'Y' else ''}"
            retail_type = mapped_type if mapped_type != "other" else _classify_retail_channel(channel_label)
            raw = {
                "product_id": f"{gid}-{entp_id or entp_name}",
                "title": gname,
                "price": raw_price,
                "channel": channel_label,
                "retail_type": retail_type,
                "link": f"https://www.price.go.kr/tprice/portal/goodinfo/goodInfoView.do?goodId={gid}",
            }
            enriched, reason = _enrich_product(raw, default_serving_g)
            if enriched:
                products.append(enriched)
            elif reason:
                unparsed.append({"name": gname, "reason": reason})

    # 모드별 업태 우선 정렬 (cvs_instant -> 편의점 우선, online_bulk -> 대형마트/할인점 최저가 우선)
    preferred_type = "cvs" if mode == "cvs_instant" else "mart_bulk" if mode == "online_bulk" else None
    products.sort(key=lambda p: (0 if p["retail_type"] == preferred_type else 1, p["unit_price_krw"]))
    return products[:display], unparsed


def _search_mock(query: str, mode: str, display: int, default_serving_g: float) -> dict[str, Any]:
    tokens = [t for t in query.lower().replace(",", " ").split() if t]
    products: list[dict[str, Any]] = []
    unparsed: list[dict[str, str]] = []

    for row in _MOCK_PRICE_DB:
        haystack = " ".join([row["title"], row["category"], *row["tags"]]).lower()
        if not all(tok in haystack for tok in tokens):
            continue
        enriched, reason = _enrich_product(row, default_serving_g)
        if enriched:
            products.append(enriched)
        elif reason:
            unparsed.append({"name": row["title"], "reason": reason})
        if len(products) >= display:
            break

    preferred_type = "cvs" if mode == "cvs_instant" else "mart_bulk" if mode == "online_bulk" else None
    products.sort(key=lambda p: (0 if p["retail_type"] == preferred_type else 1, p["unit_price_krw"]))

    if not products and unparsed:
        return err(ErrorCode.UNIT_PARSE_FAILED, f"'{query}' 상품의 수량/중량 단위를 파싱할 수 없습니다.")
    if not products:
        return err(ErrorCode.NO_SHOPPING_RESULT, f"'{query}'에 해당하는 가격 검색 결과가 없습니다.")

    basis = "한국소비자원/Open Food Facts 키 미설정 또는 USE_MOCK=true로 로컬 기준가 사용(추정 포함)"
    if mode == "cvs_instant":
        basis = f"{basis} — {CVS_DISCLAIMER}"

    return ok(
        {
            "requested_query": query,
            "products": products,
            "excluded_unparsed": unparsed,
            "source_mode": "mock_fallback",
            "is_estimated": True,
            "basis_note": basis,
        }
    )


@safe_tool
def search_product_price(
    query: str,
    mode: str = "cvs_instant",
    display: int = 10,
    default_serving_g: float = 100.0,
) -> dict[str, Any]:
    """한국소비자원 생필품 가격정보(XML) 및 Open Food Facts 보조로 가격을 조회하고 1회분 단가로 환산한다."""
    if not query or not query.strip():
        return err(ErrorCode.INVALID_INPUT, "검색어(query)가 비어 있습니다.")

    settings = get_settings()
    if not settings.data_go_kr_api_key:
        if settings.use_mock:
            return _search_mock(query, mode, display, default_serving_g)
        return err(ErrorCode.MISSING_API_KEY, "DATA_GO_KR_API_KEY가 설정되지 않았습니다.")

    try:
        products, unparsed = _fetch_consumer_price_live(query, mode, display, default_serving_g)
    except requests.Timeout:
        return _search_mock(query, mode, display, default_serving_g) if settings.use_mock else err(ErrorCode.API_TIMEOUT, "한국소비자원 가격 API 응답 시간 초과")
    except Exception:
        return _search_mock(query, mode, display, default_serving_g) if settings.use_mock else err(ErrorCode.API_ERROR, "한국소비자원 가격 API 호출 실패")

    if products:
        basis = "한국소비자원 생필품 가격정보 API 실시간 조회 및 단가 환산 결과"
        if mode == "cvs_instant":
            basis = f"{basis} ({CVS_DISCLAIMER})"
        return ok(
            {
                "requested_query": query,
                "products": products,
                "excluded_unparsed": unparsed,
                "source_mode": "consumer_price_live",
                "is_estimated": False,
                "basis_note": basis,
            }
        )

    # 1순위 소비자원 DB에 없는 가공식품은 2순위 Open Food Facts로 메타데이터 보완
    off_items = _search_open_food_facts(query, limit=min(5, display))
    if settings.use_mock:
        mock_res = _search_mock(query, mode, display, default_serving_g)
        if mock_res["ok"] and off_items:
            mock_res["data"]["openfoodfacts_items"] = off_items
        return mock_res

    if unparsed:
        return err(ErrorCode.UNIT_PARSE_FAILED, f"'{query}'에서 단가 환산 가능한 상품을 찾지 못했습니다.")
    if off_items:
        return err(
            ErrorCode.NO_SHOPPING_RESULT,
            f"'{query}' 소비자원 가격 데이터 없음 (Open Food Facts 제품 {len(off_items)}건 확인, 국내 소매가 미제공으로 임의 가격 생성 제외)",
        )
    return err(ErrorCode.NO_SHOPPING_RESULT, f"'{query}'에 해당하는 한국소비자원/Open Food Facts 상품 결과가 없습니다.")
