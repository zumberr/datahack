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
    content_tsv TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('spanish', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('spanish', coalesce(category, '')), 'B') ||
        setweight(to_tsvector('spanish', coalesce(section_title, '')), 'B') ||
        setweight(to_tsvector('spanish', content), 'C')
    ) STORED,
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

-- Per-assistant-turn metadata: what was retrieved, what the gate said, which
-- reformulated query we searched with. Needed so feedback analysis can
-- attribute user complaints to specific retrieval/gate/prompt decisions.
CREATE TABLE IF NOT EXISTS turn_metadata (
    turn_id BIGINT PRIMARY KEY REFERENCES session_turns(id) ON DELETE CASCADE,
    search_query TEXT,
    retrieved_ids BIGINT[],
    retrieved_urls TEXT[],
    confident BOOLEAN NOT NULL,
    confidence_score REAL,
    signals JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- User feedback on individual assistant turns. rating drives the triage in
-- scripts/analyze_feedback.py: `missing_info` → corpus gaps,
-- `wrong` → hallucination candidates, `not_helpful`/`incomplete` → retrieval
-- or prompt tuning.
CREATE TABLE IF NOT EXISTS feedback (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id BIGINT REFERENCES session_turns(id) ON DELETE SET NULL,
    rating TEXT NOT NULL CHECK (rating IN (
        'helpful', 'not_helpful', 'wrong', 'incomplete', 'missing_info'
    )),
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS feedback_rating_idx
    ON feedback (rating, created_at DESC);
CREATE INDEX IF NOT EXISTS feedback_turn_idx
    ON feedback (turn_id);
