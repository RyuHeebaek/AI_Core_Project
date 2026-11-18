"""환경변수 및 애플리케이션 설정 관리 (NF3001, NF3002)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """런타임 환경 설정 스냅샷."""

    use_mock: bool
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    data_go_kr_api_key: str
    nutrition_api_url: str
    consumer_price_api_base_url: str
    kakao_rest_api_key: str
    kakao_local_api_url: str
    max_replan: int
    max_worker_retry: int
    db_path: str


def get_settings() -> Settings:
    """현재 환경변수 기준으로 설정을 읽어 반환한다."""
    raw_db = os.getenv("DB_PATH", str(PROJECT_ROOT / "backend" / "diet_planner.db"))
    db_path = str((PROJECT_ROOT / raw_db).resolve()) if not Path(raw_db).is_absolute() else raw_db
    return Settings(
        use_mock=_env_bool("USE_MOCK", True),
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://gms.ssafy.io/gmsapi/api.openai.com/v1").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip(),
        data_go_kr_api_key=os.getenv("DATA_GO_KR_API_KEY", "").strip(),
        nutrition_api_url=os.getenv(
            "NUTRITION_API_URL",
            "https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo03/getFoodNtrCpntDbInq03",
        ).strip(),
        consumer_price_api_base_url=os.getenv(
            "CONSUMER_PRICE_API_BASE_URL",
            "https://apis.data.go.kr/B551919/ProductPriceInfoService",
        ).strip().rstrip("/"),
        kakao_rest_api_key=os.getenv("KAKAO_REST_API_KEY", "").strip(),
        kakao_local_api_url=os.getenv(
            "KAKAO_LOCAL_API_URL",
            "https://dapi.kakao.com/v2/local/search/keyword.json",
        ).strip(),
        max_replan=max(1, int(os.getenv("MAX_REPLAN", "3"))),
        max_worker_retry=max(1, int(os.getenv("MAX_WORKER_RETRY", "2"))),
        db_path=db_path,
    )


def get_api_key_status() -> dict[str, bool]:
    """API 키 실제 값은 감추고 설정 여부만 반환한다 (NF3002)."""
    s = get_settings()
    return {
        "openai": bool(s.openai_api_key),
        "food_api": bool(s.data_go_kr_api_key),
        "consumer_price": bool(s.data_go_kr_api_key),
        "kakao": bool(s.kakao_rest_api_key),
        "use_mock": s.use_mock,
    }


def redact_secrets(text: str) -> str:
    """문자열에 API 키가 섞였을 경우 마스킹한다 (NF3002)."""
    if not text:
        return text
    s = get_settings()
    masked = text
    for secret in (
        s.openai_api_key,
        s.data_go_kr_api_key,
        s.kakao_rest_api_key,
    ):
        if secret and len(secret) >= 4:
            masked = masked.replace(secret, "[REDACTED]")
    return masked
