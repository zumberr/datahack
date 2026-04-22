"""
Hybrid retriever over pgvector + Postgres full-text search, fused with Reciprocal Rank Fusion.

Dense: cosine similarity via pgvector hnsw index.
Lexical: tsvector with spanish dictionary via GIN index.

RRF fuses the two rankings without needing score normalization.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.rag.embedder import embed_query


@dataclass
class RetrievedChunk:
    id: int
    url: str
    title: str
    category: str | None
    section_title: str | None
    heading_path: str | None
    content: str
    similarity: float           # cosine similarity (0..1) from the dense channel; 0.0 if lexical-only
    rrf_score: float


def _vector_search(sess: Session, query_vec, k: int) -> list[tuple[int, float]]:
    rows = sess.execute(
        text("""
            SELECT id, 1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
            FROM documents
            ORDER BY embedding <=> CAST(:qvec AS vector)
            LIMIT :k
        """),
        {"qvec": query_vec.tolist(), "k": k},
    ).all()
    return [(int(r[0]), float(r[1])) for r in rows]


def _lexical_search(sess: Session, query: str, k: int) -> list[tuple[int, float]]:
    rows = sess.execute(
        text("""
            SELECT id, ts_rank(content_tsv, plainto_tsquery('spanish', :q)) AS rank
            FROM documents
            WHERE content_tsv @@ plainto_tsquery('spanish', :q)
            ORDER BY rank DESC
            LIMIT :k
        """),
        {"q": query, "k": k},
    ).all()
    return [(int(r[0]), float(r[1])) for r in rows]


def _rrf_fuse(
    dense: list[tuple[int, float]],
    lexical: list[tuple[int, float]],
    *,
    k: int = 60,
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion. Higher is better."""
    scores: dict[int, float] = {}
    for rank, (doc_id, _) in enumerate(dense):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    for rank, (doc_id, _) in enumerate(lexical):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def retrieve(sess: Session, question: str, *, top_k: int | None = None) -> list[RetrievedChunk]:
    settings = get_settings()
    top_k = top_k or settings.retrieval_top_k
    candidates = max(settings.retrieval_candidates, top_k * 2)

    qvec = embed_query(question)
    dense_hits = _vector_search(sess, qvec, candidates)
    lexical_hits = _lexical_search(sess, question, candidates)

    sim_map = {doc_id: sim for doc_id, sim in dense_hits}
    fused = _rrf_fuse(dense_hits, lexical_hits)[:top_k]
    if not fused:
        return []

    doc_ids = [doc_id for doc_id, _ in fused]
    rows = sess.execute(
        text("""
            SELECT id, url, title, category, section_title, heading_path, content
            FROM documents
            WHERE id = ANY(:ids)
        """),
        {"ids": doc_ids},
    ).all()
    row_map = {int(r[0]): r for r in rows}

    out: list[RetrievedChunk] = []
    for doc_id, rrf_score in fused:
        r = row_map.get(doc_id)
        if r is None:
            continue
        out.append(RetrievedChunk(
            id=int(r[0]),
            url=r[1],
            title=r[2],
            category=r[3],
            section_title=r[4],
            heading_path=r[5],
            content=r[6],
            similarity=float(sim_map.get(doc_id, 0.0)),
            rrf_score=float(rrf_score),
        ))
    return out
