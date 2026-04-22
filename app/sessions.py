"""
Session store for conversational memory.

Sessions and turns live in Postgres (tables `sessions` and `session_turns`).
Each chat request can reuse an existing session_id or create a new one on first use.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings


@dataclass
class Turn:
    role: str          # 'user' | 'assistant'
    content: str


def create_session(sess: Session) -> UUID:
    row = sess.execute(text("INSERT INTO sessions DEFAULT VALUES RETURNING id")).one()
    return row[0]


def touch_session(sess: Session, session_id: UUID) -> bool:
    result = sess.execute(
        text("UPDATE sessions SET last_active = NOW() WHERE id = :sid RETURNING id"),
        {"sid": session_id},
    ).first()
    return result is not None


def get_or_create_session(sess: Session, session_id: UUID | None) -> UUID:
    if session_id is not None and touch_session(sess, session_id):
        return session_id
    return create_session(sess)


def load_history(sess: Session, session_id: UUID, limit: int | None = None) -> list[Turn]:
    limit = limit or get_settings().session_history_turns
    rows = sess.execute(
        text("""
            SELECT role, content FROM session_turns
            WHERE session_id = :sid
            ORDER BY created_at DESC
            LIMIT :lim
        """),
        {"sid": session_id, "lim": limit},
    ).all()
    return [Turn(role=r[0], content=r[1]) for r in reversed(rows)]


def append_turn(sess: Session, session_id: UUID, role: str, content: str) -> None:
    if role not in ("user", "assistant"):
        raise ValueError(f"invalid role: {role}")
    sess.execute(
        text("INSERT INTO session_turns (session_id, role, content) VALUES (:sid, :role, :content)"),
        {"sid": session_id, "role": role, "content": content},
    )
