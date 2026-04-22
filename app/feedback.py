"""
Feedback domain: record and query user judgments on assistant turns.

The feedback loop closes three gaps:
  1. Retrieval — low-similarity retrievals that users still flag as wrong/unhelpful
     reveal bad gate calibration or missing index coverage.
  2. Prompts — confident answers flagged as `wrong` are hallucination candidates
     and feed back into SYSTEM_PROMPT tuning.
  3. Corpus — `missing_info` ratings point to pages the scraper hasn't ingested yet.

See `scripts/analyze_feedback.py` for the offline triage.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class FeedbackError(ValueError):
    """Raised when feedback references an unknown or mismatched turn."""


def record_feedback(
    sess: Session,
    *,
    session_id: UUID,
    turn_id: int,
    rating: str,
    reason: str | None = None,
) -> int:
    """Insert feedback for an assistant turn. Returns the feedback row id.

    Raises FeedbackError if the turn does not exist, does not belong to the
    session, or is not an assistant turn (we don't accept feedback on user
    messages).
    """
    row = sess.execute(
        text("""
            SELECT id FROM session_turns
            WHERE id = :tid AND session_id = :sid AND role = 'assistant'
        """),
        {"tid": turn_id, "sid": session_id},
    ).first()
    if row is None:
        raise FeedbackError(
            f"turn {turn_id} not found in session {session_id} or is not an assistant turn"
        )

    inserted = sess.execute(
        text("""
            INSERT INTO feedback (session_id, turn_id, rating, reason)
            VALUES (:sid, :tid, :rating, :reason)
            RETURNING id
        """),
        {"sid": session_id, "tid": turn_id, "rating": rating, "reason": reason},
    ).one()
    return int(inserted[0])
