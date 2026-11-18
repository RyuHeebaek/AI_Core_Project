"""식단 계획 CRUD 및 동일 날짜 시간 중복 경고 API (F3015, F3016)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.database import get_conn, utc_now_iso
from app.models.schemas import PlanCreate, PlanOut, PlanSaveResponse, PlanUpdate

router = APIRouter(prefix="/api/plans", tags=["plans"])


def _row_to_plan(row: Any) -> PlanOut:
    return PlanOut(
        id=row["id"],
        session_id=row["session_id"],
        title=row["title"],
        mode=row["mode"],
        plan_date=row["plan_date"],
        start_time=row["start_time"],
        end_time=row["end_time"],
        items=json.loads(row["items_json"]),
        nutrition_summary=json.loads(row["nutrition_summary_json"]),
        total_price_krw=row["total_price_krw"],
        shopping_summary=json.loads(row["shopping_summary_json"]),
        notes=row["notes"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _times_overlap(s1: str | None, e1: str | None, s2: str | None, e2: str | None) -> bool:
    """같은 날짜에서 두 계획의 시간대가 겹치는지 판정한다 (F3015)."""
    if not s1 or not s2:
        return False
    end1 = e1 or s1
    end2 = e2 or s2
    if s1 == s2:
        return True
    return s1 < end2 and s2 < end1


def _find_overlaps(
    conn: Any,
    plan_date: str,
    start_time: str | None,
    end_time: str | None,
    exclude_id: int | None = None,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, title, plan_date, start_time, end_time FROM plans WHERE plan_date = ?",
        (plan_date,),
    ).fetchall()

    warnings: list[dict[str, Any]] = []
    for r in rows:
        if exclude_id is not None and r["id"] == exclude_id:
            continue
        if _times_overlap(start_time, end_time, r["start_time"], r["end_time"]):
            warnings.append(
                {
                    "plan_id": r["id"],
                    "title": r["title"],
                    "plan_date": r["plan_date"],
                    "start_time": r["start_time"],
                    "end_time": r["end_time"],
                    "message": f"같은 날짜({plan_date})에 시간이 겹치는 기존 계획('{r['title']}', {r['start_time']}~{r['end_time'] or r['start_time']})이 있습니다.",
                }
            )
    return warnings


def _validate_time_range(start_time: str | None, end_time: str | None) -> None:
    if start_time and end_time and end_time < start_time:
        raise HTTPException(status_code=422, detail="종료 시각은 시작 시각보다 빠를 수 없습니다.")


@router.post("", response_model=PlanSaveResponse, status_code=201)
def create_plan(payload: PlanCreate) -> PlanSaveResponse:
    """식단 계획을 저장한다. 같은 날짜에 시간이 겹쳐도 저장을 막지 않고 경고만 반환한다 (F3015)."""
    _validate_time_range(payload.start_time, payload.end_time)
    now = utc_now_iso()
    with get_conn(immediate=True) as conn:
        overlaps = _find_overlaps(conn, payload.plan_date, payload.start_time, payload.end_time)
        cur = conn.execute(
            """
            INSERT INTO plans
                (session_id, title, mode, plan_date, start_time, end_time,
                 items_json, nutrition_summary_json, total_price_krw, shopping_summary_json,
                 notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.session_id,
                payload.title,
                payload.mode,
                payload.plan_date,
                payload.start_time,
                payload.end_time,
                json.dumps(payload.items, ensure_ascii=False),
                json.dumps(payload.nutrition_summary, ensure_ascii=False),
                payload.total_price_krw,
                json.dumps(payload.shopping_summary, ensure_ascii=False),
                payload.notes,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (cur.lastrowid,)).fetchone()
    return PlanSaveResponse(plan=_row_to_plan(row), overlap_warnings=overlaps)


@router.get("", response_model=list[PlanOut])
def list_plans(session_id: str | None = Query(default=None)) -> list[PlanOut]:
    """저장된 식단 계획 목록을 조회한다."""
    with get_conn() as conn:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM plans WHERE session_id = ? ORDER BY plan_date DESC, id DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM plans ORDER BY plan_date DESC, id DESC").fetchall()
    return [_row_to_plan(r) for r in rows]


@router.get("/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int) -> PlanOut:
    """특정 식단 계획 상세를 조회한다."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="계획을 찾을 수 없습니다.")
    return _row_to_plan(row)


@router.put("/{plan_id}", response_model=PlanSaveResponse)
@router.patch("/{plan_id}", response_model=PlanSaveResponse)
def update_plan(plan_id: int, payload: PlanUpdate) -> PlanSaveResponse:
    """저장된 식단 계획의 핵심 값을 수정한다 (F3016)."""
    with get_conn(immediate=True) as conn:
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="계획을 찾을 수 없습니다.")

        updates = payload.model_dump(exclude_unset=True)
        new_date = updates.get("plan_date", row["plan_date"])
        new_start = updates.get("start_time", row["start_time"])
        new_end = updates.get("end_time", row["end_time"])
        _validate_time_range(new_start, new_end)

        conn.execute(
            """
            UPDATE plans SET
                title = ?, plan_date = ?, start_time = ?, end_time = ?,
                items_json = ?, nutrition_summary_json = ?, total_price_krw = ?,
                shopping_summary_json = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                updates.get("title", row["title"]),
                new_date,
                new_start,
                new_end,
                json.dumps(updates["items"], ensure_ascii=False) if "items" in updates else row["items_json"],
                json.dumps(updates["nutrition_summary"], ensure_ascii=False)
                if "nutrition_summary" in updates
                else row["nutrition_summary_json"],
                updates.get("total_price_krw", row["total_price_krw"]),
                json.dumps(updates["shopping_summary"], ensure_ascii=False)
                if "shopping_summary" in updates
                else row["shopping_summary_json"],
                updates.get("notes", row["notes"]),
                utc_now_iso(),
                plan_id,
            ),
        )
        updated_row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        overlaps = _find_overlaps(conn, new_date, new_start, new_end, exclude_id=plan_id)

    return PlanSaveResponse(plan=_row_to_plan(updated_row), overlap_warnings=overlaps)


@router.delete("/{plan_id}")
def delete_plan(plan_id: int) -> dict[str, Any]:
    """저장된 식단 계획을 삭제한다 (F3016)."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="계획을 찾을 수 없습니다.")
    return {"deleted": True, "plan_id": plan_id}
