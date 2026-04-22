"""Unit tests for the feedback models — no DB required."""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import FeedbackRequest


def test_feedback_request_accepts_canonical_rating():
    req = FeedbackRequest(
        session_id=uuid4(),
        turn_id=42,
        rating="not_helpful",
        reason="No respondió sobre costos nocturnos",
    )
    assert req.rating == "not_helpful"
    assert req.turn_id == 42


@pytest.mark.parametrize("rating", ["helpful", "not_helpful", "wrong", "incomplete", "missing_info"])
def test_feedback_request_accepts_all_valid_ratings(rating):
    req = FeedbackRequest(session_id=uuid4(), turn_id=1, rating=rating)
    assert req.rating == rating


def test_feedback_request_rejects_unknown_rating():
    with pytest.raises(ValidationError):
        FeedbackRequest(session_id=uuid4(), turn_id=1, rating="maybe")


def test_feedback_request_rejects_non_positive_turn_id():
    with pytest.raises(ValidationError):
        FeedbackRequest(session_id=uuid4(), turn_id=0, rating="helpful")


def test_feedback_request_reason_is_optional():
    req = FeedbackRequest(session_id=uuid4(), turn_id=5, rating="helpful")
    assert req.reason is None


def test_feedback_request_caps_long_reason():
    with pytest.raises(ValidationError):
        FeedbackRequest(
            session_id=uuid4(),
            turn_id=5,
            rating="wrong",
            reason="x" * 2001,
        )
