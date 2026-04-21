"""
Hierarchical chunker.

Strategy:
1. Split document by headings (Markdown #/##/###, ALL-CAPS lines, "Term:" patterns).
2. Within each section, if it exceeds MAX_TOKENS, split by paragraphs with overlap.
3. Sections are NEVER fused across different headings — each chunk is semantically atomic.
4. Each chunk carries section_title and heading_path metadata.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ingest.schemas import NormalizedDocument

MAX_TOKENS = 800
OVERLAP_TOKENS = 100

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
_ALLCAPS_HEADING_RE = re.compile(r"^([A-ZÁÉÍÓÚÑÜ][A-ZÁÉÍÓÚÑÜ \-0-9]{3,80})\s*$", re.M)
_LABEL_HEADING_RE = re.compile(r"^([A-ZÁÉÍÓÚÑÜa-záéíóúñü][A-Za-záéíóúñÑü ]{3,60}):\s*$", re.M)


@dataclass
class Chunk:
    url: str
    title: str
    category: str
    chunk_index: int
    content: str
    section_title: str
    heading_path: str
    source_hash: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class _Section:
    level: int
    heading: str
    body: str
    parent_path: list[str]

    @property
    def full_path(self) -> list[str]:
        return self.parent_path + [self.heading]


def _approx_tokens(text: str) -> int:
    """Fast approximation: ~1 token per 0.75 words. Good enough for chunk sizing."""
    return max(1, int(len(text.split()) / 0.75))


def _split_into_sections(title: str, content: str) -> list[_Section]:
    """Walk the text once and cut at heading boundaries, tracking heading depth."""
    markers: list[tuple[int, int, str]] = []  # (start_offset, level, heading)

    for m in _MD_HEADING_RE.finditer(content):
        markers.append((m.start(), len(m.group(1)), m.group(2).strip()))

    if not markers:
        for m in _ALLCAPS_HEADING_RE.finditer(content):
            heading = m.group(1).strip()
            if len(heading.split()) <= 10:
                markers.append((m.start(), 2, heading))
        for m in _LABEL_HEADING_RE.finditer(content):
            markers.append((m.start(), 3, m.group(1).strip()))

    markers.sort(key=lambda x: x[0])

    if not markers:
        return [_Section(level=1, heading=title, body=content.strip(), parent_path=[])]

    sections: list[_Section] = []
    path_stack: list[tuple[int, str]] = []

    first = markers[0]
    preamble = content[: first[0]].strip()
    if preamble:
        sections.append(_Section(level=1, heading=title, body=preamble, parent_path=[]))

    for i, (start, level, heading) in enumerate(markers):
        end = markers[i + 1][0] if i + 1 < len(markers) else len(content)
        body_start = content.find("\n", start)
        body_start = body_start if body_start != -1 else start
        body = content[body_start:end].strip()

        while path_stack and path_stack[-1][0] >= level:
            path_stack.pop()
        parent_path = [h for _, h in path_stack]

        sections.append(_Section(level=level, heading=heading, body=body, parent_path=parent_path))
        path_stack.append((level, heading))

    return [s for s in sections if s.body]


def _split_by_paragraphs(body: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
    if not paragraphs:
        return [body]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = _approx_tokens(para)
        if current and current_tokens + para_tokens > max_tokens:
            chunks.append("\n\n".join(current))
            tail: list[str] = []
            tail_tokens = 0
            for prev in reversed(current):
                t = _approx_tokens(prev)
                if tail_tokens + t > overlap_tokens:
                    break
                tail.insert(0, prev)
                tail_tokens += t
            current = tail
            current_tokens = tail_tokens

        if para_tokens > max_tokens:
            words = para.split()
            step = int(max_tokens * 0.75)
            over = int(overlap_tokens * 0.75)
            for start in range(0, len(words), max(1, step - over)):
                piece = " ".join(words[start : start + step])
                if piece:
                    chunks.append(piece)
            current = []
            current_tokens = 0
            continue

        current.append(para)
        current_tokens += para_tokens

    if current:
        chunks.append("\n\n".join(current))

    return chunks


MIN_CHUNK_TOKENS = 5


def chunk_document(doc: NormalizedDocument) -> list[Chunk]:
    """
    Preserve semantic boundaries: each section becomes at least one chunk. Sections larger than
    MAX_TOKENS are split by paragraphs with overlap; never mix content across sections.
    """
    sections = _split_into_sections(doc.title, doc.content)

    chunks: list[Chunk] = []
    idx = 0
    for sec in sections:
        pieces = _split_by_paragraphs(sec.body, MAX_TOKENS, OVERLAP_TOKENS)
        heading_path = (
            " > ".join([doc.title] + sec.full_path)
            if sec.heading != doc.title
            else doc.title
        )
        for piece in pieces:
            if _approx_tokens(piece) < MIN_CHUNK_TOKENS:
                continue
            chunks.append(Chunk(
                url=doc.url,
                title=doc.title,
                category=doc.category,
                chunk_index=idx,
                content=piece,
                section_title=sec.heading,
                heading_path=heading_path,
                source_hash=doc.source_hash,
            ))
            idx += 1

    if not chunks:
        chunks.append(Chunk(
            url=doc.url,
            title=doc.title,
            category=doc.category,
            chunk_index=0,
            content=doc.content,
            section_title=doc.title,
            heading_path=doc.title,
            source_hash=doc.source_hash,
        ))
    return chunks
