#!/usr/bin/env bash
# Entrypoint para Hugging Face Spaces (SDK: docker).
#
# Variables de entorno relevantes (configúralas como Secrets en el Space):
#   DATABASE_URL         — Supabase Session pooler (recomendado) o cualquier
#                          Postgres+pgvector externo. OBLIGATORIO. Formato:
#                          postgresql+psycopg://postgres.<REF>:<PASS>@aws-0-<REGION>.pooler.supabase.com:5432/postgres?sslmode=require
#   GROQ_API_KEY         — (u otro proveedor LLM). OBLIGATORIO al menos uno.
#   INIT_DB_ON_START     — "true" (default) aplica scripts/init_db.sql al arrancar.
#   INGEST_ON_START      — "true" corre el ingest con $INGEST_PATH. Default "false".
#   INGEST_PATH          — Ruta dentro del contenedor con los JSON. Default scripts/sample_data.
#   PORT                 — Puerto HTTP. HF Spaces lo fija en 7860.

set -euo pipefail

: "${PORT:=7860}"
: "${INIT_DB_ON_START:=true}"
: "${INGEST_ON_START:=false}"
: "${INGEST_PATH:=scripts/sample_data}"

if [ -z "${DATABASE_URL:-}" ]; then
  echo "[entrypoint] ERROR: DATABASE_URL no está configurado."
  echo "[entrypoint] Añade un Secret 'DATABASE_URL' apuntando a tu Supabase Session pooler."
  echo "[entrypoint] Formato (ver README → Desplegar en HF Spaces + Supabase):"
  echo "[entrypoint]   postgresql+psycopg://postgres.<REF>:<PASS>@aws-0-<REGION>.pooler.supabase.com:5432/postgres?sslmode=require"
  echo "[entrypoint] Recuerda activar pgvector en Database → Extensions antes del primer arranque."
  exit 1
fi

if [ "${INIT_DB_ON_START}" = "true" ]; then
  echo "[entrypoint] Aplicando schema (scripts/init_db.sql)…"
  python - <<'PY'
from pathlib import Path
from sqlalchemy import text
from app.db import get_engine

sql = Path("scripts/init_db.sql").read_text(encoding="utf-8")
engine = get_engine()
with engine.begin() as conn:
    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
        conn.execute(text(stmt))
print("[entrypoint] Schema listo.")
PY
fi

if [ "${INGEST_ON_START}" = "true" ]; then
  if [ -e "${INGEST_PATH}" ]; then
    echo "[entrypoint] Ejecutando ingest desde ${INGEST_PATH}…"
    python scripts/ingest.py "${INGEST_PATH}"
  else
    echo "[entrypoint] INGEST_ON_START=true pero ${INGEST_PATH} no existe. Se omite."
  fi
fi

echo "[entrypoint] Arrancando uvicorn en 0.0.0.0:${PORT}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
