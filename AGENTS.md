# AGENTS.md — Guía operativa para agentes de IA

Documento estructurado para que un agente (Claude Code, Cursor, Copilot, etc.) entienda este repositorio rápidamente y pueda modificarlo de forma segura. Léelo entero antes de editar.

---

## 1. Propósito del sistema

**BravoBot** es un backend RAG en Python para un asistente universitario institucional. Responde preguntas de aspirantes usando únicamente documentos scrapeados de `pascualbravo.edu.co`.

### Invariantes no negociables

| # | Invariante | Dónde se enforza |
|---|---|---|
| I1 | El bot **nunca** debe inventar datos. Si el contexto no cubre la pregunta → respuesta de fallback institucional | `app/rag/prompts.py:SYSTEM_PROMPT` + `app/rag/confidence.py` |
| I2 | Solo se indexan URLs de `pascualbravo.edu.co` (incluye subdominios) | `app/ingest/normalizer.py:_is_allowed_url` |
| I3 | Cada chunk pertenece a **una sola sección semántica** — nunca fusionar secciones distintas | `app/rag/chunker.py:chunk_document` |
| I4 | Cada respuesta del LLM debe citar con `[N]` los fragmentos usados | `SYSTEM_PROMPT` + `app/main.py:_extract_used_citations` |
| I5 | `scripts/ingest.py` debe ser **idempotente** por `source_hash` | `scripts/ingest.py:_upsert_chunks` |
| I6 | El gate de confianza usa **múltiples señales**, nunca un solo umbral | `app/rag/confidence.py:evaluate_confidence` |

Si una modificación rompe una invariante, **detente y pregunta al usuario**.

---

## 2. Mapa del repositorio

```
datahack/
├── app/
│   ├── main.py              ← FastAPI entrypoint (endpoints /chat, /sessions, /health)
│   ├── config.py            ← Settings (pydantic-settings, lee .env)
│   ├── db.py                ← SQLAlchemy engine + db_session() context manager
│   ├── models.py            ← Pydantic request/response models del API
│   ├── sessions.py          ← CRUD de sessions + session_turns (memoria conversacional)
│   ├── ingest/
│   │   ├── schemas.py       ← NormalizedDocument (contrato interno)
│   │   └── normalizer.py    ← JSON crudo → NormalizedDocument limpio
│   └── rag/
│       ├── chunker.py       ← Partición jerárquica por headings
│       ├── embedder.py      ← sentence-transformers (multilingual-e5-large)
│       ├── retriever.py     ← Hybrid search (vector + tsvector) + RRF
│       ├── confidence.py    ← Gate multi-señal
│       ├── reformulator.py  ← Reescribe preguntas de seguimiento como standalone
│       ├── generator.py     ← MultiProviderLLM (Groq→Cerebras→Anthropic)
│       └── prompts.py       ← SYSTEM_PROMPT, FALLBACK_ANSWER, format_context()
├── scripts/
│   ├── init_db.sql          ← Schema de Postgres (extensions, tables, indexes)
│   ├── ingest.py            ← CLI: carga JSON → chunks → embeddings → DB
│   └── sample_data/         ← Fixtures JSON (incluye casos sucios intencionalmente)
├── tests/                   ← pytest (36 tests, unit-only, no requieren DB ni LLM)
├── docker-compose.yml       ← Postgres 16 + pgvector
├── pyproject.toml           ← Dependencias y config de pytest/ruff
├── .env.example             ← Plantilla de variables
└── README.md                ← Documentación orientada a humanos
```

---

## 3. Flujo de datos

### 3.1 Ingesta (offline)

```
JSON del scraper
  → normalize_many()               app/ingest/normalizer.py
    aliases + domain check + HTML strip + category inference + dedupe
  → chunk_document()               app/rag/chunker.py
    split by headings, then by paragraphs with overlap
  → embed_passages()               app/rag/embedder.py
    prefix "passage: " + e5-large + normalize
  → _upsert_chunks()               scripts/ingest.py
    UPSERT ON CONFLICT (url, chunk_index)
```

### 3.2 Consulta (online, `POST /chat`)

