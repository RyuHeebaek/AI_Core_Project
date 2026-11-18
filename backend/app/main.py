"""FastAPI 백엔드 엔트리포인트 (F3017, NF3006)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.logging_agent import logging_agent
from app.api.chat import router as chat_router
from app.api.logs import router as logs_router
from app.api.plans import router as plans_router
from app.config import get_api_key_status
from app.database import init_db
from app.events.event_bus import event_bus
from app.tools import list_tool_specs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    logging_agent.attach(event_bus)
    yield
    logging_agent.detach(event_bus)


app = FastAPI(
    title="하이브리드 식단 플래너 Agent API",
    description="영양성분 선형 조합 + 가성비 역산 기반 멀티 Agent 식단 플래너 (SSAFY AI 코어 3회차)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(plans_router)
app.include_router(logs_router)


@app.get("/api/health", tags=["system"])
def health_check() -> dict:
    """서버 상태, API 키 설정 여부(값 미노출), 등록 Tool 명세를 반환한다."""
    return {
        "status": "ok",
        "api_keys": get_api_key_status(),
        "tools": list_tool_specs(),
    }
