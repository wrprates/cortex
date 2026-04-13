# cortex

Sistema multiagente de ciência de dados (DS-Agents). Ver [ARCHITECTURE.md](./ARCHITECTURE.md).

## Quickstart

```bash
cp .env.example .env
# edite .env com suas chaves (ANTHROPIC_API_KEY, senhas)

# build da imagem do sandbox (usada on-demand pelo agent-core)
docker compose --profile tools build sandbox

# sobe o stack principal
docker compose up -d --build
```

Serviços:

- `http://localhost/api/` — FastAPI do agent-core
- `http://localhost/kestra/` — UI do Kestra
- `http://localhost:9001` — Console do MinIO

## Estrutura

Ver `ARCHITECTURE.md`.
