from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.postgres_dsn,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as s:
        yield s


async def insert_project(name: str, description: str | None, config: dict[str, Any]) -> dict:
    async with session() as s:
        result = await s.execute(
            text(
                """
                INSERT INTO projects (name, description, config)
                VALUES (:name, :description, CAST(:config AS JSONB))
                RETURNING id, name, description, status, config, created_at, updated_at
                """
            ),
            {"name": name, "description": description, "config": _json(config)},
        )
        row = result.mappings().one()
        await s.commit()
        return dict(row)


async def fetch_project(project_id: UUID) -> dict | None:
    async with session() as s:
        result = await s.execute(
            text(
                "SELECT id, name, description, status, config, created_at, updated_at "
                "FROM projects WHERE id = :id"
            ),
            {"id": project_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None


async def insert_run(project_id: UUID) -> dict:
    async with session() as s:
        result = await s.execute(
            text(
                """
                INSERT INTO runs (project_id)
                VALUES (:project_id)
                RETURNING id, project_id, status, started_at, finished_at
                """
            ),
            {"project_id": project_id},
        )
        row = result.mappings().one()
        await s.commit()
        return dict(row)


async def fetch_run(run_id: UUID) -> dict | None:
    async with session() as s:
        result = await s.execute(
            text(
                "SELECT id, project_id, status, started_at, finished_at "
                "FROM runs WHERE id = :id"
            ),
            {"id": run_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None


async def insert_human_decision(
    run_id: UUID,
    task_id: UUID | None,
    decision: str,
    comments: str | None,
) -> dict:
    async with session() as s:
        result = await s.execute(
            text(
                """
                INSERT INTO human_decisions (run_id, task_id, decision, comments)
                VALUES (:run_id, :task_id, :decision, :comments)
                RETURNING id, run_id, task_id, decision, comments, decided_at
                """
            ),
            {
                "run_id": run_id,
                "task_id": task_id,
                "decision": decision,
                "comments": comments,
            },
        )
        row = result.mappings().one()
        await s.commit()
        return dict(row)


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str)
