from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException

from ..services import runs as runs_service
from ..storage import postgres as db
from .schemas import (
    HumanDecisionIn,
    HumanDecisionOut,
    ProjectCreate,
    ProjectOut,
    RunOut,
    RunStart,
)

router = APIRouter()


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(payload: ProjectCreate) -> ProjectOut:
    row = await db.insert_project(payload.name, payload.description, payload.config)
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