```
ChatRequest
  → get_or_create_session()        app/sessions.py
  → load_history()
  → reformulate(question, history) app/rag/reformulator.py   # standalone question
  → retrieve(sess, query)          app/rag/retriever.py
    vector_search + lexical_search → RRF fusion → top_k
  → evaluate_confidence()          app/rag/confidence.py
    5 signals → passed?
       NO  → FALLBACK_ANSWER (no LLM call)
       YES → build_user_message() + MultiProviderLLM.complete()
  → _extract_used_citations()      app/main.py
  → append_turn(user) + append_turn(assistant)
  → ChatResponse
```

---

## 4. Esquema de base de datos

Tablas definidas en `scripts/init_db.sql`:

```sql
documents(
  id BIGSERIAL PK,
  url TEXT,                          -- UNIQUE con chunk_index
  title TEXT,
  category TEXT,                     -- pregrado|posgrado|admisiones|costos|perfiles|beneficios|otros
  section_title TEXT,
  heading_path TEXT,                 -- "Título > Sección > Subsección"
  chunk_index INT,
  content TEXT,
  content_tsv TSVECTOR GENERATED,    -- spanish dictionary, GIN index
  embedding VECTOR(1024),            -- multilingual-e5-large, HNSW cosine index
  source_hash TEXT,                  -- idempotency
  created_at TIMESTAMPTZ
)

sessions(id UUID PK, created_at, last_active)
session_turns(id, session_id FK, role IN ('user','assistant'), content, created_at)
```

**Si cambias la dimensión del modelo de embeddings**, debes editar:
1. `EMBEDDING_DIM` en `.env` / `config.py`
2. `VECTOR(1024)` en `scripts/init_db.sql`
3. Re-crear la tabla `documents` (o hacer migración con `ALTER`)

---

## 5. Variables de entorno

Fuente de verdad: `.env.example`. Leídas por `app/config.py:Settings`.

| Variable | Default | Uso |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://bravobot:bravobot@localhost:5433/bravobot` | SQLAlchemy connection string (puerto 5433 para evitar conflicto con Postgres local) |
| `GROQ_API_KEY` | — | Principal. Requerido al menos un provider. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | |
| `CEREBRAS_API_KEY` | — | Fallback 1 |
| `CEREBRAS_MODEL` | `llama-3.3-70b` | |
| `ANTHROPIC_API_KEY` | — | Fallback 2 |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5` | |
| `LLM_PROVIDER_ORDER` | `groq,cerebras,anthropic` | Orden de intentos |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-large` | |
| `EMBEDDING_DIM` | `1024` | Debe coincidir con el modelo y con init_db.sql |
| `RETRIEVAL_TOP_K` | `5` | Chunks enviados al LLM |
| `RETRIEVAL_CANDIDATES` | `10` | Candidatos por canal antes de RRF |
| `CONFIDENCE_*` | ver `.env.example` | Umbrales del gate (todos tuneables sin tocar código) |
| `SESSION_HISTORY_TURNS` | `6` | Turnos que se cargan para reformulación |
| `CORS_ORIGINS` | `*` | Para el frontend |

---

## 6. Comandos canónicos

```bash
# Setup desde cero
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
cp .env.example .env   # editar con tu key

# Scraper (genera JSON procesados en data/processed desde data/mappings.json)
python scripts/scraper.py

# DB (puerto 5433 en host para evitar conflicto con Postgres local)
docker compose up -d
docker compose down    # stop
docker compose down -v # wipe volume (reset completo)

# Ingesta
python scripts/ingest.py scripts/sample_data/    # smoke test
python scripts/ingest.py data/processed/               # datos reales
python scripts/ingest.py --verbose data/processed/     # logs por documento

# API
uvicorn app.main:app --reload --port 8000

# Tests (no necesitan DB ni LLM)
pytest -q
pytest tests/test_confidence.py -v               # módulo específico
```

> **Nota**: El contenedor Docker mapea el puerto **5433** del host al 5432 interno.
> Si tienes PostgreSQL instalado localmente en el puerto 5432, esto evita conflictos.
> Asegúrate de que `DATABASE_URL` en `.env` use el puerto `5433`.

---

## 7. Guías para modificar cada módulo

### 7.1 `app/ingest/normalizer.py`

