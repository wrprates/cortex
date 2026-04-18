---
marp: true
theme: gaia
paginate: true
backgroundColor: "#fff"
style: |
  section { font-size: 26px; }
  h1 { color: #0b3a82; }
  h2 { color: #0b3a82; }
  code { background: #f1f3f5; padding: 2px 6px; border-radius: 4px; }
  .small { font-size: 20px; color: #555; }
  .tag { background:#e7f0ff; color:#0b3a82; padding:2px 8px; border-radius:12px; font-size:18px; }
---

<!-- _class: lead -->

# Cortex

### A data-science teammate that works on GitHub issues alongside you

<span class="small">Engineering showcase · LangGraph + FastAPI + R (tidyverse/tidymodels/Quarto) + ephemeral Docker sandbox</span>

---

## The problem with "autonomous DS agents"

- Most DS agent demos pretend the whole pipeline can run unattended.
- In practice they fail the moment reality hits: schema drift, a weird column, a flaky library.
- And when they work, the output is a **one-shot zip of code + charts** — no history, no review, no reproducibility.
- DS work is inherently **collaborative and iterative**. A teammate, not a black box.

---

## Design principle

> **Cortex is a teammate, not an autonomous agent.**

- **1 issue = 1 run = 1 PR.** Human and Cortex pull from the same backlog.
- You can work in parallel: pick some issues, Cortex picks others, nobody collides.
- Humans review PRs the normal way. Deterministic structure, reproducible history.
- Fail **loud** on real blockers. Don't ask for approval every 5 minutes.

---

## Architecture

```
   ┌─────────────────┐   POST /v1/ticks      ┌──────────────────┐
   │   GitHub Issues │──────────────────────▶│  FastAPI / API   │
   │  (backlog)      │◀──── claim via label ─│  cortex-agent    │
   └─────────────────┘                        └────────┬─────────┘
           ▲  PR + comments                            │
           │                                           ▼
   ┌───────┴─────────┐     ┌──────────────────────────────────┐
   │   Your repo     │◀────│ LangGraph state machine           │
   │   (R code +     │     │ plan→quality→EDA→model→review→rep │
   │    Quarto HTML) │     └──────────┬───────────────────────┘
   └─────────────────┘                │
                                      ▼
                   ┌─────────────────────────────────┐
                   │ Ephemeral Docker sandbox (R)     │
                   │  tidyverse · tidymodels · Quarto │
                   └─────────────────────────────────┘
   Postgres = LangGraph checkpointer · MinIO = dataset/artifact store
```

---

## The graph

```
probe → plan ──┐ (interrupt_after)    approve
               ▼
             quality ─▶ hypothesis ─▶ modeling ──┐ (interrupt_after)   approve
                                                  ▼
                                                review ─▶ report ─▶ done
```

- Two human breakpoints only: **post-plan**, **post-model**. Everything else runs.
- State is persisted in Postgres — resuming after approval is just `graph.invoke(None)`.
- Hard guards against infinite loops (e.g. re-entering `modeling` after approval).

---

## Deterministic reports

- **Decisions stay with the LLM** (classification, narrative, hypotheses).
- **Structure stays in the repo** — Quarto templates pinned to `agent-core/src/templates/*.qmd`.
- The agent does NOT generate the `.qmd` layout anymore. It produces `summary.json`; the template renders.
- Every run yields the same shape of HTML output — you can diff reports across runs.

<span class="tag">zero surprise</span> <span class="tag">grep-able</span> <span class="tag">reproducible</span>

---

## Concurrency model (teammate-safe)

- Backlog = GitHub issues open, labeled `cortex:<kind>`, **without** `cortex:in-progress`.
- Claim = add `cortex:in-progress` label + comment with ISO `lease-until` timestamp.
- Race detection: re-read the issue immediately before writing; if someone else claimed, back off.
- End of run: release label + close issue. On push failure: release only (leave open to retry).

<span class="small">Coming next: lease sweeper for orphaned claims, `git fetch/rebase` before commit to cope with parallel human merges.</span>

---

## Stack choices (and why)

| Component | Choice | Why |
|---|---|---|
| Language in sandbox | **R** (tidyverse, tidymodels, Quarto) | Our audience is statisticians; echarts4r beats matplotlib for client decks |
| LLM routing | Opus for plan/review, Sonnet for execution | Opus too expensive for per-stage codegen; Sonnet handles routine work well |
| GitHub integration | Direct **REST** via httpx — no `gh` CLI | Deterministic, testable with `respx`, no shell dependency |
| State | **Postgres** as LangGraph checkpointer | Survives restarts, inspectable, plays nice with async FastAPI |
| Tests | pytest + respx + monkeypatch | 45/45 green across GitHub helpers, `/ticks`, close-issue cycle |

---

## Where we are / what's next

| Sprint | Scope | Status |
|---|---|---|
| 1 — issue-driven loop | backlog, tick, claim, close | ✅ Done (PRs #18–#24) |
| 3 — rich Quarto templates | quality report with gt + boxplots + target analysis | 🚧 In progress (PR #25) |
| 2 — robust concurrency | lease sweeper, heartbeat, rebase-before-push | ⏳ Next |
| 4 — auto-approve on green CI | skip human approval when CI + metrics pass | ⏳ Queued |
| 5 — fail-loud hardening | budget caps, token tracking, explicit error surfaces | ⏳ Queued |

---

<!-- _class: lead -->

## Takeaways

- **Collaborator, not autonomous.** The human always owns the merge.
- **Structure in the repo, decisions in the LLM.** Quarto templates stay pinned; prompts change.
- **Explicit intent > smart defaults.** `auto_create_repo=true` is *asked for*, never inferred.
- **Fail loud on real blockers.** Don't ask for approval on every trivial choice.

<br>

### github.com/wrprates/cortex
