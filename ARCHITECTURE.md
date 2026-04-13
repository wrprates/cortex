# DS-Agents — Arquitetura e Especificação

## Visão geral

Sistema multiagente de ciência de dados usando LLMs. O objetivo é ter uma "equipe virtual" de data science que recebe um problema, planeja, executa código, valida resultados e gera relatórios — com aprovação humana em pontos críticos.

O sistema precisa ser **100% Docker**, portável entre o MacBook de desenvolvimento e qualquer servidor Linux (DigitalOcean, AWS, etc.).

O dono do projeto é um data scientist experiente que já tem 2 projetos fechados esperando essa infraestrutura. O sistema precisa funcionar de verdade, não é experimento acadêmico.

---

## Stack tecnológica

| Componente | Tecnologia | Papel |
|---|---|---|
| Orquestração de agentes | **LangGraph** (Python) | Cérebro do sistema — grafo de estados, checkpoints, human-in-the-loop |
| LLM | **Claude API** (Sonnet para tarefas rotineiras, Opus para decisões complexas) | Inteligência dos agentes |
| Banco de dados | **PostgreSQL 16 + pgvector** | Estado dos projetos, runs, tasks, logs, metadados, e futuramente embeddings |
| Armazenamento de artefatos | **MinIO** (compatível com S3) | Datasets, modelos, relatórios, notebooks, plots |
| Orquestrador de jobs | **Kestra** | Agendamento, execução de pipelines, retries, logs operacionais |
| Execução de código | **Container sandbox efêmero** | Roda código Python/R gerado pelos agentes de forma isolada e segura |
| Reverse proxy | **Nginx** | Expõe interfaces (API, Kestra UI) |
| API do sistema | **FastAPI** | Endpoint para submeter projetos, aprovar etapas, consultar status |

---

## Arquitetura de containers (Docker Compose)

```
docker-compose.yml
│
├── agent-core        → App Python (LangGraph + FastAPI)
├── postgres           → PostgreSQL 16 com pgvector
├── minio              → Object storage S3-compatible
├── kestra             → Orquestrador de workflows
├── sandbox            → Container efêmero para execução de código (profile: tools)
└── nginx              → Reverse proxy
```

### Regras importantes

- **Tudo roda via `docker compose up`** — tanto no Mac (dev) quanto no servidor (prod).
- O `.env` fica fora do Git (`.gitignore`). Cada ambiente tem seu próprio `.env`.
- Dados de clientes **nunca** vão para o GitHub. Ficam nos volumes Docker (MinIO/Postgres).
- O container `sandbox` **não fica rodando**. O `agent-core` o spawna sob demanda para executar código e depois mata.
- O `agent-core` precisa de acesso ao Docker socket para spawnar containers sandbox.

---

## Estrutura de pastas

```
ds-agents/
├── docker-compose.yml
├── .env.example                 # Template das variáveis (sem secrets)
├── .gitignore
├── README.md
├── ARCHITECTURE.md              # Este documento
│
├── agent-core/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── src/
│       ├── main.py              # FastAPI app
│       ├── config.py            # Settings (pydantic-settings, lê do .env)
│       ├── graph/
│       │   ├── state.py         # Definição do estado do grafo LangGraph
│       │   ├── builder.py       # Construção do grafo (nós, edges, checkpoints)
│       │   └── nodes.py         # Funções de cada nó do grafo
│       ├── agents/
│       │   ├── orchestrator.py  # Prompt + tools do Orchestrator
│       │   ├── analyst.py       # Prompt + tools do Data Analyst
│       │   ├── modeler.py       # Prompt + tools do Modeler
│       │   └── reviewer.py      # Prompt + tools do Reviewer
│       ├── sandbox/
│       │   ├── runner.py        # Lógica de spawn do container efêmero
│       │   └── docker_client.py # Interface com Docker SDK
│       ├── storage/
│       │   ├── postgres.py      # Interface com PostgreSQL (asyncpg ou sqlalchemy)
│       │   └── minio_client.py  # Interface com MinIO (boto3)
│       └── api/
│           ├── routes.py        # Endpoints FastAPI
│           └── schemas.py       # Pydantic models para request/response
│
├── sandbox/
│   ├── Dockerfile               # Python 3.11 + R + libs de data science
│   └── entrypoint.sh            # Recebe script, executa, retorna resultado
│
├── kestra/
│   └── flows/                   # Workflows YAML do Kestra
│       └── example-pipeline.yml
│
├── nginx/
│   └── nginx.conf
│
└── scripts/
    ├── init-db.sql              # Schema inicial do PostgreSQL
    └── backup.sh                # Script de backup (pg_dump + minio sync)
```

