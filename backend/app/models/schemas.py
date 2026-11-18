"""FastAPI 요청/응답 Pydantic 스키마 (F3014~F3017)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ProfilePayload(BaseModel):
    """사용자 신체 및 목표 프로필 입력."""

    gender: Literal["male", "female"]
    weight_kg: float = Field(..., gt=20, lt=300)
    target_weight_kg: float | None = Field(default=None, gt=20, lt=300)
    target_weeks: int | None = Field(default=None, ge=1, le=104)
    goal_type: Literal["diet", "maintain", "bulk"] = "diet"
    meals_per_day: int = Field(default=3, ge=1, le=6)
    budget_krw: int | None = Field(default=None, ge=0)
    height_cm: float | None = Field(default=None, gt=100, lt=250)
    age: int | None = Field(default=None, ge=10, lt=110)
    activity_factor: float = Field(default=1.375, ge=1.1, le=2.2)
    lactose_free: bool = False
    low_sugar: bool = False
    no_caffeine_after_18: bool = False
    disliked_ingredients: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """POST /api/chat 요청."""

    session_id: str = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=2000)
    profile: ProfilePayload | None = None
    worker_llm_enabled: bool = True


class ChatResponse(BaseModel):
    """POST /api/chat 응답."""

    session_id: str
    run_id: str
    mode: str
    reply: str
    clarification_needed: bool = False
    candidate_combo: dict[str, Any] = Field(default_factory=dict)
    validation_result: dict[str, Any] = Field(default_factory=dict)
    anomaly_report: dict[str, Any] = Field(default_factory=dict)
    replan_count: int = 0
    llm_calls_count: int = 0
    execution_trace: list[dict[str, Any]] = Field(default_factory=list)
    api_status: dict[str, bool] = Field(default_factory=dict)
    error_notice: str | None = None


class PlanCreate(BaseModel):
    """POST /api/plans 요청 (F3015)."""

    session_id: str | None = None
    title: str = Field(..., min_length=1, max_length=200)
    mode: str = "cvs_instant"
    plan_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    end_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    items: list[dict[str, Any]] = Field(default_factory=list)
    nutrition_summary: dict[str, Any] = Field(default_factory=dict)
    total_price_krw: int = Field(default=0, ge=0)
    shopping_summary: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class PlanUpdate(BaseModel):
    """PATCH /api/plans/{plan_id} 요청 (F3016)."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    plan_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    start_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    end_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    items: list[dict[str, Any]] | None = None
    nutrition_summary: dict[str, Any] | None = None
    total_price_krw: int | None = Field(default=None, ge=0)
    shopping_summary: dict[str, Any] | None = None
    notes: str | None = None


class PlanOut(BaseModel):
    """저장된 식단 계획 응답."""

    id: int
    session_id: str | None
    title: str
    mode: str
    plan_date: str
    start_time: str | None
    end_time: str | None
    items: list[dict[str, Any]]
    nutrition_summary: dict[str, Any]
    total_price_krw: int
    shopping_summary: dict[str, Any]
    notes: str
    created_at: str
    updated_at: str


class PlanSaveResponse(BaseModel):
    """계획 저장/수정 결과 및 동일 날짜 시간 중복 경고 (F3015)."""

    plan: PlanOut
    overlap_warnings: list[dict[str, Any]] = Field(default_factory=list)


class LogSessionSummary(BaseModel):
    """세션별 실행 로그 목록 요약 (F3014)."""

    session_id: str
    event_count: int
    replan_count: int
    last_event_at: str
    latest_anomaly_status: str | None = None


class LogDetailResponse(BaseModel):
    """특정 세션의 실행 로그 상세 및 최신 이상 탐지 결과."""

    session_id: str
    events: list[dict[str, Any]]
    latest_anomaly: dict[str, Any] | None = None
