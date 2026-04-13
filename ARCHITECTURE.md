# Architecture

Deep technical specification for Cortex. For a high-level overview, installation and usage, see [README.md](./README.md).

---

## 1. Design principles

1. **Run-anywhere via Docker.** The same `docker compose up` works on a developer Mac (arm64, Docker Desktop) and on a Linux server (amd64, Docker Engine). No platform-specific paths, no host bind mounts for data flow, multi-arch images only.
2. **Client data never leaves local volumes.** Datasets, models and reports live in Postgres/MinIO volumes. `.env` is gitignored; each environment holds its own secrets.
3. **Human-in-the-loop at risky steps.** The plan and the final training step pause for explicit human approval.
4. **Independent review.** The Reviewer agent runs with an adversarial prompt and never shares context with the agent that produced the work.
5. **Sandboxed execution.** All generated code runs in an ephemeral, network-less, capability-dropped container with hard mem/cpu/time caps.

---

## 2. Container topology

```
docker-compose.yml
├── agent-core   FastAPI + LangGraph; mounts /var/run/docker.sock to spawn sandboxes
├── postgres     PostgreSQL 16 + pgvector (state, checkpoints, future embeddings)
├── minio        S3-compatible object storage
├── kestra       workflow orchestrator (scheduling, retries, ops)
├── nginx        reverse proxy → /api/, /kestra/
└── sandbox      built with profile "tools"; spawned on-demand, not long-running
```

The `sandbox` service exists in the compose file only so the image is built alongside the stack. It is never `up`-ed — `agent-core` spawns instances of it per execution and removes them when done.

### Sandbox handoff (portability-critical)

Because `agent-core` talks to the host Docker daemon, any bind mount it declares is interpreted in the **host** filesystem, not in `agent-core`'s own filesystem. To avoid platform-specific paths:

- A named volume `cortex_sandbox_tmp` is declared with a fixed external name.
- `agent-core` mounts it at `/tmp/sandbox` and writes each run's workspace under `/tmp/sandbox/<run_id>/`.
- When spawning the sandbox, the same named volume is mounted at `/sandbox_root`, and `working_dir=/sandbox_root/<run_id>` points the container to its workspace.

This works identically on Mac and Linux because named volumes are resolved by the daemon, not by paths.

---

## 3. Agents

Each agent is defined by a system prompt + a Python entry function. No shared state across agents — communication goes through the graph state, which acts as the single source of truth for the run.

### 3.1 Orchestrator (Project Manager)

- **Role:** reads the brief, produces a staged plan, decides which agent runs next, compiles the final report.
- **Actions:** `plan`, `decide_next`, `compile_report`.
- **Model:** Claude Opus (complex reasoning).
- **Human checkpoints:** initial plan approval.

### 3.2 Data Analyst

- **Role:** EDA, data quality alerts, hypotheses, descriptive statistics, visualizations.
- **Execution:** generates Python code → runs in sandbox → reads `outputs/summary.json` and plot files.
- **Model:** Claude Sonnet.

### 3.3 Modeler

- **Role:** feature engineering, baselines, model comparison, selection.
- **Execution:** generates Python code → runs in sandbox → reads `outputs/metrics.json`, `leaderboard.csv`, `model.pkl`.
- **Model:** Claude Opus.
- **Human checkpoints:** before final/large-scale training.

### 3.4 Reviewer (Critic)

- **Role:** audits everything produced so far. Adversarial prompt focused on data leakage, improper splits, metric misuse, bias, unsupported claims.
- **Execution:** no code, JSON verdict only (`approved` or `rejected` with structured issues list).
- **Model:** Claude Opus.
- **Loop guard:** on `rejected`, control flows back to Modeler; after `MAX_REVIEW_LOOPS` (default 2) the run is forced to report to avoid infinite loops.

---

## 4. LangGraph graph

```
START ─► plan ─► eda ─► decide_next ─► modeling ─► review ─┬─► report ─► END
                                              ▲            │
                                              └── rejected ┘
```

### 4.1 State

Defined in `agent-core/src/graph/state.py` as a `TypedDict`. Key fields:

| Field | Purpose |
|---|---|
| `project_id`, `run_id` | Identity |
| `description`, `datasets` | Input |
| `plan` | Output of the Orchestrator's `plan` action |
| `current_phase` | `planning` / `eda` / `modeling` / `review` / `reporting` / `done` |
| `eda_results`, `model_results`, `review_results`, `final_report` | Per-stage artifacts |
| `human_decisions` | Approval/rejection log |
| `status` | `active` / `waiting_human` / `completed` / `failed` |
| `review_loop_count` | Bounded retry counter |

