import threading
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..services import runs as runs_service
from ..storage import github_manager, github_pm
from ..storage import postgres as db
from .schemas import (
    BacklogItem,
    BacklogOut,
    ClientCreate,
    ClientOut,
    HumanDecisionIn,
    HumanDecisionOut,
    ProjectCreate,
    ProjectOut,
    RunOut,
    RunStart,
    TickIn,
    TickOut,
)

router = APIRouter()


@router.post("/clients", response_model=ClientOut, status_code=201)
async def create_client(payload: ClientCreate) -> ClientOut:
    # Schema já garantiu que só um dos dois modos chega até aqui.
    if payload.auto_create_repo:
        repo = await github_manager.create_client_repo(payload.name)
        if repo is None:
            # Falha explícita: se pediu criar e o GitHub recusou, não criamos
            # cliente órfão. Mensagem acionável pro humano ver o motivo no log.
            raise HTTPException(
                status_code=502,
                detail=(
                    "GitHub recusou a criação do repo — ver logs do agent-core "
                    "(token sem escopo, repo homônimo em outra org, rate limit)."
                ),
            )
    else:
        repo = payload.github_repo
    row = await db.insert_client(payload.name, repo, payload.config)
    return ClientOut(**row)


@router.get("/clients/{client_id}", response_model=ClientOut)
async def get_client(client_id: UUID) -> ClientOut:
    row = await db.fetch_client(client_id)
    if row is None:
        raise HTTPException(status_code=404, detail="client not found")
    return ClientOut(**row)


@router.post("/clients/{client_id}/create-repo")
async def create_client_repo(client_id: UUID) -> dict:
    """Cria um repositório GitHub para o cliente."""
    client = await db.fetch_client(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="client not found")

    if client.get("github_repo"):
        return {"status": "exists", "repo": client["github_repo"]}

    repo_url = await github_manager.create_client_repo(client["name"])
    if repo_url is None:
        raise HTTPException(status_code=500, detail="failed to create repo")

    return {"status": "created", "repo": repo_url}


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(payload: ProjectCreate) -> ProjectOut:
    row = await db.insert_project(
        name=payload.name,
        description=payload.description,
        config=payload.config,
        client_id=payload.client_id,
        workflow_type=payload.workflow_type.value,
    )
    return ProjectOut(**row)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: UUID) -> ProjectOut:
    row = await db.fetch_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return ProjectOut(**row)


@router.get("/projects/{project_id}/backlog", response_model=BacklogOut)
async def get_project_backlog(project_id: UUID) -> BacklogOut:
    """
    Read-only: lista issues que o Cortex pegaria na próxima rodada.

    Critério (ver `github_pm.list_claimable_issues`): issues `open` com alguma
    label `cortex:<kind>` e **sem** `cortex:in-progress`. Ordenadas `updated_at`
    ascendente pra evitar starvation.

    Não faz claim nem dispara run — serve pra humano ver a fila e pra debug
    do loop de ticks que entra em sprint 1/B3.
    """
    project = await db.fetch_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    client_id = project.get("client_id")
    if client_id is None:
        raise HTTPException(
            status_code=409,
            detail="project has no client; backlog requires a client with github_repo",
        )

    client = await db.fetch_client(client_id)
    github_repo = (client or {}).get("github_repo")
    if not github_repo:
        raise HTTPException(
            status_code=409,
            detail="client has no github_repo configured",
        )

    issues = github_pm.list_claimable_issues(github_repo)
    items: list[BacklogItem] = []
    for issue in issues:
        kind = github_pm.get_issue_kind(issue)
        if kind is None:
            # list_claimable_issues já filtrou por label kind — essa guard é
            # defensiva contra evolução da constante _KIND_LABELS.
            continue
        items.append(
            BacklogItem(
                issue_number=issue["number"],
                title=issue["title"],
                kind=kind,
                url=issue["html_url"],
                updated_at=issue["updated_at"],
                labels=[lb.get("name", "") for lb in issue.get("labels", [])],
            )
        )
    return BacklogOut(project_id=project_id, github_repo=github_repo, items=items)


