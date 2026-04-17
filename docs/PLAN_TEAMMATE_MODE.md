# Cortex Teammate Mode — Plano de Refatoração

> Plano geral para transformar o Cortex de "IA autônoma que roda um projeto ponta-a-ponta" em **parceiro de trabalho** que opera num repo existente, pega tarefas do backlog, e trabalha em paralelo com o humano sem atrapalhar.

## Motivação

O Cortex hoje:
- **Sempre cria** repo novo quando um projeto é iniciado, mesmo quando o humano queria continuar trabalho em repo existente.
- Executa um "full_ml" linear de ponta-a-ponta, com checkpoints de aprovação humana obrigatórios entre fases.
- Exige que o humano dispare um `POST /v1/runs` e acompanhe o processo até o fim.
- Gera resultado pobre no repo (README + JSON), sem a estrutura rica de Quarto/artefatos do "penúltimo projeto".
- Pede aprovação humana demais; mínimo erro vira cobrança.

O que o dono quer:
- **Parceiro de trabalho, não orquestrador linear.** O Cortex e o humano trabalham **no mesmo repo, ao mesmo tempo**, sem pisar no pé um do outro.
- **1 issue = 1 run = 1 PR.** Granularidade pequena. Cada unidade de trabalho é uma issue do GitHub, executada num run curto, entregue num PR pequeno, idealmente auto-mergeável.
- **Criar repo novo OU continuar existente — conforme a intenção.** Se o humano diz "novo projeto X", Cortex cria o repo `X`. Se diz "continua o projeto Y", Cortex opera em `Y` já existente. Capacidade de criar repo **fica** (foi trabalho conquistar); o que muda é que criar deixa de ser o default automático.
- **Estrutura determinística e rica.** Cada etapa produz um Quarto report (`outputs/quality.html`, `outputs/hypothesis.html`, etc.) + artefatos (`artifacts/models/`, `artifacts/plots/`).
- **Auto-approve por padrão.** Só pausa para humano em bloqueio real (data leak, ambiguidade no brief, métrica abaixo de threshold).
- **Sem frescura.** Funcionar sem inventar moda. Fail loud com mensagem acionável.

## Modelo mental alvo

```
Repo (ex.: ecommerce-demo)
├── Issues (backlog) ──────────── humano e Cortex pegam daqui
│   ├── #12 [cortex:quality] ── Cortex pega
│   ├── #13 [cortex:eda]     ── ninguém claim ainda
│   └── #14                  ── humano pega (sem label cortex)
├── main ──────────────────────── sempre verde; PRs mergeiam aqui
└── branches run/<short> ──────── 1 por run; commit(s) pequenos; PR curto
```

Ciclo base:
1. Humano (ou job cron) dispara o Cortex: "tick — olha o backlog".
2. Cortex lista issues abertas com label `cortex:*` **sem** label `cortex:in-progress`.
3. Pega a issue mais prioritária (label `priority:*` ou ordem do backlog).
4. Põe label `cortex:in-progress` + comentário "estou nisso, ETA ~N min".
5. Clona repo (shallow, read-only), detecta o que já existe, executa só a etapa necessária.
6. Gera artefatos na estrutura padrão, abre PR pequeno.
7. Se CI passar e review automática não flagrar → auto-merge → fecha a issue.
8. Libera lock (label `cortex:in-progress` sai).

## Concorrência humano ↔ Cortex

- **Lock por label (`cortex:in-progress`)**: ao pegar a issue, Cortex adiciona a label. Humano que ver essa label sabe que é pra deixar quieto.
- **Lease com TTL**: label `cortex:in-progress` tem um "carimbo" em comentário (`lease-until: <iso-ts>`). Job de limpeza periódica remove label expirada — cobre caso de crash.
- **Fetch antes de cada push**: Cortex sempre `git fetch` + rebase no `origin/main` antes de abrir PR. Se houver commit humano no caminho, Cortex rebaseia em cima.
- **Desistência limpa**: se humano fechar a issue manualmente durante um run, Cortex detecta no próximo heartbeat e aborta (sem push), comentando "pegou humano, desistindo".
- **Sem lock global no repo**: humano pode comitar no `main` a qualquer momento, Cortex só se preocupa com sua branch de run.

## Sprints

Cada sprint entrega valor incremental. Ordem importa: sprint N assume a base do sprint N-1.

### Sprint 1 — Issue-driven + Repo-awareness (novo OU existente)

