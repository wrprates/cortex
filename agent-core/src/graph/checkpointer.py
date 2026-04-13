from __future__ import annotations

import logging
from functools import lru_cache

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

from ..config import get_settings

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_saver: PostgresSaver | None = None


def get_checkpointer() -> PostgresSaver:
    global _pool, _saver
    if _saver is not None:
        return _saver

    settings = get_settings()
    _pool = ConnectionPool(
        conninfo=settings.postgres_sync_dsn,
        max_size=10,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    _saver = PostgresSaver(_pool)
    _saver.setup()
    logger.info("PostgresSaver initialized")
    return _saver
