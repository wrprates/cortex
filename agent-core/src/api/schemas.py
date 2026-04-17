from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class WorkflowType(str, Enum):
    DATA_QUALITY = "data_quality"
    EDA_HYPOTHESIS = "eda_hypothesis"
    FULL_ML = "full_ml"


class ClientCreate(BaseModel):
    """
    Dois modos mutuamente exclusivos:

    - **criar** (`auto_create_repo=True`, `github_repo=None`): Cortex cria um
      repositório novo no GitHub com o nome do cliente.
    - **continuar** (`auto_create_repo=False`, `github_repo=<url>`): apenas
      registra o cliente sobre um repo já existente.

    Qualquer outra combinação é rejeitada com 400 — intenção precisa ser
    explícita pra evitar criar repo "sem querer" quando o humano só queria
    continuar trabalho existente.
    """

    name: str = Field(..., min_length=1, max_length=200)
    github_repo: str | None = None
    auto_create_repo: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_repo_mode(self) -> "ClientCreate":
        if self.auto_create_repo and self.github_repo:
            raise ValueError(
                "auto_create_repo=true com github_repo fornecido é ambíguo: "
                "use auto_create_repo=false para continuar num repo existente, "
                "ou omita github_repo para criar um novo."
            )
        if not self.auto_create_repo and not self.github_repo:
            raise ValueError(
                "auto_create_repo=false exige github_repo (URL do repo existente)."
            )
        return self


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
    config: dict[str, Any] = Field(default_factory=dict)


class ProjectOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    client_id: UUID | None
    workflow_type: str
    primary_language: str | None = None  # mantido p/ backward-compat com registros antigos
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


class BacklogItem(BaseModel):
    """Item retornado pelo endpoint de backlog (issue claimável pelo Cortex)."""

    issue_number: int
    title: str
    kind: str  # quality | eda | modeling | review
    url: str
    updated_at: datetime
    labels: list[str]


class BacklogOut(BaseModel):
    project_id: UUID
    github_repo: str
    items: list[BacklogItem]


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
