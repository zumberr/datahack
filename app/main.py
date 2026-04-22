"""
FastAPI entrypoint for BravoBot.

Endpoints:
  POST /chat      — main RAG pipeline (reformulation → retrieval → gate → generation)
  POST /sessions  — create a new conversation session
  POST /feedback  — user feedback on an assistant turn (closes the quality loop)
  GET  /health    — DB connectivity + LLM provider availability
"""
from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.db import db_session
from app.feedback import FeedbackError, record_feedback
from app.models import (
    ChatRequest,
    ChatResponse,
    Citation,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    SessionCreateResponse,
)
from app.rag.confidence import evaluate_confidence
from app.rag.generator import LLMError, get_llm
from app.rag.prompts import FALLBACK_ANSWER, SYSTEM_PROMPT, build_user_message
from app.rag.reformulator import reformulate
from app.rag.retriever import retrieve
from app.sessions import (
    append_turn,
    create_session,
    get_or_create_session,
    load_history,
    save_turn_metadata,
)

logger = logging.getLogger("bravobot.api")

_CITATION_RE = re.compile(r"\[(\d+)\]")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info("BravoBot starting. Providers configured: %s", settings.available_providers())
    yield


app = FastAPI(title="BravoBot", version="0.1.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _extract_used_citations(answer: str, chunks) -> list[Citation]:
    """Extract [N] markers actually present in the answer; map to chunk metadata."""
    used_ids: list[int] = []
    for m in _CITATION_RE.finditer(answer):
        cid = int(m.group(1))
        if cid not in used_ids and 1 <= cid <= len(chunks):
            used_ids.append(cid)

    out: list[Citation] = []
    for cid in used_ids:
        chunk = chunks[cid - 1]
        snippet = chunk.content.strip()
        if len(snippet) > 280:
            snippet = snippet[:277] + "..."
        out.append(Citation(id=cid, url=chunk.url, title=chunk.title, snippet=snippet))
    return out


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db_ok = False
    try:
        with db_session() as sess:
            sess.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)

    providers = get_settings().available_providers()
    status = "ok" if db_ok and providers else "degraded"
    return HealthResponse(status=status, database=db_ok, providers=providers)


@app.post("/sessions", response_model=SessionCreateResponse)
def new_session() -> SessionCreateResponse:
    with db_session() as sess:
        sid = create_session(sess)
    return SessionCreateResponse(session_id=sid)


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    with db_session() as sess:
        session_id = get_or_create_session(sess, req.session_id)
        history = load_history(sess, session_id)

        search_query = reformulate(question, history)
        if search_query != question:
            logger.info("Reformulated: %r → %r", question, search_query)

        chunks = retrieve(sess, search_query)
        confidence = evaluate_confidence(search_query, chunks)
        logger.info("Confidence: passed=%s score=%.3f signals=%s details=%s",
                    confidence.passed, confidence.score, confidence.signals, confidence.details)

        if not confidence.passed or not chunks:
            answer = FALLBACK_ANSWER
            citations: list[Citation] = []
            confident = False
        else:
            user_msg = build_user_message(search_query, chunks)
            try:
                llm = get_llm()
                answer = llm.complete(SYSTEM_PROMPT, user_msg, temperature=0.2, max_tokens=800)
            except LLMError as exc:
                logger.error("LLM generation failed entirely: %s", exc)
                raise HTTPException(status_code=503, detail="LLM providers unavailable") from exc

            citations = _extract_used_citations(answer, chunks)
            confident = True

        append_turn(sess, session_id, "user", question)
        assistant_turn_id = append_turn(sess, session_id, "assistant", answer)

        # Persist per-turn retrieval/gate metadata so /feedback can later attribute
        # complaints to a specific decision (retrieval vs. gate vs. prompt).
        save_turn_metadata(
            sess,
            assistant_turn_id,
            search_query=search_query,
            retrieved_ids=[c.id for c in chunks],
            retrieved_urls=[c.url for c in chunks],
            confident=confident,
            confidence_score=confidence.score,
            signals={
                "passed": confidence.signals,
                "details": confidence.details,
            },
        )

    return ChatResponse(
        session_id=session_id,
        turn_id=assistant_turn_id,
        answer=answer,
        citations=citations,
        confident=confident,
    )


@app.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest) -> FeedbackResponse:
    """Record user feedback on a specific assistant turn.

    Ratings:
      - helpful       — positive signal
      - not_helpful   — "esto no respondió mi pregunta"
      - wrong         — answer contained incorrect info (hallucination candidate)
      - incomplete    — partially answered, user expected more detail
      - missing_info  — bot said it didn't have the info, user disagrees (corpus gap)
    """
    try:
        with db_session() as sess:
            fb_id = record_feedback(
                sess,
                session_id=req.session_id,
                turn_id=req.turn_id,
                rating=req.rating,
                reason=req.reason,
            )
    except FeedbackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FeedbackResponse(feedback_id=fb_id)