### 4.2 Interruptions

`interrupt_after=["plan", "modeling"]` — the graph pauses after those nodes and is resumed via the `POST /v1/decisions` endpoint. State is persisted by a Postgres checkpointer keyed on `thread_id = run_id`, so a resumed run picks up exactly where it was paused, even after a container restart.

---

## 5. Sandbox execution contract

Each sandbox invocation receives a workspace directory (named-volume subpath) containing:

```
<workspace>/
├── script.py | script.R | notebook.ipynb    # generated code
├── inputs/                                   # input data
├── outputs/                                  # populated by the script
└── meta.json                                 # optional metadata
```

The entrypoint runs the appropriate interpreter from the current working directory. Scripts are expected to write structured outputs the agent layer can consume:

- Analyst → `outputs/summary.json`, plot files.
- Modeler → `outputs/metrics.json`, `outputs/leaderboard.csv`, `outputs/model.pkl`.

### Hard limits (all enforceable via Docker SDK)

| Control | Default |
|---|---|
| Network | `none` |
| Memory | 2 GB |
| CPU | 2 cores |
| PIDs | 256 |
| Capabilities | all dropped |
| Privileges | `no-new-privileges:true` |
| Timeout | 300 s |

---

## 6. Database schema

`scripts/init-db.sql` creates the schema on first boot. Tables:

| Table | Purpose |
|---|---|
| `projects` | One row per DS project. Config stored as JSONB. |
| `runs` | One row per graph execution. |
| `tasks` | One row per agent call within a run. Stores I/O, tokens, cost estimate. |
| `artifacts` | Pointers to objects in MinIO (datasets, models, reports, plots). |
| `human_decisions` | Approval/rejection log, joined to runs/tasks. |
| `agent_logs` | Per-LLM-call telemetry (model, tokens in/out, latency). |
| `memory_embeddings` | `vector(1536)` column prepared for semantic memory (not yet populated). |

LangGraph's own checkpoint tables are created at startup by `PostgresSaver.setup()` in the same database.

---

## 7. API surface

Base path: `/v1` (proxied under `/api/` by Nginx).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/projects` | Create a project |
| `GET` | `/projects/{id}` | Fetch project |
| `POST` | `/runs` | Start a run (runs the graph in a FastAPI BackgroundTask) |
| `GET` | `/runs/{id}` | Fetch DB row |
| `GET` | `/runs/{id}/state` | Live state snapshot from the checkpointer |
| `POST` | `/decisions` | Record approval/rejection; resumes the run |
| `GET` | `/health` | Liveness |

---

## 8. Environment variables

Populate `.env` from `.env.example`. Non-obvious entries:

| Variable | Notes |
|---|---|
| `CLAUDE_MODEL_ROUTINE` / `CLAUDE_MODEL_COMPLEX` | Split routine vs. high-stakes calls between Sonnet and Opus. |
| `SANDBOX_SHARED_VOLUME` | Named Docker volume shared by agent-core and sandboxes. Do not change casually. |
| `SANDBOX_SHARED_MOUNT` | Where that volume is mounted inside agent-core (`/tmp/sandbox`). |
| `SANDBOX_TIMEOUT_SECONDS` / `SANDBOX_MEMORY_LIMIT` / `SANDBOX_CPU_LIMIT` | Per-execution hard caps. |

---

## 9. Operations

### Development loop (Mac)

```
docker compose --profile tools build sandbox   # once
docker compose up -d --build
docker compose logs -f agent-core
```

### Production update

```
git pull
docker compose up -d --build
```

### Backups

`scripts/backup.sh` is a placeholder for `pg_dump` + MinIO sync against a mounted backup target. Intended to run from cron on the host.

### Cost envelope (early)

- Infra: DigitalOcean droplet ~US$48/mo.
- Claude API: ~US$30–80/mo for two active projects.
- Total: ~US$80–130/mo.

---

## 10. Roadmap

Deliberately out of scope for v1; revisit when there is real usage:

- Populate `memory_embeddings` from prior runs and wire semantic retrieval.
- Richer report templates (Markdown/HTML/PDF).
- Web UI (Dify, Open WebUI, or custom React).
- Messaging integrations (Telegram, WhatsApp).
- Additional specialized agents (e.g. causal inference, time series).
- Observability (LangSmith or similar).
- Local open-source models as a fallback.
