"""
Question reformulator — turns a follow-up question into a standalone one.

"¿Y cuánto cuesta?" after "¿Qué es Ingeniería Mecánica?" becomes
"¿Cuánto cuesta Ingeniería Mecánica?".

Only invoked when there's at least one prior user turn; otherwise the original question is returned.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from app.rag.generator import LLMError, get_llm
from app.rag.prompts import REFORMULATE_SYSTEM
from app.sessions import Turn

logger = logging.getLogger("bravobot.reformulator")


def _has_prior_user_turn(history: Sequence[Turn]) -> bool:
    return any(t.role == "user" for t in history)


def reformulate(question: str, history: Sequence[Turn]) -> str:
    """Return a standalone version of `question` using `history` if follow-up context is needed."""
    if not _has_prior_user_turn(history):
        return question

    convo_lines = [f"{t.role.upper()}: {t.content}" for t in history]
    convo_lines.append(f"USER: {question}")
    user_msg = "\n".join(convo_lines)

    try:
        llm = get_llm()
        rewritten = llm.complete(
            REFORMULATE_SYSTEM,
            user_msg,
            temperature=0.0,
            max_tokens=120,
        ).strip().strip('"').strip("'")
        if not rewritten or len(rewritten) > 500:
            return question
        return rewritten
    except LLMError as exc:
        logger.warning("Reformulation failed, using original question: %s", exc)
        return question
