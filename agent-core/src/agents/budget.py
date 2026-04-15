"""
Cap de custo em tokens por run. Scope-per-thread via ContextVar — cada thread
(cada run disparado pela API) tem seu próprio budget isolado.

Uso:

    with run_budget(max_tokens=500_000) as budget:
        call_llm(...)            # auto-incrementa
        budget.check()           # raise TokenBudgetExceeded se passou
        total = budget.used      # leitura
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class TokenBudgetExceeded(RuntimeError):
    """Run consumiu mais tokens que o limite configurado."""


@dataclass
class _Budget:
    limit: int
    used: int = 0

    def add(self, tokens_in: int, tokens_out: int) -> None:
        self.used += tokens_in + tokens_out

    def check(self) -> None:
        if self.used > self.limit:
            raise TokenBudgetExceeded(
                f"run excedeu cap de tokens: {self.used} > {self.limit}"
            )


_current: ContextVar["_Budget | None"] = ContextVar("cortex_token_budget", default=None)


@contextmanager
def run_budget(max_tokens: int):
    """Ativa um orçamento para o thread/task atual."""
    b = _Budget(limit=max_tokens)
    token = _current.set(b)
    try:
        logger.info("token budget ativo: limite=%d", max_tokens)
        yield b
    finally:
        _current.reset(token)
        logger.info("token budget final: %d / %d", b.used, b.limit)


def record(tokens_in: int, tokens_out: int) -> None:
    """Registra uso no budget ativo (no-op se nenhum budget ativo)."""
    b = _current.get()
    if b is None:
        return
    b.add(tokens_in, tokens_out)
    if b.used > b.limit:
        raise TokenBudgetExceeded(
            f"run excedeu cap de tokens: {b.used} > {b.limit}"
        )


def current_usage() -> tuple[int, int] | None:
    """(used, limit) do budget ativo, ou None."""
    b = _current.get()
    return (b.used, b.limit) if b else None
