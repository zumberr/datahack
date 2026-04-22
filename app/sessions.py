"""
Session store for conversational memory and per-turn metadata.

Sessions and turns live in Postgres (tables `sessions`, `session_turns`).
Assistant turns additionally carry retrieval/gate metadata in `turn_metadata`,
which the feedback loop uses to attribute user complaints to specific
retrieval or generation decisions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
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


def append_turn(sess: Session, session_id: UUID, role: str, content: str) -> int:
    """Insert a turn and return its id. Raises ValueError on invalid role."""
    if role not in ("user", "assistant"):
        raise ValueError(f"invalid role: {role}")
    row = sess.execute(
        text("""
            INSERT INTO session_turns (session_id, role, content)
            VALUES (:sid, :role, :content)
            RETURNING id
        """),
        {"sid": session_id, "role": role, "content": content},
    ).one()
    return int(row[0])


def save_turn_metadata(
    sess: Session,
    turn_id: int,
    *,
    search_query: str,
    retrieved_ids: list[int],
    retrieved_urls: list[str],
    confident: bool,
    confidence_score: float,
    signals: dict[str, Any],
) -> None:
    """Persist the retrieval/gate decision for an assistant turn."""
    sess.execute(
        text("""
            INSERT INTO turn_metadata
                (turn_id, search_query, retrieved_ids, retrieved_urls,
                 confident, confidence_score, signals)
            VALUES
                (:turn_id, :search_query, :retrieved_ids, :retrieved_urls,
                 :confident, :confidence_score, CAST(:signals AS jsonb))
            ON CONFLICT (turn_id) DO UPDATE SET
                search_query = EXCLUDED.search_query,
                retrieved_ids = EXCLUDED.retrieved_ids,
                retrieved_urls = EXCLUDED.retrieved_urls,
                confident = EXCLUDED.confident,
                confidence_score = EXCLUDED.confidence_score,
                signals = EXCLUDED.signals
        """),
        {
            "turn_id": turn_id,
            "search_query": search_query,
            "retrieved_ids": retrieved_ids,
            "retrieved_urls": retrieved_urls,
            "confident": confident,
            "confidence_score": confidence_score,
            "signals": json.dumps(signals),
        },
    )
