from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ProjectOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    status: str
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class RunStart(BaseModel):
    project_id: UUID
    datasets: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class RunOut(BaseModel):
    id: UUID
    project_id: UUID
    status: str
    started_at: datetime
    finished_at: datetime | None = None


class HumanDecisionIn(BaseModel):
    run_id: UUID
    task_id: UUID | None = None
    decision: str  # "approved" | "rejected"
    comments: str | None = None


class HumanDecisionOut(BaseModel):
    id: UUID
    run_id: UUID
    task_id: UUID | None
    decision: str
    comments: str | None
    decided_at: datetime
