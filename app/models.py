from __future__ import annotations

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
    answer: str
    citations: list[Citation]
    confident: bool


class SessionCreateResponse(BaseModel):
    session_id: UUID


class HealthResponse(BaseModel):
    status: str
    database: bool
    providers: list[str]