- **Antes de editar**: entiende que este módulo recibe JSON arbitrario del tercero. Es **tolerante** (`RawDocument` permite extras) pero **estricto** en salida (`NormalizedDocument` es canónico).
- **Para añadir un alias de campo**: edítalo en las constantes `_ALIASES_*`. Los aliases se comparan case-insensitive.
- **Para añadir una categoría**: agrégala a `_URL_CATEGORY_PATTERNS` o `_KEYWORD_CATEGORY_PATTERNS`. El orden importa (primer match gana).
- **Categorías canónicas**: `_CATEGORY_CANONICAL` mapea plurales/variantes del scraper (`pregrados`, `especialización`, `matrícula`…) al set canónico. Si añades una nueva etiqueta que pueda venir del scraper, agrégala al dict.
- **Metadata enriquecida**: cuando el scraper entrega campos como `faculty`, `modalidad`, `program_title`, `inscriptions`, `class_start`, `price_table`, `summary`, el normalizador construye secciones Markdown deterministas (`## Información general`, `## Presentación`, `## Inscripciones`, `## Costos de matrícula por estrato`). Esto permite que el chunker jerárquico las separe y que el retriever las encuentre sin tocar código extra.
- **Documentos sin presentación**: si `presentation`/`content` viene vacío pero hay metadata rica, se construye el documento solo a partir de la metadata (ver test `test_missing_presentation_but_rich_metadata_builds_content`). No lo rechaces — ese caso existe en el dump real (ej. *Tecnología en Producción Industrial*).
- **No bypassees `_is_allowed_url`** — es la defensa de dominio (I2).
- Casos sucios de prueba: `scripts/sample_data/dirty_cases.json`; ejemplos del scraper real: `scripts/sample_data/real_scraper_pregrados.json`.

### 7.2 `app/rag/chunker.py`

- **No mezcles secciones** (I3). El chunker parte primero por heading (`##`, ALL CAPS, `Label:`) y solo luego aplica límite de tamaño **dentro** de cada sección.
- `MAX_TOKENS=800`, `OVERLAP_TOKENS=100`, `MIN_CHUNK_TOKENS=5` son constantes del módulo. Cambios requieren re-ingesta completa.
- Cada chunk debe tener `section_title` y `heading_path` poblados (afectan la citación).

### 7.3 `app/rag/retriever.py`

- Retorna `list[RetrievedChunk]` ordenado por RRF score (mejor primero).
- `similarity` (cosine) viene **solo** del canal denso. Si un doc aparece solo por canal léxico, `similarity=0.0` — el gate de confianza lo tiene en cuenta.
- Para cambiar el fusor: sustituye `_rrf_fuse`. No toques el SQL del canal léxico sin validar que `plainto_tsquery('spanish', ...)` siga aplicando.

### 7.4 `app/rag/confidence.py`

- **La interfaz pública es `evaluate_confidence(question, chunks) -> ConfidenceResult`**. Los tests dependen de ella.
- Añadir una señal nueva:
  1. Computa el valor.
  2. Agrégala a `signals` (dict bool) y a `details` (dict float/str).
  3. Ajusta `confidence_signals_required` si cambia el total.
  4. Añade un test en `tests/test_confidence.py`.
- No subas `confidence_catastrophic_min` sin justificación — es el last-resort contra matches completamente irrelevantes.

### 7.5 `app/rag/generator.py`

- `MultiProviderLLM.complete()` ya maneja fallback. Si una llamada falla, el siguiente provider lo intenta.
- Para añadir un provider nuevo: implementa una clase con `.complete(system, user, *, temperature, max_tokens) -> str` y regístrala en `_build_client`.
- **No metas lógica RAG aquí** — este módulo es un cliente LLM puro.

### 7.6 `app/rag/prompts.py`

- **Cambios al `SYSTEM_PROMPT` deben preservar las 7 reglas anti-alucinación** (I1, I4). Revisa la lista antes de reescribir.
- `FALLBACK_ANSWER` es devuelto verbatim cuando el gate falla — debe ser autosuficiente y contener redirección al sitio oficial.

### 7.7 `app/main.py`

- Endpoints: `/chat`, `/sessions`, `/health`. Si añades uno nuevo, registra un modelo Pydantic en `app/models.py`.
- El pipeline completo vive en la función `chat()`. Si refactorizas, mantén el orden: sesión → reformulación → retrieval → gate → generación → citaciones → append turns.