**Objetivo**: Cortex passa a tratar "criar repo" como ação **sob demanda**, não default. Opera em repo existente quando for o caso, e pega 1 issue por run.

Mudanças:
- `api/schemas.py` — `ProjectCreate` ganha dois modos mutuamente exclusivos:
  - **modo continuar** (novo default): `github_repo` (URL completa) — obrigatório se `create_new=false`.
  - **modo novo**: `create_new=true` + `new_repo_name` + `visibility` (`private`/`public`) — dispara criação.
  - Validação: exatamente um dos dois modos deve ser usado. 400 claro se ambíguo.
- `api/routes.py` — novo endpoint `POST /v1/ticks` (ou reuso de `POST /v1/runs` sem body): dispara um "pegar 1 issue do backlog e executar". Tick assume que o projeto já foi criado via `POST /v1/projects`.
- `storage/github_manager.py` — `create_client_repo()` **permanece**, mas só é chamado quando `create_new=true`. Em "modo continuar", se o repo não existe, levanta erro claro ao invés de criar silenciosamente.
- `graph/nodes.py` — novo nó `pick_issue` no topo do grafo, que lista issues abertas com label `cortex:*` e sem `cortex:in-progress`, e decide a kind (`quality` / `eda` / `modeling` / etc.) pela label.
- `scripts/init-db.sql` — migração: `projects.github_repo NOT NULL` (populada tanto em criação quanto em continuação; sempre aponta pro repo efetivo).

**Pronto quando**:
- `POST /v1/projects` com `{"create_new": true, "new_repo_name": "churn-demo"}` cria repo novo e cadastra projeto.
- `POST /v1/projects` com `{"github_repo": "https://github.com/wrprates/ecommerce-demo"}` cadastra projeto sobre repo existente sem tocar no GitHub.
- Em ambos os casos, abro uma issue `[cortex:quality]` e em minutos um PR aparece fechando essa issue.

### Sprint 2 — Concorrência sem pisar no pé

**Objetivo**: múltiplos humanos/Cortex podem trabalhar no mesmo repo sem colidir.

Mudanças:
- `storage/github_manager.py` — `claim_issue(issue_id) -> bool` (adiciona label `cortex:in-progress` + comentário com lease). Falha se já claimed.
- `storage/github_manager.py` — `release_claim(issue_id)`, `lease_expired(issue_id)`.
- `graph/nodes.py` — antes de cada `git push`, rodar `git fetch && git rebase origin/main`. Se falhar, comentar no PR "rebase manual necessário" e abortar run limpo.
- Cron job (`scripts/lease_sweeper.py`) — roda a cada 5min, remove label `cortex:in-progress` de issues com lease expirado.
- Heartbeat no meio do run: a cada N segundos, Cortex verifica se a issue ainda está open + claimed por ele. Se não, aborta.

**Pronto quando**: eu abro 3 issues com label `cortex:quality` ao mesmo tempo, disparo 3 ticks em paralelo, e 3 PRs diferentes aparecem sem conflito.

### Sprint 3 — Estrutura determinística do repo DS

**Objetivo**: repo fica rico (Quarto HTMLs, artefatos, README vivo) conforme issues fecham.

Estrutura alvo:
```
<repo>/
├── README.md         overview do projeto + status de cada etapa (auto-atualizado)
├── R/                01_quality.R, 02_hypothesis.R, 03_modeling.R, 04_report.R
├── outputs/          quality.{qmd,html}, hypothesis.{qmd,html}, modeling.{qmd,html}, report.{qmd,html}
├── artifacts/        models/, plots/, metrics.json, leaderboard.csv
└── data/             sample.csv (amostra pública) + schema.json
```

Mudanças:
- `templates/` — scaffolds de `.qmd` por etapa.
- `agents/analyst_r.py`, `modeler_r.py` — deixam de publicar só no MinIO; passam a escrever direto nos paths determinísticos do repo (R/, outputs/, artifacts/).
- `graph/nodes.py` — cada nó, antes de rodar, checa se seu artefato já existe. Se sim, pula. Idempotência por design.
- Quarto render acontece no sandbox R e o HTML é comitado.

**Pronto quando**: abro issues `cortex:quality`, `cortex:eda`, `cortex:modeling` em sequência, e ao final o `main` tem os 3 HTMLs renderizados + `artifacts/models/*.rds` + README com checklist atualizado.

