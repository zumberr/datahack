"""
Normalizer — transforms messy third-party JSON into clean NormalizedDocument.

Handles:
- Field aliases (url/link/href, title/titulo/heading, content/body/text/html, category/tipo)
- Domain validation (only pascualbravo.edu.co)
- HTML stripping when content looks like HTML
- Unicode normalization + control char removal
- Whitespace collapsing
- Empty/too-short content rejection
- Duplicate detection via content hash
- Category inference from URL path + keyword heuristics when missing
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

from selectolax.parser import HTMLParser

from app.ingest.schemas import NormalizedDocument

_ALIASES_URL = ("url", "link", "href", "pageUrl", "page_url", "source")
_ALIASES_TITLE = ("title", "titulo", "heading", "name", "nombre")
_ALIASES_CONTENT = ("content", "body", "text", "contenido", "html", "raw_text", "markdown")
_ALIASES_CATEGORY = ("category", "categoria", "section", "seccion", "tipo", "type")

_ALLOWED_HOST_SUFFIX = "pascualbravo.edu.co"
_MIN_CONTENT_LEN = 80
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_WHITESPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_HTML_TAG_HINT_RE = re.compile(r"<(p|div|span|a|ul|li|h[1-6]|table|img|br|article|section)\b", re.I)

# URL path → category mapping (first match wins; order matters)
_URL_CATEGORY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/pregrado", re.I), "pregrado"),
    (re.compile(r"/posgrado|/especializacion|/maestria", re.I), "posgrado"),
    (re.compile(r"/tecnolog", re.I), "pregrado"),  # tecnologías are pregrado-level
    (re.compile(r"/admision|/inscripc", re.I), "admisiones"),
    (re.compile(r"/costo|/matricul|/derechos-pecuniarios|/valor", re.I), "costos"),
    (re.compile(r"/bienestar|/beca|/beneficio", re.I), "beneficios"),
    (re.compile(r"/perfil", re.I), "perfiles"),
]

_KEYWORD_CATEGORY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bespecializaci[oó]n|\bmaestr[ií]a\b", re.I), "posgrado"),
    (re.compile(r"\bingenier[ií]a|\btecnolog[ií]a|\bt[eé]cnica profesional\b", re.I), "pregrado"),
    (re.compile(r"\binscripc|\bproceso de admisi|\brequisitos de admisi", re.I), "admisiones"),
    (re.compile(r"\bmatr[ií]cula|\bderechos pecuniarios|\bvalor del semestre|\bcosto\b", re.I),
     "costos"),
    (re.compile(r"\bperfil ocupacional|\bperfil profesional|\begresad", re.I), "perfiles"),
    (re.compile(r"\bbeca\b|\bbienestar|\bbeneficio\b|\bsubsidio", re.I), "beneficios"),
]


def _pick(data: dict[str, Any], aliases: Iterable[str]) -> Any | None:
    lower = {k.lower(): v for k, v in data.items()}
    for alias in aliases:
        val = lower.get(alias.lower())
        if val is not None and val != "":
            return val
    return None


def _looks_like_html(text: str) -> bool:
    return bool(_HTML_TAG_HINT_RE.search(text))


def _strip_html(text: str) -> str:
    tree = HTMLParser(text)
    for tag in ("script", "style", "nav", "footer", "header", "aside", "noscript"):
        for node in tree.css(tag):
            node.decompose()
    extracted = tree.body.text(separator="\n") if tree.body else tree.text(separator="\n")
    return extracted or ""


def _clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL_CHARS_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_MULTI_WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def _is_allowed_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.netloc or "").lower().split(":")[0]
    return host == _ALLOWED_HOST_SUFFIX or host.endswith("." + _ALLOWED_HOST_SUFFIX)


def _infer_category(url: str, title: str, content: str) -> str:
    for pattern, cat in _URL_CATEGORY_PATTERNS:
        if pattern.search(url):
            return cat
    haystack = f"{title}\n{content[:500]}"
    for pattern, cat in _KEYWORD_CATEGORY_PATTERNS:
        if pattern.search(haystack):
            return cat
    return "otros"


def _content_hash(url: str, content: str) -> str:
    h = hashlib.sha256()
    h.update(url.encode("utf-8"))
    h.update(b"\x00")
    h.update(content.encode("utf-8"))
    return h.hexdigest()


def normalize_one(data: dict[str, Any]) -> tuple[NormalizedDocument | None, list[str]]:
    """Normalize a single raw document. Returns (doc, warnings). doc is None if rejected."""
    warnings: list[str] = []

    url_raw = _pick(data, _ALIASES_URL)
    if not isinstance(url_raw, str) or not url_raw.strip():
        return None, ["rejected: missing url"]
    url = url_raw.strip()

    if not _is_allowed_url(url):
        return None, [f"rejected: url not in pascualbravo.edu.co ({url})"]

    title_raw = _pick(data, _ALIASES_TITLE)
    title = title_raw.strip() if isinstance(title_raw, str) else ""
    if not title:
        # Fall back to last meaningful URL segment
        path_parts = [p for p in urlparse(url).path.split("/") if p]
        title = path_parts[-1].replace("-", " ").replace("_", " ").title() if path_parts else "Sin título"
        warnings.append("title missing; inferred from URL")

    content_raw = _pick(data, _ALIASES_CONTENT)
    if not isinstance(content_raw, str) or not content_raw.strip():
        return None, ["rejected: missing content"]

    if _looks_like_html(content_raw):
        content = _strip_html(content_raw)
        warnings.append("html stripped")
    else:
        content = content_raw

    content = _clean_text(content)

    if len(content) < _MIN_CONTENT_LEN:
        return None, [f"rejected: content too short ({len(content)} chars)"]

    category_raw = _pick(data, _ALIASES_CATEGORY)
    if isinstance(category_raw, str) and category_raw.strip():
        category = category_raw.strip().lower()
    else:
        category = _infer_category(url, title, content)
        warnings.append(f"category inferred as '{category}'")

    doc = NormalizedDocument(
        url=url,
        title=title,
        category=category,
        content=content,
        source_hash=_content_hash(url, content),
        warnings=warnings,
    )
    return doc, warnings


def normalize_many(items: Iterable[dict[str, Any]]) -> tuple[list[NormalizedDocument], list[str]]:
    """Normalize a batch; dedupe by (url, content_hash). Returns (docs, global_warnings)."""
    seen_hashes: set[str] = set()
    seen_urls_contents: dict[str, str] = {}
    out: list[NormalizedDocument] = []
    log: list[str] = []

    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            log.append(f"#{idx}: rejected (not a dict)")
            continue
        doc, warns = normalize_one(raw)
        if doc is None:
            log.extend(f"#{idx}: {w}" for w in warns)
            continue

        if doc.source_hash in seen_hashes:
            log.append(f"#{idx}: duplicate content hash ({doc.url})")
            continue

        prev_hash = seen_urls_contents.get(doc.url)
        if prev_hash and prev_hash != doc.source_hash:
            log.append(f"#{idx}: url seen with different content — keeping last ({doc.url})")
            out = [d for d in out if d.url != doc.url]

        seen_hashes.add(doc.source_hash)
        seen_urls_contents[doc.url] = doc.source_hash
        out.append(doc)
        for w in warns:
            log.append(f"#{idx} [{doc.url}]: {w}")

    return out, log
