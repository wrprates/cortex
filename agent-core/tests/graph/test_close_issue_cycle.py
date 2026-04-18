"""
Testes do helper `_close_issue_driven_cycle` (sprint 1/B4).

O helper fecha o ciclo da issue-driver no GitHub ao fim de um run teammate:
release do claim sempre, close da issue só no happy path. Pedaço pequeno
e crítico — se trava, bloqueia re-tick da mesma issue.
"""
from __future__ import annotations

import pytest

from src.graph import nodes

REPO = "https://github.com/wrprates/demo-repo"


@pytest.fixture
def gh_calls(monkeypatch):
    """
    Captura chamadas a github_pm.release_claim / close_issue sem tocar rede.
    Retorna {release: [...], close: [...]}.
    """
    calls = {"release": [], "close": []}

    def fake_release(repo, num):
        calls["release"].append((repo, num))
        return True

    def fake_close(repo, num, reason="completed"):
        calls["close"].append((repo, num, reason))
        return True

    # Patch no módulo importado localmente dentro do helper.
    from src.storage import github_pm as real_pm

    monkeypatch.setattr(real_pm, "release_claim", fake_release)
    monkeypatch.setattr(real_pm, "close_issue", fake_close)
    return calls


class TestCloseIssueDrivenCycle:
    def test_happy_path_releases_and_closes(self, gh_calls):
        state = {"issue_number": 42}

        nodes._close_issue_driven_cycle(state, REPO, push_ok=True)

        assert gh_calls["release"] == [(REPO, 42)]
        assert gh_calls["close"] == [(REPO, 42, "completed")]

    def test_push_failed_only_releases_claim(self, gh_calls):
        """
        Se o push falhou, a issue fica aberta pra outro tick pegar. Release
        sempre pra não travar o backlog em `cortex:in-progress`.
        """
        state = {"issue_number": 42}

        nodes._close_issue_driven_cycle(state, REPO, push_ok=False)

        assert gh_calls["release"] == [(REPO, 42)]
        assert gh_calls["close"] == []

    def test_noop_when_not_issue_driven(self, gh_calls):
        """Run legado via POST /v1/runs: state não tem issue_number."""
        nodes._close_issue_driven_cycle({}, REPO, push_ok=True)

        assert gh_calls["release"] == []
        assert gh_calls["close"] == []

    def test_noop_when_issue_number_is_none(self, gh_calls):
        state = {"issue_number": None}

        nodes._close_issue_driven_cycle(state, REPO, push_ok=True)

        assert gh_calls["release"] == []
        assert gh_calls["close"] == []

    def test_swallows_release_exception(self, monkeypatch):
        """
        Falha na rede não deve propagar pra cima — o run já fez o trabalho
        útil. Humano limpa manualmente se precisar.
        """
        from src.storage import github_pm as real_pm

        def fake_release(*_args, **_kw):
            raise RuntimeError("rede caiu")

        monkeypatch.setattr(real_pm, "release_claim", fake_release)
        monkeypatch.setattr(real_pm, "close_issue", lambda *a, **k: True)

        # Não deve levantar.
        nodes._close_issue_driven_cycle(
            {"issue_number": 42}, REPO, push_ok=True
        )

    def test_issue_number_as_string_is_accepted(self, gh_calls):
        """
        Defensivo: LangGraph serializa/deserializa state; se `issue_number`
        voltar como string, a conversão pra int precisa funcionar.
        """
        nodes._close_issue_driven_cycle(
            {"issue_number": "42"}, REPO, push_ok=True
        )

        assert gh_calls["release"] == [(REPO, 42)]
        assert gh_calls["close"] == [(REPO, 42, "completed")]