---

## Agentes — definição e responsabilidades

### 1. Orchestrator (Project Manager)

**Papel:** Recebe o briefing do projeto, cria plano de trabalho, gerencia o estado, decide qual agente chamar, compila resultado final.

**Inputs:** Descrição do problema, dados disponíveis, restrições.

**Outputs:** Plano de etapas, delegação de tarefas, relatório final consolidado.

**Quando chama humano:** Aprovação do plano inicial, decisões estratégicas ambíguas.

### 2. Data Analyst

**Papel:** Inspeciona dados, faz EDA, gera hipóteses, produz visualizações e estatísticas descritivas.

**Inputs:** Dataset (caminho no MinIO), perguntas do Orchestrator.

**Outputs:** Relatório de EDA, plots (salvos no MinIO), hipóteses, alertas sobre qualidade dos dados.

**Execução:** Gera código Python/R → executa no sandbox → coleta resultado.

### 3. Modeler

**Papel:** Feature engineering, treinamento de modelos, comparação de métricas, seleção do melhor modelo.

**Inputs:** Dados preparados, hipóteses do Analyst, critérios de sucesso.

**Outputs:** Modelos treinados (salvos no MinIO), tabela comparativa de métricas, justificativa da escolha.

**Quando chama humano:** Antes de treinar modelo final / em larga escala.

### 4. Reviewer (Crítico)

**Papel:** Audita o trabalho dos outros agentes. Procura data leakage, erros metodológicos, splits problemáticos, métricas inadequadas, viés.

**Inputs:** Artefatos e relatórios dos outros agentes.

**Outputs:** Parecer de aprovação ou lista de problemas encontrados. Se reprova, o fluxo volta para o agente responsável.

**Regra:** O Reviewer NUNCA é o mesmo "cérebro" que produziu o trabalho. Ele recebe o output e avalia com prompt independente, focado em encontrar problemas.

---

## Fluxo de um projeto (grafo LangGraph)

```
[INÍCIO]
    │
    ▼
[Orchestrator] ──── gera plano ────► [HUMAN APPROVAL]
    │                                       │
    │◄──────── aprovado ───────────────────┘
    │
    ▼
[Data Analyst] ──── EDA ────► resultados
    │
    ▼
[Orchestrator] ──── avalia EDA, decide próximo passo
    │
    ▼
[Modeler] ──── feature eng + treino ────► [HUMAN APPROVAL]
    │                                            │
    │◄──────── aprovado ────────────────────────┘
    │
    ▼
[Reviewer] ──── audita tudo
    │
    ├── aprovado ────► [Orchestrator] ──── gera relatório final ────► [FIM]
    │
    └── reprovado ───► volta para o agente responsável (loop)
```

### Estados do grafo (LangGraph State)

```python
class ProjectState(TypedDict):
    project_id: str
    description: str
    plan: dict                    # plano gerado pelo Orchestrator
    current_phase: str            # "planning", "eda", "modeling", "review", "reporting"
    datasets: list[str]           # caminhos no MinIO
    eda_results: dict             # output do Data Analyst
    model_results: dict           # output do Modeler
    review_results: dict          # output do Reviewer
    human_decisions: list[dict]   # log de aprovações/rejeições humanas
    messages: list                # histórico de mensagens entre agentes
    artifacts: list[str]          # caminhos de artefatos gerados (MinIO)
    status: str                   # "active", "waiting_human", "completed", "failed"
```

---

## Sandbox de execução de código — CRÍTICO

O sandbox é o componente mais importante de segurança do sistema.

### Regras

- **Container efêmero**: spawna, executa, retorna resultado, morre.
- **Sem acesso à rede**: `network_mode: none` no Docker.
- **Timeout**: máximo 5 minutos por execução (configurável).
- **Limites de memória**: 2 GB por execução (configurável).
- **Sem acesso ao host**: sem bind mounts ao filesystem do host.
- **Comunicação**: o agent-core passa o script + dados via volume temporário, o sandbox executa e escreve o resultado no mesmo volume.

### Dockerfile do sandbox

