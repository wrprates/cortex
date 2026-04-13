# Cortex

> Multi-agent data science system. A virtual DS team, orchestrated by LLMs, that plans, executes code, validates results and produces reports — with human-in-the-loop at critical checkpoints.

Cortex receives a problem brief plus data, delegates to specialized agents (Analyst, Modeler, Reviewer) coordinated by an Orchestrator, executes generated code in a locked-down sandbox, and delivers a final report. The whole stack runs via `docker compose up` — same setup on a MacBook for development and on any Linux server for production.

---

## Why this exists

Running a DS project end-to-end involves a lot of mechanical work: EDA, baseline models, leakage checks, metric comparison, writeups. Cortex automates that loop while keeping a human approver in the critical decisions. It is built to run **real client projects**, not as an experiment — data stays in local volumes (Postgres/MinIO), never on GitHub.

---

## How it works

```
 briefing ──► [Orchestrator] ──► plan ──► ⏸ HUMAN APPROVAL
                                            │
                  ┌─────────────────────────┘
                  ▼
            [Data Analyst] ── EDA ──► artifacts (MinIO)
                  │
                  ▼
            [Orchestrator] ── decides next step
                  │
                  ▼
            [Modeler] ── features + training ──► ⏸ HUMAN APPROVAL
                  │
                  ▼
            [Reviewer] ── audits for leakage, bad splits, bias
                  │
         ┌────────┴────────┐
       approved        rejected → back to Modeler (bounded loop)
         │
         ▼
      [Orchestrator] ── final report ──► done
```

Graph state, checkpoints and resumption are managed by **LangGraph** with a Postgres checkpointer — a run can be paused for human approval and resumed later without losing context.

---

## Stack

| Component | Technology | Role |
|---|---|---|
| Agent orchestration | **LangGraph** (Python) | State graph, checkpoints, human-in-the-loop |
| LLM | **Claude API** (Sonnet routine, Opus for complex decisions) | Agent intelligence |
| Database | **PostgreSQL 16 + pgvector** | Projects, runs, tasks, logs; future: semantic memory |
| Object storage | **MinIO** (S3-compatible) | Datasets, models, reports, plots |
| Job orchestration | **Kestra** | Scheduling, pipelines, retries |
| Code execution | **Ephemeral Docker sandbox** | Isolated Python/R runtime, no network, capped mem/cpu |
| API | **FastAPI** | Submit projects, approve steps, inspect runs |
| Reverse proxy | **Nginx** | Exposes API and Kestra UI |

---

## Quickstart

Prerequisites: Docker (Desktop on Mac, Engine on Linux) and an Anthropic API key.

```bash
git clone <repo> cortex && cd cortex
cp .env.example .env
# edit .env: set ANTHROPIC_API_KEY and change default passwords

# build the sandbox image (used on-demand, not part of the main stack)
docker compose --profile tools build sandbox

# start the stack
docker compose up -d --build

# health check
curl http://localhost/api/health
```

Services exposed:

| URL | What |
|---|---|
| `http://localhost/api/` | FastAPI (agent-core) |
| `http://localhost/kestra/` | Kestra UI |
| `http://localhost:9001` | MinIO console |

### Minimal flow

```bash
# 1. Create a project
curl -X POST http://localhost/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"churn-model","description":"Predict churn from customer_events.parquet"}'

# 2. Start a run (returns run_id; agent-core runs the graph in background)
curl -X POST http://localhost/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"<UUID>","datasets":["s3://dsagents/customer_events.parquet"]}'

# 3. Inspect current state (plan, phase, artifacts)
curl http://localhost/api/v1/runs/<RUN_ID>/state

# 4. Approve or reject at a pause point
curl -X POST http://localhost/api/v1/decisions \
  -H 'Content-Type: application/json' \
  -d '{"run_id":"<RUN_ID>","decision":"approved","comments":"plan LGTM"}'
```

---

## Deploying to a server

Same stack, no code changes:

```bash
git clone <repo> cortex && cd cortex
cp .env.example .env   # production secrets
docker compose --profile tools build sandbox
docker compose up -d --build
```

Portability is a hard requirement — all images are multi-arch (arm64/amd64), and the sandbox is wired through a named Docker volume so that the agent-core-spawns-sandbox handoff works identically on Docker Desktop (Mac) and Docker Engine (Linux).

To update production:

```bash
git pull && docker compose up -d --build
```

Recommended starting size: 4 vCPU / 8 GB RAM / 160 GB SSD (e.g. DigitalOcean droplet at ~US$48/mo).

---

## Repository layout

```
cortex/
├── docker-compose.yml
├── .env.example
├── ARCHITECTURE.md           # deep technical spec
├── agent-core/               # FastAPI + LangGraph app
│   ├── Dockerfile
│   └── src/
│       ├── main.py
│       ├── config.py
│       ├── api/              # FastAPI routes + schemas
│       ├── agents/           # Orchestrator, Analyst, Modeler, Reviewer
│       ├── graph/            # LangGraph state, nodes, builder, checkpointer
│       ├── sandbox/          # ephemeral container runner
│       ├── services/         # run orchestration
│       └── storage/          # Postgres + MinIO clients
├── sandbox/                  # image with Python + R + DS libraries
│   ├── Dockerfile
│   └── entrypoint.sh
├── kestra/flows/             # example pipelines
├── nginx/nginx.conf
└── scripts/init-db.sql       # Postgres schema + pgvector
```

---

## Security model

The sandbox is the most critical security boundary:

- **No network** (`network_mode=none`)
- **No host bind mounts** — data flows through a named Docker volume
- **Capped** memory, CPU, PID count; all Linux capabilities dropped; `no-new-privileges`
- **Hard timeout** per execution (default 5 min)
- **Ephemeral** — container is removed after every run

Client data never leaves local volumes (Postgres/MinIO). `.env` is gitignored — each environment has its own.

---

## Documentation

- **[ARCHITECTURE.md](./ARCHITECTURE.md)** — detailed technical specification: agents' responsibilities, graph state shape, DB schema, operational notes, roadmap.

---

## Status

Bootstrapping. Core pieces (FastAPI, Postgres schema, LangGraph skeleton, sandbox runner, agents, Kestra example) are in place. End-to-end run against real data is the next milestone.

## License

TBD.