@router.post("/runs", response_model=RunOut, status_code=201)
async def start_run(payload: RunStart, background: BackgroundTasks) -> RunOut:
    project = await db.fetch_project(payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    # Resolve github_repo do cliente (evita consulta no node, que roda em thread)
    github_repo = None
    if project.get("client_id"):
        client = await db.fetch_client(project["client_id"])
        github_repo = (client or {}).get("github_repo")

    row = await db.insert_run(payload.project_id)

    t = threading.Thread(
        target=runs_service.start_run,
        kwargs={
            "run_id": row["id"],
            "project_id": payload.project_id,
            "description": project["description"] or "",
            "datasets": payload.datasets,
            "workflow_type": project.get("workflow_type", "full_ml"),
            "client_id": str(project["client_id"]) if project.get("client_id") else None,
            "github_repo": github_repo,
        },
        daemon=True,
    )
    t.start()
    return RunOut(**row)


@router.post("/ticks", response_model=TickOut)
async def tick(payload: TickIn, background: BackgroundTasks) -> TickOut:
    """
    Um "tick" do Cortex: pega a próxima issue claimável do backlog do projeto,
    claim via label `cortex:in-progress` e dispara um run. Retorna sempre 200
    com `status` discriminado (started | idle | skipped) — idle/skipped não
    são erros, são estados normais do loop de polling.
    """
    project = await db.fetch_project(payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    client_id = project.get("client_id")
    if client_id is None:
        raise HTTPException(
            status_code=409,
            detail="project has no client; ticks require a client with github_repo",
        )
    client = await db.fetch_client(client_id)
    github_repo = (client or {}).get("github_repo")
    if not github_repo:
        raise HTTPException(status_code=409, detail="client has no github_repo configured")

    # 1. Candidatos
    issues = github_pm.list_claimable_issues(github_repo)
    if not issues:
        return TickOut(status="idle", reason="no claimable issues")

    # 2. Mais antiga (updated_at asc no list_claimable_issues) — anti-starvation
    target = issues[0]
    kind = github_pm.get_issue_kind(target)
    if kind is None:
        return TickOut(status="idle", reason="no claimable issues")

    # 3. Claim; se perdeu race, devolve skipped
    if not github_pm.claim_issue(github_repo, target["number"]):
        return TickOut(
            status="skipped",
            reason=f"race lost on issue #{target['number']}",
            issue_number=target["number"],
            issue_kind=kind,
            issue_title=target.get("title"),
        )

    # 4. Cria run com contexto da issue e dispara thread
    row = await db.insert_run(
        payload.project_id,
        issue_number=target["number"],
        issue_kind=kind,
        issue_title=target.get("title"),
    )
    threading.Thread(
        target=runs_service.start_run,
        kwargs={
            "run_id": row["id"],
            "project_id": payload.project_id,
            "description": project["description"] or "",
            "datasets": payload.datasets,
            "workflow_type": project.get("workflow_type", "full_ml"),
            "client_id": str(client_id),
            "github_repo": github_repo,
            "issue_number": target["number"],
            "issue_kind": kind,
            "issue_title": target.get("title"),
        },
        daemon=True,
    ).start()

    return TickOut(
        status="started",
        run_id=row["id"],
        issue_number=target["number"],
        issue_kind=kind,
        issue_title=target.get("title"),
    )


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: UUID) -> RunOut:
    row = await db.fetch_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunOut(**row)


@router.get("/runs/{run_id}/state")
async def get_run_state(run_id: UUID) -> dict:
    state = runs_service.get_run_state(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run state not found")
    return state


@router.post("/decisions", response_model=HumanDecisionOut, status_code=201)
async def record_decision(
    payload: HumanDecisionIn, background: BackgroundTasks
) -> HumanDecisionOut:
    if payload.decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="decision must be approved|rejected")

    row = await db.insert_human_decision(
        payload.run_id, payload.task_id, payload.decision, payload.comments
    )
    threading.Thread(
        target=runs_service.resume_run,
        kwargs={
            "run_id": payload.run_id,
            "decision": payload.decision,
            "comments": payload.comments,
        },
        daemon=True,
    ).start()
    return HumanDecisionOut(**row)
