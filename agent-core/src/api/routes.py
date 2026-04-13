from fastapi import APIRouter, HTTPException

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
    raise HTTPException(status_code=501, detail="not implemented yet")


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str) -> ProjectOut:
    raise HTTPException(status_code=501, detail="not implemented yet")


@router.post("/runs", response_model=RunOut, status_code=201)
async def start_run(payload: RunStart) -> RunOut:
    raise HTTPException(status_code=501, detail="not implemented yet")


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: str) -> RunOut:
    raise HTTPException(status_code=501, detail="not implemented yet")


@router.post("/decisions", response_model=HumanDecisionOut, status_code=201)
async def record_decision(payload: HumanDecisionIn) -> HumanDecisionOut:
    raise HTTPException(status_code=501, detail="not implemented yet")
