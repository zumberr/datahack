"""
Normalizer — transforms messy third-party JSON into clean NormalizedDocument.

Handles:
- Field aliases (url/link/href, title/titulo/heading, content/body/text/html/presentation, category/tipo)
- Category canonicalization (pregrados→pregrado, posgrados→posgrado, etc.)
- Domain validation (only pascualbravo.edu.co)
- HTML stripping when content looks like HTML
- Unicode normalization + control char removal
- Whitespace collapsing
- Rich metadata enrichment: when scraper provides structured fields (faculty,
  modalidad, program_title, inscriptions, class_start, price_table, summary),
  they are folded into the document content as Markdown sections so the chunker
  and retriever can use them directly.
- Fallback build-from-metadata: if `presentation`/`content` is missing but rich
  metadata is present, a synthetic document is built so the program is still
  searchable (e.g. Tecnología en Producción Industrial in the real dump).
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

_ALIASES_URL = ("url", "link", "href", "pageUrl", "page_url", "source", "source_url")
_ALIASES_TITLE = ("title", "titulo", "heading", "name", "nombre", "program_title", "question")
# `presentation` is the real scraper's main body field; keep it alongside classic aliases.
# `answer` covers FAQ items; `program_overview` covers posgrado program descriptions.
_ALIASES_CONTENT = (
    "content", "body", "text", "contenido", "html", "raw_text", "markdown",
    "presentation", "presentacion", "answer", "program_overview",
)
_ALIASES_CATEGORY = ("category", "categoria", "section", "seccion", "tipo", "type", "item_type")

_ALLOWED_HOST_SUFFIX = "pascualbravo.edu.co"
_MIN_CONTENT_LEN = 80
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MULTI_WHITESPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_HTML_TAG_HINT_RE = re.compile(r"<(p|div|span|a|ul|li|h[1-6]|table|img|br|article|section)\b", re.I)

# Canonical category values the rest of the pipeline expects.
# Everything plural / alias → singular canonical form.
_CATEGORY_CANONICAL: dict[str, str] = {
    "pregrado": "pregrado",
    "pregrados": "pregrado",
    "programa": "pregrado",
    "programas": "pregrado",
    "tecnologia": "pregrado",
    "tecnologias": "pregrado",
    "tecnología": "pregrado",
    "tecnologías": "pregrado",
    "ingenieria": "pregrado",
    "ingenierias": "pregrado",
    "ingeniería": "pregrado",
    "ingenierías": "pregrado",
    "posgrado": "posgrado",
    "posgrados": "posgrado",
    "especializacion": "posgrado",
    "especializaciones": "posgrado",
    "especialización": "posgrado",
    "especializaciónes": "posgrado",
    "maestria": "posgrado",
    "maestrias": "posgrado",
    "maestría": "posgrado",
    "maestrías": "posgrado",
    "doctorado": "posgrado",
    "doctorados": "posgrado",
    "faq": "otros",
    "admision": "admisiones",
    "admisiones": "admisiones",
    "admisión": "admisiones",
    "inscripcion": "admisiones",
    "inscripciones": "admisiones",
    "inscripción": "admisiones",
    "costo": "costos",
    "costos": "costos",
    "precios": "costos",
    "precio": "costos",
    "matricula": "costos",
    "matrícula": "costos",
    "pecuniarios": "costos",
    "beneficio": "beneficios",
    "beneficios": "beneficios",
    "beca": "beneficios",
    "becas": "beneficios",
    "bienestar": "beneficios",
    "perfil": "perfiles",
    "perfiles": "perfiles",
    "calendario": "calendario",
    "fechas": "calendario",
    "otros": "otros",
}

# URL path → category mapping (first match wins; order matters)
_URL_CATEGORY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"/pregrado", re.I), "pregrado"),
    (re.compile(r"/posgrado|/especializacion|/maestria", re.I), "posgrado"),
    (re.compile(r"/tecnolog", re.I), "pregrado"),  # tecnologías are pregrado-level
    (re.compile(r"/admision|/inscripc", re.I), "admisiones"),
    (re.compile(r"/costo|/matricul|/derechos-pecuniarios|/valor", re.I), "costos"),
    (re.compile(r"/bienestar|/beca|/beneficio", re.I), "beneficios"),
    (re.compile(r"/perfil", re.I), "perfiles"),
    (re.compile(r"/calendario", re.I), "calendario"),
]

_KEYWORD_CATEGORY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bespecializaci[oó]n|\bmaestr[ií]a\b", re.I), "posgrado"),
    (re.compile(r"\bingenier[ií]a|\btecnolog[ií]a|\bt[eé]cnica profesional\b", re.I), "pregrado"),
    (re.compile(r"\binscripc|\bproceso de admisi|\brequisitos de admisi", re.I), "admisiones"),
    (re.compile(r"\bmatr[ií]cula|\bderechos pecuniarios|\bvalor del semestre|\bcosto\b", re.I),
     "costos"),
    (re.compile(r"\bperfil ocupacional|\bperfil profesional|\begresad", re.I), "perfiles"),
    (re.compile(r"\bbeca\b|\bbienestar|\bbeneficio\b|\bsubsidio", re.I), "beneficios"),
    (re.compile(r"\bcalendario\b|\bfecha", re.I), "calendario"),
]

# Fields that trigger the rich-metadata enrichment path.
_METADATA_KEYS_FOR_RICH_CONTENT = (
    "summary", "faculty", "modalidad", "program_title",
    "inscriptions", "class_start", "price_table",
    # Posgrado-specific fields from the scraper:
    "cost", "schedule", "credits", "snies", "registro_calificado",
    "vigencia", "semesters", "program_heading", "program_overview",
)


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


def _canonicalize_category(raw: str) -> str | None:
    """Map any scraper variant to the canonical set used by the retriever/prompts.

    Returns None if the value is not recognized so the caller can fall back to inference.
    """
    if not raw:
        return None
    key = raw.strip().lower()
    # Keep accents and strip trailing punctuation/whitespace only.
    key = key.rstrip(".,;: ")
    return _CATEGORY_CANONICAL.get(key)


def _infer_category(url: str, title: str, content: str) -> str:
    for pattern, cat in _URL_CATEGORY_PATTERNS:
        if pattern.search(url):
            return cat
    haystack = f"{title}\n{content[:500]}"
    for pattern, cat in _KEYWORD_CATEGORY_PATTERNS:
        if pattern.search(haystack):
            return cat
    return "otros"


def _render_table_content(rows: list[Any], table_name: str | None = None) -> str:
    """Convert a list of dicts or list of lists (scraped table rows) into a Markdown table string.

    Each dict represents one row; keys become column headers.
    If rows is a list of lists, it's rendered without headers.
    If `table_name` is provided it is prepended as a heading.
    """
    if not rows:
        return ""
    
    lines: list[str] = []
    if table_name:
        lines.append(f"## {table_name}")
        lines.append("")

    if isinstance(rows[0], dict):
        headers: list[str] = list(rows[0].keys())
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            cells = [str(row.get(h, "")).strip().replace("\n", " ") for h in headers]
            lines.append("| " + " | ".join(cells) + " |")
    elif isinstance(rows[0], list):
        # Determine max columns
        max_cols = max(len(row) for row in rows if isinstance(row, list))
        # Create a generic header since Markdown tables require one
        headers = [f"Col {i+1}" for i in range(max_cols)]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            if not isinstance(row, list):
                continue
            cells = [str(cell).strip().replace("\n", " ") for cell in row]
            # Pad if necessary
            cells.extend([""] * (max_cols - len(cells)))
            lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def _format_price_table(price_table: Any) -> str | None:
    """Render the scraper's price_table (list of {Estrato, Valor}) as Markdown.

    Returns None if the input is unusable. Price values are echoed verbatim so the
    retriever can match on the exact formatting ("$2.500.000", "$ 1.800.000") that
    the LLM will later cite.
    """
    if not isinstance(price_table, list) or not price_table:
        return None
    lines: list[str] = []
    for entry in price_table:
        if not isinstance(entry, dict):
            continue
        # Keys from real scraper: "Estrato" / "Valor". Be forgiving with case.
        lowered = {str(k).lower(): v for k, v in entry.items()}
        estrato = lowered.get("estrato") or lowered.get("tier") or lowered.get("nivel")
        valor = lowered.get("valor") or lowered.get("price") or lowered.get("precio")
        if estrato is None and valor is None:
            continue
        estrato_txt = str(estrato).strip() if estrato is not None else "Sin estrato"
        valor_txt = str(valor).strip() if valor is not None else "Sin valor"
        lines.append(f"- Estrato {estrato_txt}: {valor_txt}")
    if not lines:
        return None
    return "\n".join(lines)


def _build_general_info_section(data: dict[str, Any]) -> str | None:
    """`## Información general` — program_title, faculty, modalidad, SNIES/summary."""
    program_title = _pick(data, ("program_title",))
    faculty = _pick(data, ("faculty", "facultad"))
    modalidad = _pick(data, ("modalidad", "modality", "modalidades"))
    summary = _pick(data, ("summary", "resumen"))

    bits: list[str] = []
    if isinstance(program_title, str) and program_title.strip():
        bits.append(f"- Título otorgado: {program_title.strip()}")
    if isinstance(faculty, str) and faculty.strip():
        bits.append(f"- Facultad: {faculty.strip()}")
    if isinstance(modalidad, str) and modalidad.strip():
        bits.append(f"- Modalidad: {modalidad.strip()}")
    if isinstance(summary, str) and summary.strip():
        # summary usually carries the SNIES + registro calificado blurb.
        bits.append(f"- Información oficial: {summary.strip()}")

    if not bits:
        return None
    return "## Información general\n" + "\n".join(bits)


def _build_inscriptions_section(data: dict[str, Any]) -> str | None:
    """`## Inscripciones` — inscription window + class start date."""
    inscriptions = _pick(data, ("inscriptions", "inscripciones"))
    class_start = _pick(data, ("class_start", "inicio_clases", "inicio_de_clases"))

    bits: list[str] = []
    if isinstance(inscriptions, str) and inscriptions.strip():
        bits.append(f"- Período de inscripciones: {inscriptions.strip()}")
    if isinstance(class_start, str) and class_start.strip():
        bits.append(f"- Inicio de clases: {class_start.strip()}")

    if not bits:
        return None
    return "## Inscripciones\n" + "\n".join(bits)


def _build_costs_section(data: dict[str, Any]) -> str | None:
    """`## Costos de matrícula por estrato` — rendered from the price_table array."""
    price_table = _pick(data, ("price_table", "precios", "costos"))
    rendered = _format_price_table(price_table)
    if not rendered:
        return None
    return "## Costos de matrícula por estrato\n" + rendered


def _build_posgrado_details_section(data: dict[str, Any]) -> str | None:
    """Build `## Detalles del programa` for posgrado-specific metadata.

    Covers fields exclusive to the posgrado scraper: cost, schedule, credits,
    snies, registro_calificado, vigencia, semesters.
    """
    cost = _pick(data, ("cost", "costo"))
    schedule = _pick(data, ("schedule", "horario"))
    credits_ = _pick(data, ("credits", "creditos", "créditos"))
    snies = _pick(data, ("snies",))
    registro = _pick(data, ("registro_calificado",))
    vigencia = _pick(data, ("vigencia",))
    semesters = _pick(data, ("semesters", "semestres", "duracion", "duración"))

    bits: list[str] = []
    if isinstance(semesters, str) and semesters.strip():
        bits.append(f"- Duración del programa: {semesters.strip()}")
    if isinstance(cost, str) and cost.strip():
        bits.append(f"- Costo por semestre: {cost.strip()}")
    if isinstance(schedule, str) and schedule.strip():
        bits.append(f"- Horario: {schedule.strip()}")
    if isinstance(credits_, (str, int)) and str(credits_).strip():
        bits.append(f"- Número de créditos académicos: {str(credits_).strip()}")
    if isinstance(snies, (str, int)) and str(snies).strip():
        bits.append(f"- Código SNIES: {str(snies).strip()}")
    if isinstance(registro, str) and registro.strip():
        bits.append(f"- Resolución de registro calificado: {registro.strip()}")
    if isinstance(vigencia, str) and vigencia.strip():
        bits.append(f"- Vigencia del registro: {vigencia.strip()}")

    if not bits:
        return None
    return "## Detalles del programa\n" + "\n".join(bits)


def _enrich_content_with_metadata(base_content: str | None, data: dict[str, Any]) -> str:
    """Compose a document from rich scraper metadata + optional narrative body.

    Output is deterministic Markdown that the hierarchical chunker can split cleanly:
      ## Información general  (program_title, faculty, modalidad, SNIES)
      ## Presentación         (the narrative body, when present)
      ## Inscripciones        (inscription window + class start)
      ## Costos de matrícula por estrato   (from price_table)

    If `base_content` already opens with a Markdown heading (e.g. doc already curated),
    it is placed under `## Presentación` only if it is plain prose; otherwise we keep it
    as-is to avoid double-wrapping.
    """
    sections: list[str] = []

    general = _build_general_info_section(data)
    if general:
        sections.append(general)

    if isinstance(base_content, str) and base_content.strip():
        stripped = base_content.strip()
        # If the scraper body already has its own headings, just append it raw so
        # the chunker sees the original hierarchy. Otherwise wrap as Presentación.
        if stripped.startswith("#"):
            sections.append(stripped)
        else:
            sections.append("## Presentación\n" + stripped)

    posgrado_details = _build_posgrado_details_section(data)
    if posgrado_details:
        sections.append(posgrado_details)

    inscriptions = _build_inscriptions_section(data)
    if inscriptions:
        sections.append(inscriptions)

    costs = _build_costs_section(data)
    if costs:
        sections.append(costs)

    return "\n\n".join(sections).strip()


def _has_rich_metadata(data: dict[str, Any]) -> bool:
    """True when any of the scraper's structured fields are present and non-empty."""
    return _pick(data, _METADATA_KEYS_FOR_RICH_CONTENT) is not None


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

    # Handle table content: list of dicts or list of lists from table scraper → Markdown text.
    if isinstance(content_raw, list) and content_raw and (isinstance(content_raw[0], dict) or isinstance(content_raw[0], list)):
        table_name = data.get("table_name") or data.get("title")
        content_raw = _render_table_content(content_raw, table_name)
        warnings.append("table content rendered as Markdown")

    has_narrative = isinstance(content_raw, str) and content_raw.strip()
    has_metadata = _has_rich_metadata(data)

    if not has_narrative and not has_metadata:
        return None, ["rejected: missing content"]

    # Step 1: turn the narrative body (if any) into clean plain/markdown text.
    narrative_clean: str | None = None
    if has_narrative:
        if _looks_like_html(content_raw):
            narrative_clean = _strip_html(content_raw)
            warnings.append("html stripped")
        else:
            narrative_clean = content_raw
        narrative_clean = _clean_text(narrative_clean)

    # Step 2: merge metadata sections around the narrative (always when metadata
    # is available, so costs/inscription dates are searchable even when the body
    # doesn't mention them).
    if has_metadata:
        content = _enrich_content_with_metadata(narrative_clean, data)
        content = _clean_text(content)
        if not has_narrative:
            warnings.append("content built from metadata (no presentation/body)")
        else:
            warnings.append("content enriched with scraper metadata")
    else:
        content = narrative_clean or ""

    if len(content) < _MIN_CONTENT_LEN:
        return None, [f"rejected: content too short ({len(content)} chars)"]

    category_raw = _pick(data, _ALIASES_CATEGORY)
    category: str | None = None
    if isinstance(category_raw, str) and category_raw.strip():
        canonical = _canonicalize_category(category_raw)
        if canonical:
            category = canonical
            if canonical != category_raw.strip().lower():
                warnings.append(f"category canonicalized: '{category_raw}' → '{canonical}'")
        else:
            # Unknown literal — try inference, but keep the raw as a last resort so we
            # don't lose information.
            category = _infer_category(url, title, content)
            warnings.append(
                f"category '{category_raw}' not in canonical map; inferred as '{category}'"
            )
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
