"""OpenAI / SSAFY GMS 호환 LLM 클라이언트 팩토리 (NF3001, NF3002)."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import get_settings, redact_secrets


def get_llm(temperature: float = 0.0) -> ChatOpenAI | None:
    """OPENAI_API_KEY가 설정되어 있으면 ChatOpenAI 인스턴스를 반환한다."""
    s = get_settings()
    if not s.openai_api_key:
        return None
    return ChatOpenAI(
        api_key=s.openai_api_key,
        base_url=s.openai_base_url or None,
        model=s.openai_model,
        temperature=temperature,
        timeout=20,
        max_retries=1,
    )


def safe_llm_invoke(user_prompt: str, system_prompt: str = "") -> tuple[str | None, bool, str | None]:
    """LLM을 안전하게 호출한다. 반환: (응답 텍스트, 실제 LLM 호출 여부, 오류 메시지)."""
    llm = get_llm()
    if llm is None:
        return None, False, "OPENAI_API_KEY 미설정"
    try:
        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=user_prompt))
        resp = llm.invoke(messages)
        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        return content.strip(), True, None
    except Exception as exc:
        return None, True, redact_secrets(str(exc))


def safe_llm_json(user_prompt: str, system_prompt: str = "") -> tuple[dict[str, Any] | None, bool, str | None]:
    """LLM 응답에서 JSON 객체를 안전하게 추출한다."""
    raw, called, err_msg = safe_llm_invoke(user_prompt, system_prompt)
    if not raw or not called:
        return None, called, err_msg
    try:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, dict):
                return parsed, True, None
    except Exception:
        pass
    return None, called, err_msg