Deve incluir:
- Python 3.11 com: pandas, numpy, scikit-learn, matplotlib, seaborn, plotly, statsmodels, xgboost, lightgbm, scipy, category_encoders
- R com: tidyverse, caret, data.table, ggplot2
- Jupyter (para execução de notebooks via papermill, se necessário)

### Fluxo de execução

1. Agent gera código
2. `agent-core` cria volume temporário, escreve script + dados
3. Spawna container sandbox com esse volume montado
4. Sandbox executa, escreve output (resultados, plots, logs) no volume
5. `agent-core` lê o output
6. Container sandbox é removido
7. Volume temporário é limpo

---

## Schema do PostgreSQL

### Tabelas principais

**projects** — um registro por projeto de data science
- id (UUID), name, description, status, created_at, updated_at, config (JSONB)

**runs** — cada execução do grafo para um projeto
- id (UUID), project_id (FK), started_at, finished_at, status, final_state (JSONB)

**tasks** — cada tarefa delegada a um agente dentro de um run
- id (UUID), run_id (FK), agent_name, phase, input (JSONB), output (JSONB), status, started_at, finished_at, tokens_used, cost_estimate

**artifacts** — referência a arquivos no MinIO
- id (UUID), task_id (FK), project_id (FK), artifact_type (dataset/model/plot/report), minio_path, metadata (JSONB), created_at

**human_decisions** — log de aprovações/rejeições
- id (UUID), run_id (FK), task_id (FK), decision (approved/rejected), comments, decided_at

**agent_logs** — log detalhado de cada chamada LLM
- id (UUID), task_id (FK), agent_name, prompt_hash, model_used, tokens_in, tokens_out, latency_ms, created_at

---

## Variáveis de ambiente (.env)

```env
# LLM
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...           # opcional, backup

# Postgres
PG_PASSWORD=sua_senha_segura
PG_HOST=postgres
PG_PORT=5432
PG_DB=dsagents
PG_USER=dsagents

# MinIO
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=sua_senha_segura
MINIO_ENDPOINT=minio:9000

# Kestra
KESTRA_DB_URL=jdbc:postgresql://postgres:5432/dsagents

# Sandbox
SANDBOX_TIMEOUT_SECONDS=300
SANDBOX_MEMORY_LIMIT=2g
SANDBOX_IMAGE=ds-agents-sandbox:latest

# Geral
ENVIRONMENT=development         # development | production
LOG_LEVEL=INFO
```

---

## Logística de desenvolvimento → produção

### No Mac (desenvolvimento)
1. Clonar repo do GitHub
2. Criar `.env` baseado no `.env.example`
3. `docker compose up`
4. Desenvolver, testar, iterar
5. `git push`

### No servidor (produção)
1. `git clone` do repo
2. Criar `.env` com keys de produção
3. `docker compose up -d`
4. Configurar backup automatizado (cron + `scripts/backup.sh`)

### Para atualizar produção
```bash
git pull
docker compose up -d --build
```

---

## Infra recomendada

### Desenvolvimento
- MacBook Pro M1 16 GB (suficiente, stack consome ~4-6 GB RAM)
- Docker Desktop

### Produção (início)
- DigitalOcean Droplet: 4 vCPU, 8 GB RAM, 160 GB SSD (~US$48/mês)
- Custo API Claude: ~US$30-80/mês para 2 projetos
- Total estimado: ~US$80-130/mês

### Produção (escala)
- AWS EC2/ECS se virar produto B2B
- GPU somente se rodar modelos open source locais

---

## Evolução futura (NÃO implementar agora)

- [ ] Memória semântica com pgvector (extensão já instalada, indexar quando tiver histórico)
- [ ] Templates de relatório sofisticados
- [ ] Interface web (Dify, Open WebUI, ou React)
- [ ] Integrações com Telegram/WhatsApp (OpenClaw)
- [ ] Mais agentes especializados conforme demanda
- [ ] Observabilidade (LangSmith ou similar)
- [ ] Modelos open source locais

---

## Como usar este documento

1. Crie o repo `ds-agents` no GitHub
2. Clone no Mac
3. Coloque este arquivo como `ARCHITECTURE.md` na raiz
4. Abra o Claude Code ou Claude no VS Code
5. Peça: **"Leia o ARCHITECTURE.md e implemente o projeto completo seguindo a especificação."**
6. Revise o código gerado
7. `docker compose up` para testar
8. Itere até funcionar

---

*Documento gerado em sessão de planejamento. Abril 2026.*