### 7.8 `tests/`

- **Los tests actuales son unit-only**: no requieren DB, LLM ni embeddings (sentence-transformers se lazy-importa).
- Para mantener esta propiedad: no escribas tests que hagan `embed_passages()` real ni llamadas a `retrieve()` contra Postgres. Si necesitas integración, créalos en `tests/integration/` y márcalos con `@pytest.mark.integration` (pendiente de configurar).

---

## 8. Anti-patrones (no hagas esto)

- ❌ Llamar al LLM antes del gate de confianza (rompe anti-alucinación).
- ❌ Indexar documentos sin pasar por `normalize_many` (bypassea validación de dominio).
- ❌ Hacer chunking por tamaño fijo sin respetar headings (rompe I3).
- ❌ Añadir lógica de negocio en `generator.py` (es solo cliente LLM).
- ❌ Modificar `source_hash` sin invalidar chunks previos (rompe idempotencia).
- ❌ Usar `f-strings` para construir SQL (usa `text()` + parámetros — ya está así en todo el repo).
- ❌ Leer `.env` directamente con `os.environ` — usa `get_settings()`.
- ❌ Crear dependencias circulares: `config` y `db` son hojas; `rag/*` puede importar `ingest/*` pero no al revés; `main` importa todo.

---

## 9. Cómo añadir funcionalidades comunes

### Añadir un nuevo tipo de pregunta soportada

Si el jurado/usuario quiere que responda sobre algo nuevo (ej. horarios, ubicación de campus):

1. Verifica que la info esté en el scrape (si no, pídele al equipo de scraping).
2. Añade una categoría en `_URL_CATEGORY_PATTERNS` / `_KEYWORD_CATEGORY_PATTERNS` si aplica.
3. Re-ingestar: `python scripts/ingest.py data/raw/`.
4. Prueba con curl o `/docs`.

### Subir/bajar la sensibilidad del gate

No modifiques código — ajusta `.env`:

- Más permisivo (más respuestas, más riesgo): baja `CONFIDENCE_TOP1_MIN`, `CONFIDENCE_KEYWORD_COVERAGE_MIN`, o `CONFIDENCE_SIGNALS_REQUIRED` (mínimo 2).
- Más estricto: sube los mismos umbrales. Si sube `CONFIDENCE_SIGNALS_REQUIRED` a 4 o 5, habrá muchos fallbacks.

### Cambiar el modelo de embeddings

1. Edita `EMBEDDING_MODEL` y `EMBEDDING_DIM` en `.env`.
2. Edita `VECTOR(1024)` → `VECTOR(<nueva_dim>)` en `scripts/init_db.sql`.
3. `docker compose down -v && docker compose up -d` para recrear la tabla.
4. Re-ingestar todo.

### Añadir un endpoint nuevo

1. Modelo en `app/models.py`.
2. Handler en `app/main.py` usando `db_session()`.
3. Test en `tests/` (stub de DB si es necesario).

---

## 10. Checklist antes de hacer commit

- [ ] `pytest -q` pasa (41/41 mínimo, más si añadiste tests).
- [ ] `python -c "from app.main import app"` no lanza.
- [ ] No hay API keys hardcodeadas (solo en `.env`, nunca en código).
- [ ] Si añadiste dependencia: está en `pyproject.toml`.
- [ ] Si tocaste `init_db.sql`: documentaste si requiere reset de volumen.
- [ ] Si tocaste el `SYSTEM_PROMPT`: validaste que aún dice el fallback exacto cuando no hay contexto.

---

## 11. Contacto con el sistema vivo

- **Swagger UI**: `http://localhost:8000/docs` (interactivo).
- **Health**: `curl http://localhost:8000/health`.
- **Logs**: stdout de uvicorn. El gate de confianza loguea las señales en cada `/chat`.
- **Inspección de DB**:
  ```bash
  docker exec -it bravobot-postgres psql -U bravobot -d bravobot
  SELECT category, count(*) FROM documents GROUP BY category;
  SELECT url, chunk_index, section_title FROM documents ORDER BY url, chunk_index LIMIT 20;
  ```
