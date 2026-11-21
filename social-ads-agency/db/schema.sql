-- Layer 5: durable memory. PostgreSQL 15+ with the pgvector extension.
-- Redis holds hot conversation state; this holds the record of truth.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS clients (
    client_key   TEXT PRIMARY KEY,
    business_name TEXT,
    stage        TEXT NOT NULL,
    state        JSONB NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quotes (
    id           TEXT PRIMARY KEY,
    client_key   TEXT NOT NULL REFERENCES clients(client_key),
    package      TEXT NOT NULL,
    currency     TEXT NOT NULL,
    total_minor  BIGINT NOT NULL,
    data         JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS campaigns (
    id           TEXT PRIMARY KEY,
    client_key   TEXT NOT NULL REFERENCES clients(client_key),
    platform     TEXT NOT NULL,
    status       TEXT NOT NULL,
    data         JSONB NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS campaigns_status_idx ON campaigns (status);

CREATE TABLE IF NOT EXISTS approvals (
    id           TEXT PRIMARY KEY,
    campaign_id  TEXT NOT NULL REFERENCES campaigns(id),
    status       TEXT NOT NULL,
    decided_by   TEXT,
    data         JSONB NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only audit trail: every payment, approval, launch and kill-switch pause.
CREATE TABLE IF NOT EXISTS events (
    id           BIGSERIAL PRIMARY KEY,
    client_key   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    data         JSONB NOT NULL,
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_client_idx ON events (client_key, at DESC);

-- The Brand Brain: facts, tone, offers, objections, past winners. 1024 dims = voyage-3.5.
CREATE TABLE IF NOT EXISTS brand_memories (
    id           BIGSERIAL PRIMARY KEY,
    client_key   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    text         TEXT NOT NULL,
    embedding    vector(1024) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS brand_memories_client_idx ON brand_memories (client_key);
CREATE INDEX IF NOT EXISTS brand_memories_hnsw ON brand_memories USING hnsw (embedding vector_cosine_ops);
