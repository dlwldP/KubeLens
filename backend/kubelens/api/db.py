"""SQLite 데이터 모델 (기획서 9장)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, event
from sqlmodel import Field, SQLModel, create_engine

_DEFAULT_DB = Path(__file__).resolve().parents[2] / "kubelens.db"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Cluster(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    kubeconfig_path: str  # 경로만 저장한다. kubeconfig 내용은 저장하지도, LLM에 보내지도 않는다.
    context: str | None = None
    server_version: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class DiagnosisSession(SQLModel, table=True):
    __tablename__ = "session"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: int = Field(foreign_key="cluster.id", index=True)
    title: str
    created_at: datetime = Field(default_factory=utcnow)


class Message(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    role: str  # user | assistant
    content: str
    # {"diagnosis": {...}, "valid": bool, "validation_errors": [...], "usage": {...}}
    diagnosis_json: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ToolCall(SQLModel, table=True):
    id: str = Field(primary_key=True)  # 진단 결과의 evidence_refs가 이 값을 가리킨다
    message_id: int = Field(foreign_key="message.id", index=True)
    tool_name: str
    args_json: str
    output_text: str  # 마스킹·절단 후
    truncated: bool
    duration_ms: int
    status: str
    created_at: datetime = Field(default_factory=utcnow)


def make_engine(url: str | None = None) -> Engine:
    url = url or os.environ.get("KUBELENS_DB_URL") or f"sqlite:///{_DEFAULT_DB}"
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url, connect_args={"check_same_thread": False, "timeout": 30} if is_sqlite else {}
    )
    if is_sqlite:
        # 여러 진단이 동시에 도구 호출을 기록해도 "database is locked"가 나지 않게 한다
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: object, _record: object) -> None:
            cur = dbapi_conn.cursor()  # type: ignore[attr-defined]
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    SQLModel.metadata.create_all(engine)
    return engine
