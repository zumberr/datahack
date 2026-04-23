#!/usr/bin/env python3
"""
Ingest JSON (or a directory of JSONs) scraped from pascualbravo.edu.co into Postgres+pgvector.

Usage:
    python scripts/ingest.py <path-to-json-or-directory>

Idempotent: re-running replaces chunks for documents whose source_hash changed.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.db import db_session
from app.ingest.normalizer import normalize_many
from app.rag.chunker import chunk_document
from app.rag.embedder import embed_passages

logger = logging.getLogger("ingest")


def _load_json_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        # Grouped format: {"preguntas": [...], "programas": [...]}.
        # If any value is a list of dicts, flatten them all into one list.
        has_sublists = any(
            isinstance(v, list) and v and isinstance(v[0], dict) for v in data.values()
        )
        if has_sublists:
            out: list[dict[str, Any]] = []
            for v in data.values():
                if isinstance(v, list):
                    out.extend(d for d in v if isinstance(d, dict))
            logger.info("Flattened grouped JSON from %s (%d items)", path.name, len(out))
            return out
        # Single document object.
        return [data]
    raise ValueError(f"{path}: expected object or list, got {type(data).__name__}")


def _collect_inputs(target: Path) -> list[dict[str, Any]]:
    if target.is_file():
        return _load_json_file(target)
    if target.is_dir():
        out: list[dict[str, Any]] = []
        for p in sorted(target.glob("**/*.json")):
            try:
                out.extend(_load_json_file(p))
            except Exception as exc:
                logger.error("Failed to read %s: %s", p, exc)
        return out
    raise FileNotFoundError(target)


def _upsert_chunks(chunks: list, embeddings) -> tuple[int, int]:
    """
    For each (url, source_hash) group:
      - if url exists with a different source_hash, delete old rows
      - insert new chunks
    Returns (inserted, skipped_duplicate_hash).
    """
    if not chunks:
        return 0, 0

    inserted = 0
    skipped = 0
    with db_session() as sess:
        urls_to_hash: dict[str, str] = {c.url: c.source_hash for c in chunks}

        existing = sess.execute(
            text("SELECT url, source_hash FROM documents WHERE url = ANY(:urls)"),
            {"urls": list(urls_to_hash.keys())},
        ).all()
        existing_map: dict[str, set[str]] = {}
        for url, shash in existing:
            existing_map.setdefault(url, set()).add(shash)

        urls_to_delete: list[str] = []
        urls_to_skip: set[str] = set()
        for url, new_hash in urls_to_hash.items():
            present = existing_map.get(url, set())
            if not present:
                continue
            if new_hash in present and len(present) == 1:
                urls_to_skip.add(url)
            else:
                urls_to_delete.append(url)

        if urls_to_delete:
            sess.execute(
                text("DELETE FROM documents WHERE url = ANY(:urls)"),
                {"urls": urls_to_delete},
            )

        for chunk, vec in zip(chunks, embeddings):
            if chunk.url in urls_to_skip:
                skipped += 1
                continue
            sess.execute(
                text("""
                    INSERT INTO documents
                        (url, title, category, section_title, heading_path,
                         chunk_index, content, embedding, source_hash)
                    VALUES
                        (:url, :title, :category, :section_title, :heading_path,
                         :chunk_index, :content, :embedding, :source_hash)
                    ON CONFLICT (url, chunk_index) DO UPDATE SET
                        title = EXCLUDED.title,
                        category = EXCLUDED.category,
                        section_title = EXCLUDED.section_title,
                        heading_path = EXCLUDED.heading_path,
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding,
                        source_hash = EXCLUDED.source_hash
                """),
                {
                    "url": chunk.url,
                    "title": chunk.title,
                    "category": chunk.category,
                    "section_title": chunk.section_title,
                    "heading_path": chunk.heading_path,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "embedding": vec.tolist(),
                    "source_hash": chunk.source_hash,
                },
            )
            inserted += 1

    return inserted, skipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest scraped JSON into BravoBot's pgvector store")
    parser.add_argument("path", type=Path, help="JSON file or directory containing .json files")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    raw = _collect_inputs(args.path)
    logger.info("Loaded %d raw records from %s", len(raw), args.path)

    docs, warnings = normalize_many(raw)
    logger.info("Normalized: %d documents accepted, %d warnings", len(docs), len(warnings))
    for w in warnings:
        logger.debug("  %s", w)

    if not docs:
        logger.warning("No valid documents to ingest.")
        return 1

    all_chunks = []
    for doc in docs:
        all_chunks.extend(chunk_document(doc))
    logger.info("Chunked into %d passages across %d documents", len(all_chunks), len(docs))

    texts = [f"{c.title}\n{c.section_title}\n\n{c.content}" for c in all_chunks]
    logger.info("Embedding %d chunks (first run may download the model)...", len(texts))
    embeddings = embed_passages(texts)
    logger.info("Embedded shape=%s", embeddings.shape)

    inserted, skipped = _upsert_chunks(all_chunks, embeddings)
    logger.info("Upsert done: inserted=%d, skipped_unchanged=%d", inserted, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
