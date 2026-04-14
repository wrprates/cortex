from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..services import runs as runs_service
from ..storage import postgres as db
from ..storage import github_manager
from .schemas import (
    ClientCreate,
    ClientOut,
    HumanDecisionIn,
    HumanDecisionOut,
    ProjectCreate,
    ProjectOut,
    RunOut,
    RunStart,
)

router = APIRouter()


@router.post("/clients", response_model=ClientOut, status_code=201)
async def create_client(payload: ClientCreate) -> ClientOut:
    row = await db.insert_client(payload.name, payload.github_repo, payload.config)
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
        primary_language=payload.primary_language.value,
    )
    return ProjectOut(**row)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: UUID) -> ProjectOut:
    row = await db.fetch_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="project not found")
    return ProjectOut(**row)


@router.post("/runs", response_model=RunOut, status_code=201)
async def start_run(payload: RunStart, background: BackgroundTasks) -> RunOut:
    project = await db.fetch_project(payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    row = await db.insert_run(payload.project_id)

    background.add_task(
        runs_service.start_run,
        run_id=row["id"],
        project_id=payload.project_id,
        description=project["description"] or "",
        datasets=payload.datasets,
        workflow_type=project.get("workflow_type", "full_ml"),
        primary_language=project.get("primary_language", "r"),
        client_id=str(project["client_id"]) if project.get("client_id") else None,
    )
    return RunOut(**row)


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
    background.add_task(
        runs_service.resume_run,
        run_id=payload.run_id,
        decision=payload.decision,
        comments=payload.comments,
    )
    return HumanDecisionOut(**row)