### Sprint 4 — Auto-approve + CI

**Objetivo**: PR do Cortex se mergeia sozinho se o trabalho estiver sanado.

Mudanças:
- Remover `interrupt_after=["planning", "modeling"]` do `graph/builder.py`.
- GitHub Actions mínima: roda `Rscript -e "rmarkdown::render(...)"` na PR pra garantir que os Quarto compilam; valida `metrics.json` contra schema básico.
- `graph/nodes.py` — nó `self_review` decide: se checks passam → merge automático via API; se não → comenta no PR pedindo atenção humana e deixa label `cortex:needs-human`.
- Pausa humana só acontece se: leak detectado, `metrics.json` abaixo de threshold configurável, conflito de dados não resolvível, erro de schema.

**Pronto quando**: nenhum `/v1/decisions` precisa ser chamado num fluxo feliz. Ciclo completo (tick → PR → merge → issue fechada) roda sem intervenção.

### Sprint 5 — Robustez, menos erros, fail loud

**Objetivo**: Cortex confiável. Não fica cobrando humano por problemas internos dele.

Mudanças:
- Testes de contrato: `tests/test_strip_code_fences.py` com casos ruins de LLM (prosa PT misturada, fences mal formados, etc.).
- Retry inteligente no `analyst_r`: erro R `unexpected symbol` → reprompt específico ("sua resposta anterior continha prosa PT; repita só R"). Não retry genérico.
- Fail loud: `GITHUB_TOKEN` faltando, sandbox sem `quarto`, Postgres offline → mensagem acionável de boot, não 500 no meio do run.
- CI no próprio `cortex`: smoke test end-to-end a cada PR (issue fake → tick → PR esperado).

**Pronto quando**: eu simulo 3 falhas (token expirado, dataset corrompido, modelo ruim) e cada uma vira uma mensagem clara no PR ou na boot log — nenhuma vira crash silencioso nem cobra aprovação do humano.

## Itens que saem de escopo agora

- **Vários clientes/múltiplos repos num Cortex só**: por enquanto 1 Cortex opera em 1 repo. Multi-repo fica pra depois.
- **Humano aprovando PR da UI do Cortex**: não. Aprovação é via GitHub (merge do PR). Cortex não vai ter UI própria.
- **Deploy automático do `cortex` em sí**: o droplet continua manual por rsync+rebuild. CI do `cortex` fica pra sprint 5.
- **Kestra**: continua no stack pra schedulers futuros, mas não entra no fluxo core dessa refatoração.

## Riscos e mitigações

| Risco | Mitigação |
|-------|-----------|
| Rebase em cima de commit humano quebra o trabalho do Cortex | Sprint 2: se rebase falha, aborta run e pede atenção humana no PR |
| Quarto render demora muito / estoura timeout | `SANDBOX_TIMEOUT_SECONDS` por etapa; render fica fora do timeout do LLM |
| `cortex:in-progress` fica preso por crash | Lease sweeper (Sprint 2) + TTL |
| Issues abertas sem label `cortex:*` caem no backlog e confundem | Cortex **só** pega issues com label explícita `cortex:*`. Tudo sem label é humano |
| Múltiplos ticks simultâneos tentam a mesma issue | `claim_issue` é atômico via GitHub API (PUT label falha se já existe pela mesma session) |

## Como medir sucesso

Um dia típico de uso "teammate mode":
- Humano abre 4 issues no `ecommerce-demo` antes do café.
- Cron dispara tick a cada 10min.
- Ao meio-dia, 3 PRs foram abertos, auto-mergeados, issues fechadas. 1 issue ficou com label `cortex:needs-human` porque o modelo estourou threshold; humano olha e decide.
- Repo `ecommerce-demo/main` tem Quarto reports atualizados, artefatos comitados, README com checklist.
- Zero `POST /v1/decisions` foi chamado no dia.

---

## Próximos passos imediatos

1. **Sprint 1 em branch dedicada** (essa aqui: `plan/teammate-mode-refactor` fica só pro plano; Sprint 1 vira `feat/v0.9-sprint1-issue-driven` ou similar).
2. Antes de codar Sprint 1: escrever migration SQL + testar localmente contra uma cópia do banco do droplet.
3. Desenhar contrato de labels: `cortex:quality`, `cortex:eda`, `cortex:modeling`, `cortex:review`, `cortex:in-progress`, `cortex:needs-human`, `priority:high|medium|low`.
