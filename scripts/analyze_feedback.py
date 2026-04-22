#!/usr/bin/env python3
"""
Feedback triage — turn user feedback into actionable work for three owners.

Joins `feedback`, `session_turns`, and `turn_metadata` to produce three CSVs:

  data/feedback/corpus_gaps.csv
      Rating = missing_info OR (not_helpful AND confident=false).
      → Hand to the scraping/content team: pages that should exist but don't.

  data/feedback/hallucination_candidates.csv
      Rating = wrong AND confident=true.
      → Hand to the prompt/gate owner: gate passed and the LLM still misled.
      Review the SYSTEM_PROMPT and/or tighten the gate signals for these shapes.

  data/feedback/retrieval_misses.csv
      Rating IN (not_helpful, incomplete) AND confident=true.
      → Hand to the retrieval owner: LLM tried but the retrieved chunks were wrong
      or insufficient. Use as negatives for re-ranker training or RRF tuning.

Also prints per-rating counts and gate precision (helpful vs. not_helpful)
broken down by whether the gate said confident=true.

Usage:
    python scripts/analyze_feedback.py
    python scripts/analyze_feedback.py --since 2026-04-01
    python scripts/analyze_feedback.py --output-dir data/feedback
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.db import db_session

logger = logging.getLogger("feedback.analyze")

_BASE_SQL = """
    SELECT
        f.id            AS feedback_id,
        f.rating        AS rating,
        f.reason        AS reason,
        f.created_at    AS feedback_at,
        f.session_id    AS session_id,
        f.turn_id       AS turn_id,
        st.content      AS answer,
        u.content       AS question,
        tm.search_query AS search_query,
        tm.retrieved_urls AS retrieved_urls,
        tm.confident    AS confident,
        tm.confidence_score AS confidence_score,
        tm.signals      AS signals
    FROM feedback f
    JOIN session_turns st ON st.id = f.turn_id
    LEFT JOIN turn_metadata tm ON tm.turn_id = f.turn_id
    -- grab the user turn that came immediately before this assistant turn
    LEFT JOIN LATERAL (
        SELECT content FROM session_turns
        WHERE session_id = f.session_id
          AND role = 'user'
          AND created_at <= st.created_at
        ORDER BY created_at DESC
        LIMIT 1
    ) u ON TRUE
    WHERE (:since IS NULL OR f.created_at >= :since)
    ORDER BY f.created_at DESC
"""


def _load_rows(since: datetime | None) -> list[dict]:
    with db_session() as sess:
        rows = sess.execute(text(_BASE_SQL), {"since": since}).mappings().all()
    return [dict(r) for r in rows]


def _fmt_urls(urls) -> str:
    if not urls:
        return ""
    return " | ".join(urls)


def _fmt_signals(signals) -> str:
    if signals is None:
        return ""
    if isinstance(signals, str):
        try:
            signals = json.loads(signals)
        except json.JSONDecodeError:
            return signals
    return json.dumps(signals, ensure_ascii=False)


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in columns})


def _project_row(r: dict) -> dict:
    return {
        "feedback_id": r["feedback_id"],
        "feedback_at": r["feedback_at"].isoformat() if r["feedback_at"] else "",
        "session_id": str(r["session_id"]),
        "turn_id": r["turn_id"],
        "rating": r["rating"],
        "reason": r["reason"] or "",
        "question": (r["question"] or "").replace("\n", " ").strip(),
        "search_query": (r["search_query"] or "").replace("\n", " ").strip(),
        "answer": (r["answer"] or "").replace("\n", " ").strip(),
        "confident": r["confident"],
        "confidence_score": r["confidence_score"],
        "retrieved_urls": _fmt_urls(r["retrieved_urls"]),
        "signals": _fmt_signals(r["signals"]),
    }


def _bucket_rows(rows: list[dict]) -> dict[str, list[dict]]:
    gaps, halluc, miss = [], [], []
    for r in rows:
        rating = r["rating"]
        confident = bool(r["confident"])
        projected = _project_row(r)
        if rating == "missing_info" or (rating == "not_helpful" and not confident):
            gaps.append(projected)
        if rating == "wrong" and confident:
            halluc.append(projected)
        if rating in ("not_helpful", "incomplete") and confident:
            miss.append(projected)
    return {"corpus_gaps": gaps, "hallucination_candidates": halluc, "retrieval_misses": miss}


def _print_summary(rows: list[dict]) -> None:
    if not rows:
        print("No feedback recorded in the selected window.")
        return

    ratings = Counter(r["rating"] for r in rows)
    total = len(rows)
    print(f"\nTotal feedback entries: {total}")
    print("Rating distribution:")
    for rating, n in ratings.most_common():
        pct = 100.0 * n / total
        print(f"  {rating:14s}  {n:5d}  ({pct:5.1f}%)")

    # Gate precision: of the answers the gate marked `confident=true`, what
    # fraction did the user find helpful vs. actively bad?
    confident_rows = [r for r in rows if r["confident"]]
    non_confident_rows = [r for r in rows if not r["confident"]]

    def _counts(subset: list[dict]) -> str:
        if not subset:
            return "(no samples)"
        c = Counter(r["rating"] for r in subset)
        helpful = c["helpful"]
        bad = c["wrong"] + c["not_helpful"] + c["incomplete"]
        gap = c["missing_info"]
        return (f"helpful={helpful}  bad={bad}  missing_info={gap}  "
                f"n={len(subset)}")

    print("\nGate calibration (does the confidence gate agree with users?):")
    print(f"  confident=true   {_counts(confident_rows)}")
    print(f"  confident=false  {_counts(non_confident_rows)}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze user feedback and export triage CSVs")
    parser.add_argument("--since", type=str, default=None,
                        help="ISO date/time (e.g. 2026-04-01). Only include feedback from this point.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/feedback"),
                        help="Directory for the CSV exports.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    rows = _load_rows(since)
    logger.info("Loaded %d feedback rows", len(rows))
    _print_summary(rows)

    if not rows:
        return 0

    buckets = _bucket_rows(rows)
    columns = [
        "feedback_id", "feedback_at", "session_id", "turn_id", "rating", "reason",
        "question", "search_query", "answer",
        "confident", "confidence_score", "retrieved_urls", "signals",
    ]

    out_dir: Path = args.output_dir
    for name, bucket in buckets.items():
        path = out_dir / f"{name}.csv"
        _write_csv(path, bucket, columns)
        print(f"  wrote {len(bucket):5d} rows → {path}")

    print("\nNext steps:")
    print("  • corpus_gaps.csv → hand to scraping team (missing pages to ingest).")
    print("  • hallucination_candidates.csv → review SYSTEM_PROMPT and tighten the gate.")
    print("  • retrieval_misses.csv → use as hard negatives for retrieval/RRF tuning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
