from uuid import UUID

from fastapi import APIRouter, HTTPException

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
async def start_run(payload: RunStart) -> RunOut:
    project = await db.fetch_project(payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    row = await db.insert_run(payload.project_id)
    # TODO (Fase 6): enfileirar execução do grafo LangGraph
    return RunOut(**row)


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: UUID) -> RunOut:
    row = await db.fetch_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunOut(**row)


@router.post("/decisions", response_model=HumanDecisionOut, status_code=201)
async def record_decision(payload: HumanDecisionIn) -> HumanDecisionOut:
    if payload.decision not in {"approved", "rejected"}:
        raise HTTPException(status_code=400, detail="decision must be approved|rejected")
    row = await db.insert_human_decision(
        payload.run_id, payload.task_id, payload.decision, payload.comments
    )
    return HumanDecisionOut(**row)
