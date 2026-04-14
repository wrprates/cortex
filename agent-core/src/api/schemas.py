from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class WorkflowType(str, Enum):
    DATA_QUALITY = "data_quality"
    EDA_HYPOTHESIS = "eda_hypothesis"
    FULL_ML = "full_ml"


class PrimaryLanguage(str, Enum):
    R = "r"
    PYTHON = "python"


class ClientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    github_repo: str | None = None
    auto_create_repo: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class ClientOut(BaseModel):
    id: UUID
    name: str
    github_repo: str | None
    config: dict[str, Any]
    created_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    client_id: UUID | None = None
    workflow_type: WorkflowType = WorkflowType.FULL_ML
    primary_language: PrimaryLanguage = PrimaryLanguage.R
    config: dict[str, Any] = Field(default_factory=dict)


class ProjectOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    client_id: UUID | None
    workflow_type: str
    primary_language: str
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
