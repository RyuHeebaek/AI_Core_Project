"""SQLite 데이터베이스 초기화 및 접근 헬퍼 (F3013~F3016)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import get_settings


def utc_now_iso() -> str:
    """UTC ISO-8601 타임스탬프를 반환한다."""
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn(db_path: str | None = None, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """SQLite 커넥션을 열고 WAL 모드·즉시 락·커밋/롤백/닫기를 보장한다."""
    target = db_path or get_settings().db_path
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=10.0, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE;")
        else:
            conn.execute("BEGIN;")
        yield conn
        if conn.in_transaction:
            conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """필수 테이블이 없으면 생성한다."""
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                session_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS session_contexts (
                session_id TEXT PRIMARY KEY,
                context_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                title TEXT NOT NULL,
                mode TEXT NOT NULL,
                plan_date TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                items_json TEXT NOT NULL,
                nutrition_summary_json TEXT NOT NULL,
                total_price_krw INTEGER NOT NULL DEFAULT 0,
                shopping_summary_json TEXT NOT NULL,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS execution_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                node_name TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_logs_session
            ON execution_logs (session_id, id);

            CREATE TABLE IF NOT EXISTS anomaly_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                run_id TEXT,
                status TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                diagnosis TEXT,
                llm_called INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )


def save_profile(session_id: str, profile: dict[str, Any], db_path: str | None = None) -> None:
    """세션별 사용자 프로필을 저장/갱신한다."""
    now = utc_now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO profiles (session_id, profile_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                profile_json = excluded.profile_json,
                updated_at = excluded.updated_at
            """,
            (session_id, json.dumps(profile, ensure_ascii=False), now),
        )


def load_profile(session_id: str, db_path: str | None = None) -> dict[str, Any] | None:
    """세션별 사용자 프로필을 조회한다."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT profile_json FROM profiles WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return json.loads(row["profile_json"]) if row else None


def save_session_context(session_id: str, context: dict[str, Any], db_path: str | None = None) -> None:
    """대화 맥락 요약(Memory)을 저장한다 (Tool 원본 응답 제외 F3010)."""
    now = utc_now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO session_contexts (session_id, context_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                context_json = excluded.context_json,
                updated_at = excluded.updated_at
            """,
            (session_id, json.dumps(context, ensure_ascii=False), now),
        )


def load_session_context(session_id: str, db_path: str | None = None) -> dict[str, Any]:
    """세션의 직전 대화 맥락 요약을 조회한다."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT context_json FROM session_contexts WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return json.loads(row["context_json"]) if row else {}
