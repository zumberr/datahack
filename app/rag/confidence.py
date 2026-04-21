"""
Multi-signal confidence gate.

A single similarity threshold lets noise through and falsely rejects legitimate queries
(especially comparatives and broad enumerations). This gate combines five signals:

  1. top1_similarity     — cosine of the best chunk
  2. top3_mean_similarity — average cosine of the 3 best chunks
  3. consistency         — how coherent the top-K is (shared category, URL overlap)
  4. keyword_coverage    — fraction of salient question terms present in top-3 content
  5. format_match        — if question asks for a number/date/price, top-3 must contain digits

Passes if at least N signals succeed AND neither top1 nor top3_mean is catastrophically low.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from app.config import get_settings
from app.rag.retriever import RetrievedChunk

# Lightweight Spanish stopword list — enough to filter salient keywords from user questions.
_ES_STOPWORDS = {
    "a", "al", "algo", "algun", "alguna", "algunas", "alguno", "algunos",
    "ante", "antes", "aqui", "aquel", "aquella", "asi", "aun",
    "cada", "como", "con", "cual", "cuales", "cualquier", "cuando", "cuanto", "cuanta",
    "de", "del", "desde", "donde", "dos", "durante",
    "el", "ella", "ellas", "ellos", "en", "entre", "era", "eras", "eres", "es", "esa",
    "esas", "ese", "eso", "esos", "esta", "estan", "estar", "estas", "este", "esto", "estos",
    "fue", "fui", "fuiste", "ha", "hace", "hacer", "han", "has", "hasta", "hay",
    "la", "las", "le", "les", "lo", "los", "mas", "me", "mi", "mis", "mucho", "muchos",
    "muy", "nada", "ni", "no", "nos", "nosotros", "o", "os", "otra", "otras", "otro", "otros",
    "para", "pero", "poca", "poco", "pocos", "por", "porque", "pues", "que", "quien",
    "quienes", "se", "segun", "sea", "ser", "si", "sin", "sobre", "solo", "son", "soy",
    "su", "sus", "tal", "tambien", "te", "ti", "tiene", "tienen", "todo", "todos", "tu",
    "tus", "tuyo", "un", "una", "uno", "unos", "y", "ya", "yo", "cuales", "cuales",
    "hola", "ofrece", "tiene", "tienen", "ser", "son", "esta",
}

_NUMBER_Q_PATTERNS = re.compile(
    r"\b(cu[aá]nto|cu[aá]nta|cu[aá]l(?:es)?\s+(?:es\s+)?el?\s+(?:valor|costo|precio)|"
    r"costo|valor|precio|matr[ií]cula|cu[aá]ndo|fecha|duraci[oó]n|"
    r"requisitos?|plazo|semestres?|creditos?|horas)\b",
    re.IGNORECASE,
)
_DIGIT_RE = re.compile(r"\d")
_WORD_RE = re.compile(r"[A-Za-záéíóúñÑÁÉÍÓÚÜü]{4,}")


@dataclass
class ConfidenceResult:
    passed: bool
    score: float
    signals: dict[str, bool]
    details: dict[str, float | str]


def _ascii_fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def _extract_keywords(question: str) -> list[str]:
    folded = _ascii_fold(question)
    toks = _WORD_RE.findall(folded)
    return [t for t in toks if t not in _ES_STOPWORDS and len(t) >= 4]


def _compute_keyword_coverage(keywords: list[str], chunks: Sequence[RetrievedChunk]) -> float:
    if not keywords:
        return 1.0
    if not chunks:
        return 0.0
    corpus = _ascii_fold(" ".join(c.content for c in chunks[:3]))
    hits = sum(1 for kw in keywords if kw in corpus)
    return hits / len(keywords)


def _compute_consistency(chunks: Sequence[RetrievedChunk]) -> float:
    """
    Returns a 0..1 score:
    - Higher when retrieved chunks share a category.
    - Higher when they come from few distinct URLs (focused topic).
    - Lower for grab-bag results across unrelated pages.
    """
    if not chunks:
        return 0.0
    top = chunks[:5]
    categories = [c.category for c in top if c.category]
    cat_ratio = 0.0
    if categories:
        most_common = max(set(categories), key=categories.count)
        cat_ratio = categories.count(most_common) / len(top)
    urls = {c.url for c in top}
    url_ratio = 1.0 - (len(urls) - 1) / max(1, len(top))   # 1.0 if all same URL, 0.2 if all different
    return max(0.0, min(1.0, 0.6 * cat_ratio + 0.4 * url_ratio))


def _asks_for_number(question: str) -> bool:
    return bool(_NUMBER_Q_PATTERNS.search(question))


def _top3_has_digits(chunks: Sequence[RetrievedChunk]) -> bool:
    for c in chunks[:3]:
        if _DIGIT_RE.search(c.content):
            return True
    return False


def evaluate_confidence(question: str, chunks: Sequence[RetrievedChunk]) -> ConfidenceResult:
    settings = get_settings()

    if not chunks:
        return ConfidenceResult(
            passed=False,
            score=0.0,
            signals={k: False for k in ("top1", "top3_mean", "consistency",
                                         "keyword_coverage", "format_match")},
            details={"reason": "no chunks retrieved"},
        )

    top1_sim = chunks[0].similarity
    top3 = chunks[:3]
    top3_mean = sum(c.similarity for c in top3) / len(top3)

    consistency = _compute_consistency(chunks)
    keywords = _extract_keywords(question)
    keyword_coverage = _compute_keyword_coverage(keywords, chunks)

    expects_number = _asks_for_number(question)
    has_numbers = _top3_has_digits(chunks)
    format_match_applicable = expects_number
    format_match_ok = (not expects_number) or has_numbers

    signals = {
        "top1": top1_sim >= settings.confidence_top1_min,
        "top3_mean": top3_mean >= settings.confidence_top3_mean_min,
        "consistency": consistency >= settings.confidence_consistency_min,
        "keyword_coverage": keyword_coverage >= settings.confidence_keyword_coverage_min,
        "format_match": format_match_ok,
    }

    passed_count = sum(1 for v in signals.values() if v)
    catastrophic = (
        top1_sim < settings.confidence_catastrophic_min
        and top3_mean < settings.confidence_catastrophic_min
    )

    passed = passed_count >= settings.confidence_signals_required and not catastrophic

    score = (
        0.30 * top1_sim
        + 0.20 * top3_mean
        + 0.15 * consistency
        + 0.25 * keyword_coverage
        + 0.10 * (1.0 if format_match_ok else 0.0)
    )

    details: dict[str, float | str] = {
        "top1_similarity": round(top1_sim, 4),
        "top3_mean_similarity": round(top3_mean, 4),
        "consistency": round(consistency, 4),
        "keyword_coverage": round(keyword_coverage, 4),
        "keywords_used": ",".join(keywords) or "(none)",
        "format_match_applicable": "yes" if format_match_applicable else "no",
        "format_match_ok": "yes" if format_match_ok else "no",
        "signals_passed": passed_count,
        "catastrophic": "yes" if catastrophic else "no",
    }

    return ConfidenceResult(passed=passed, score=round(score, 4), signals=signals, details=details)
