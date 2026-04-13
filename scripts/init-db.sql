-- Cortex / DS-Agents — schema inicial
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS projects (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    config      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'active',
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    final_state  JSONB
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id);

CREATE TABLE IF NOT EXISTS tasks (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id         UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    agent_name     TEXT NOT NULL,
    phase          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    input          JSONB,
    output         JSONB,
    started_at     TIMESTAMPTZ,
    finished_at    TIMESTAMPTZ,
    tokens_used    INTEGER DEFAULT 0,
    cost_estimate  NUMERIC(12,6) DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id);

CREATE TABLE IF NOT EXISTS artifacts (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id        UUID REFERENCES tasks(id) ON DELETE SET NULL,
    project_id     UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    artifact_type  TEXT NOT NULL,
    minio_path     TEXT NOT NULL,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_artifacts_project ON artifacts(project_id);

CREATE TABLE IF NOT EXISTS human_decisions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id      UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    task_id     UUID REFERENCES tasks(id) ON DELETE SET NULL,
    decision    TEXT NOT NULL,
    comments    TEXT,
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_logs (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id      UUID REFERENCES tasks(id) ON DELETE CASCADE,
    agent_name   TEXT NOT NULL,
    prompt_hash  TEXT,
    model_used   TEXT,
    tokens_in    INTEGER DEFAULT 0,
    tokens_out   INTEGER DEFAULT 0,
    latency_ms   INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_logs_task ON agent_logs(task_id);

-- Tabela para memória semântica futura (pgvector já instalado)
CREATE TABLE IF NOT EXISTS memory_embeddings (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    embedding   vector(1536),
    metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
