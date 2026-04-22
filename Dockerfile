# BravoBot — imagen para Hugging Face Spaces (SDK: docker)
# HF Spaces ejecuta el contenedor como UID 1000 y expone $PORT (por defecto 7860).
# Requiere una base de datos Postgres con pgvector externa (se recomienda **Supabase**
# con el Session pooler en el puerto 5432) configurada vía el secret DATABASE_URL.
# Formato esperado:
#   postgresql+psycopg://postgres.<REF>:<PASS>@aws-0-<REGION>.pooler.supabase.com:5432/postgres?sslmode=require

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependencias de sistema mínimas para psycopg/torch/sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq5 \
    curl \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# HF Spaces monta el código del Space en /home/user/app con UID 1000
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface \
    TRANSFORMERS_CACHE=/home/user/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/home/user/.cache/huggingface/sentence-transformers

WORKDIR /home/user/app

# Instalar dependencias primero para aprovechar la caché de capas
COPY --chown=user:user pyproject.toml ./
RUN pip install --user --upgrade pip \
 && pip install --user \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.32" \
    "pydantic>=2.9" \
    "pydantic-settings>=2.6" \
    "sqlalchemy>=2.0" \
    "psycopg[binary]>=3.2" \
    "pgvector>=0.3.6" \
    "sentence-transformers>=3.3" \
    "torch>=2.5" \
    "numpy>=1.26" \
    "groq>=0.13" \
    "cerebras_cloud_sdk>=1.19" \
    "anthropic>=0.39" \
    "selectolax>=0.3.25" \
    "python-dotenv>=1.0" \
    "httpx>=0.27" \
    "requests>=2.31" \
    "beautifulsoup4>=4.12" \
    "pdfplumber>=0.11.4"

# Copiar el resto del proyecto
COPY --chown=user:user . .

# HF Spaces expone el servicio en $PORT (7860 por defecto)
ENV PORT=7860
EXPOSE 7860

# El entrypoint:
#   1. Crea el schema si INIT_DB_ON_START=true (por defecto true)
#   2. Ingesta datos si INGEST_ON_START=true
#   3. Arranca uvicorn en $PORT
CMD ["bash", "scripts/hf_spaces_entrypoint.sh"]
