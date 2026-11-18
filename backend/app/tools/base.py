"""공통 Tool 응답 포맷, 에러 코드, 레지스트리 및 안전 래퍼 (F3001, NF3002, NF3003)."""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, Callable

import requests
from pydantic import BaseModel

from app.config import redact_secrets

_ORIG_REQUESTS_GET = requests.get
_HTTP_SESSION = requests.Session()


def http_get(url: str, **kwargs: Any) -> requests.Response:
    """연결 재사용(Session)을 쓰되, 테스트에서 requests.get이 패치되면 그대로 따른다."""
    if requests.get is not _ORIG_REQUESTS_GET:
        return requests.get(url, **kwargs)
    return _HTTP_SESSION.get(url, **kwargs)


class ErrorCode:
    """표준 Tool 실패 코드."""

    MISSING_API_KEY = "MISSING_API_KEY"
    INVALID_INPUT = "INVALID_INPUT"
    NO_NUTRITION_DATA = "NO_NUTRITION_DATA"
    MISSING_NUTRIENT_FIELDS = "MISSING_NUTRIENT_FIELDS"
    NO_SHOPPING_RESULT = "NO_SHOPPING_RESULT"
    UNIT_PARSE_FAILED = "UNIT_PARSE_FAILED"
    NO_STORE_FOUND = "NO_STORE_FOUND"
    AMBIGUOUS_LOCATION = "AMBIGUOUS_LOCATION"
    NO_FEASIBLE_COMBO = "NO_FEASIBLE_COMBO"
    API_TIMEOUT = "API_TIMEOUT"
    API_ERROR = "API_ERROR"


# Worker가 인자 보정 재시도를 고려할 수 있는 실패 코드
RETRYABLE_ERROR_CODES = frozenset(
    {
        ErrorCode.NO_NUTRITION_DATA,
        ErrorCode.NO_SHOPPING_RESULT,
        ErrorCode.UNIT_PARSE_FAILED,
        ErrorCode.NO_STORE_FOUND,
    }
)

# LLM에게 넘기지 않고 즉시 멈추거나 사용자에게 되묻는 치명/모호 실패 코드 (F3021)
FATAL_OR_CLARIFY_CODES = frozenset(
    {
        ErrorCode.MISSING_API_KEY,
        ErrorCode.INVALID_INPUT,
        ErrorCode.AMBIGUOUS_LOCATION,
    }
)


def ok(data: Any) -> dict[str, Any]:
    """성공 Tool 응답을 생성한다."""
    return {"ok": True, "data": data, "error": None}


def err(code: str, message: str) -> dict[str, Any]:
    """실패 Tool 응답을 생성한다 (API 키 마스킹 적용)."""
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": redact_secrets(message)},
    }


def safe_tool(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Tool이 절대 예외를 외부로 던지지 않도록 공통 포맷으로 감싼다 (F3001)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return err(ErrorCode.API_ERROR, f"Tool 실행 중 오류: {redact_secrets(str(exc))}")

    return wrapper


@dataclass(frozen=True)
class ToolSpec:
    """Agent가 인식하고 선택할 수 있는 Tool 명세 (F3001)."""

    name: str
    description: str  # 언제 사용하는지 명시
    kind: str  # "external_api" | "local_server"
    args_schema: type[BaseModel]
    output_summary: str
    fn: Callable[..., dict[str, Any]]

    def to_metadata(self) -> dict[str, Any]:
        """Agent 및 API 문서용 직렬화 메타데이터를 반환한다."""
        return {
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "args_schema": self.args_schema.model_json_schema(),
            "output_summary": self.output_summary,
        }
