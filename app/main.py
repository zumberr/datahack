"""
FastAPI entrypoint for BravoBot.

Endpoints:
  POST /chat      — main RAG pipeline (reformulation → retrieval → gate → generation)
  POST /sessions  — create a new conversation session
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
from app.models import (
    ChatRequest,
    ChatResponse,
    Citation,
    HealthResponse,
    SessionCreateResponse,
)
from app.rag.confidence import evaluate_confidence
from app.rag.generator import LLMError, get_llm
from app.rag.prompts import FALLBACK_ANSWER, SYSTEM_PROMPT, build_user_message
from app.rag.reformulator import reformulate
from app.rag.retriever import retrieve
from app.sessions import append_turn, create_session, get_or_create_session, load_history

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
        append_turn(sess, session_id, "assistant", answer)

    return ChatResponse(
        session_id=session_id,
        answer=answer,
        citations=citations,
        confident=confident,
    )
