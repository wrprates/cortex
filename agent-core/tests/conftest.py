"""
Fixtures compartilhadas dos testes do agent-core.

Foco: deixar módulos que dependem de `get_settings()` testáveis sem precisar
do stack Docker (Postgres, MinIO, etc.). Cada teste substitui só o que
precisa — fixtures são enxutas de propósito.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def fake_settings(monkeypatch):
    """
    Substitui `src.config.get_settings` por um namespace com os campos usados
    pelos módulos sob teste. Cobre apenas o necessário pra storage/github_pm.
    """
    settings = SimpleNamespace(
        github_token="test-token-abc123",
        github_org="wrprates",
    )

    def _fake_get_settings():
        return settings

    # Patch no próprio módulo que importa get_settings
    import src.config

    monkeypatch.setattr(src.config, "get_settings", _fake_get_settings)
    return settings
