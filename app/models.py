from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: UUID | None = None
    question: str = Field(..., min_length=1, max_length=2000)


class Citation(BaseModel):
    id: int
    url: str
    title: str
    snippet: str


class ChatResponse(BaseModel):
    session_id: UUID
    turn_id: int
    answer: str
    citations: list[Citation]
    confident: bool


class SessionCreateResponse(BaseModel):
    session_id: UUID


class HealthResponse(BaseModel):
    status: str
    database: bool
    providers: list[str]


FeedbackRating = Literal[
    "helpful",        # thumbs up
    "not_helpful",    # "esto no respondió mi pregunta"
    "wrong",          # the answer contained incorrect info
    "incomplete",     # partially answered
    "missing_info",   # the bot said it didn't have the info but user knows it should
]


class FeedbackRequest(BaseModel):
    session_id: UUID
    turn_id: int = Field(..., ge=1)
    rating: FeedbackRating
    reason: str | None = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    feedback_id: int
