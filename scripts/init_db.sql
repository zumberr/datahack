-- BravoBot schema
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT,
    section_title TEXT,
    heading_path TEXT,
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED,
    embedding VECTOR(1024) NOT NULL,
    source_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (url, chunk_index)
);

CREATE INDEX IF NOT EXISTS documents_embedding_idx
    ON documents USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS documents_tsv_idx
    ON documents USING gin (content_tsv);

CREATE INDEX IF NOT EXISTS documents_category_idx
    ON documents (category);

CREATE INDEX IF NOT EXISTS documents_url_idx
    ON documents (url);

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS session_turns (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS session_turns_session_idx
    ON session_turns (session_id, created_at);
