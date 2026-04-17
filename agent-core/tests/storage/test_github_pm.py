"""
Testes unitários pros helpers de issue claim/release.

Estratégia: mock de httpx via respx. Sem rede, sem Docker, sem GitHub real.
Cobertura dos caminhos felizes + casos que mais importam (race, 404, 500).
"""
from __future__ import annotations

import httpx
import pytest
import respx

from src.storage import github_pm

REPO_URL = "https://github.com/wrprates/demo-repo"
ISSUES_URL = "https://api.github.com/repos/wrprates/demo-repo/issues"


# --------- get_issue_kind ---------


class TestGetIssueKind:
    @pytest.mark.parametrize(
        "labels, expected",
        [
            ([{"name": "cortex:quality"}], "quality"),
            ([{"name": "cortex:modeling"}, {"name": "cortex:in-progress"}], "modeling"),
            ([{"name": "cortex:in-progress"}, {"name": "bug"}], None),
            ([], None),
            # Duas kinds → alfa-first é determinístico; eda < modeling.
            ([{"name": "cortex:modeling"}, {"name": "cortex:eda"}], "eda"),
            ([{"name": "help-wanted"}], None),
            ([{"color": "red"}], None),
        ],
    )
    def test_extracts_kind_from_labels(self, labels, expected):
        assert github_pm.get_issue_kind({"labels": labels}) == expected

    def test_missing_labels_key(self):
        assert github_pm.get_issue_kind({}) is None


# --------- list_claimable_issues ---------


class TestListClaimableIssues:
    def test_returns_only_unclaimed_issues_with_kind_label(self, fake_settings):
        with respx.mock(assert_all_called=True) as rx:
            rx.get(ISSUES_URL).mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        # issue sem label cortex — descarta
                        {"number": 1, "labels": [{"name": "bug"}]},
                        # issue em andamento (tem in-progress) — descarta
                        {
                            "number": 2,
                            "labels": [
                                {"name": "cortex:quality"},
                                {"name": "cortex:in-progress"},
                            ],
                        },
                        # issue elegível — inclui
                        {
                            "number": 3,
                            "labels": [{"name": "cortex:eda"}],
                            "title": "fazer eda",
                        },
                    ],
                )
            )
            out = github_pm.list_claimable_issues(REPO_URL)
        assert [i["number"] for i in out] == [3]

    def test_filters_pull_requests(self, fake_settings):
        """
        GET /issues devolve PRs no mesmo namespace — identificáveis pela
        presença da chave `pull_request`. Devem ser filtrados.
        """
        with respx.mock() as rx:
            rx.get(ISSUES_URL).mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {
                            "number": 10,
                            "labels": [{"name": "cortex:quality"}],
                            "pull_request": {"url": "..."},
                        },
                        {
                            "number": 11,
                            "labels": [{"name": "cortex:quality"}],
                        },
                    ],
                )
            )
            out = github_pm.list_claimable_issues(REPO_URL)
        assert [i["number"] for i in out] == [11]

    def test_returns_empty_on_api_error(self, fake_settings):
        with respx.mock() as rx:
            rx.get(ISSUES_URL).mock(
                return_value=httpx.Response(500, text="boom")
            )
            assert github_pm.list_claimable_issues(REPO_URL) == []

    def test_returns_empty_when_no_issues(self, fake_settings):
        with respx.mock() as rx:
            rx.get(ISSUES_URL).mock(return_value=httpx.Response(200, json=[]))
            assert github_pm.list_claimable_issues(REPO_URL) == []


# --------- claim_issue ---------


class TestClaimIssue:
    ISSUE_URL = "https://api.github.com/repos/wrprates/demo-repo/issues/42"
    LABELS_URL = "https://api.github.com/repos/wrprates/demo-repo/issues/42/labels"
    COMMENTS_URL = "https://api.github.com/repos/wrprates/demo-repo/issues/42/comments"

    def test_claim_succeeds_when_issue_is_free(self, fake_settings):
        with respx.mock() as rx:
            rx.get(self.ISSUE_URL).mock(
                return_value=httpx.Response(
                    200, json={"labels": [{"name": "cortex:quality"}]}
                )
            )
            label_post = rx.post(self.LABELS_URL).mock(
                return_value=httpx.Response(200, json=[])
            )
            comment_post = rx.post(self.COMMENTS_URL).mock(
                return_value=httpx.Response(201, json={})
            )

            assert github_pm.claim_issue(REPO_URL, 42) is True
            assert label_post.called
            assert comment_post.called

    def test_claim_skips_when_already_in_progress(self, fake_settings):
        """Race check: se outro agente já claimou, não sobrescreve."""
        # assert_all_called=False porque o propósito é justamente verificar
        # que a rota POST /labels NÃO foi chamada.
        with respx.mock(assert_all_called=False) as rx:
            rx.get(self.ISSUE_URL).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "labels": [
                            {"name": "cortex:quality"},
                            {"name": "cortex:in-progress"},
                        ]
                    },
                )
            )
            label_post = rx.post(self.LABELS_URL).mock(
                return_value=httpx.Response(200)
            )

            assert github_pm.claim_issue(REPO_URL, 42) is False
            assert not label_post.called, "não deveria adicionar label quando já claimed"

    def test_claim_fails_on_read_error(self, fake_settings):
        with respx.mock() as rx:
            rx.get(self.ISSUE_URL).mock(return_value=httpx.Response(404))
            assert github_pm.claim_issue(REPO_URL, 42) is False

    def test_claim_fails_if_label_post_errors(self, fake_settings):
        with respx.mock() as rx:
            rx.get(self.ISSUE_URL).mock(
                return_value=httpx.Response(200, json={"labels": []})
            )
            rx.post(self.LABELS_URL).mock(return_value=httpx.Response(500))
            assert github_pm.claim_issue(REPO_URL, 42) is False

    def test_claim_succeeds_even_if_comment_fails(self, fake_settings):
        """Comentário de lease é best-effort — não invalida o claim."""
        with respx.mock() as rx:
            rx.get(self.ISSUE_URL).mock(
                return_value=httpx.Response(200, json={"labels": []})
            )
            rx.post(self.LABELS_URL).mock(return_value=httpx.Response(200, json=[]))
            rx.post(self.COMMENTS_URL).mock(return_value=httpx.Response(500))

            assert github_pm.claim_issue(REPO_URL, 42) is True


# --------- release_claim ---------


class TestReleaseClaim:
    LABEL_URL = (
        "https://api.github.com/repos/wrprates/demo-repo/issues/42/labels/"
        "cortex:in-progress"
    )

    def test_release_succeeds(self, fake_settings):
        with respx.mock() as rx:
            rx.delete(self.LABEL_URL).mock(return_value=httpx.Response(200))
            assert github_pm.release_claim(REPO_URL, 42) is True

    def test_release_is_idempotent_when_label_missing(self, fake_settings):
        """
        404 significa que a label já não estava lá. Retry depois de um
        release parcial não deve falhar — tratamos como sucesso.
        """
        with respx.mock() as rx:
            rx.delete(self.LABEL_URL).mock(return_value=httpx.Response(404))
            assert github_pm.release_claim(REPO_URL, 42) is True

    def test_release_fails_on_server_error(self, fake_settings):
        with respx.mock() as rx:
            rx.delete(self.LABEL_URL).mock(return_value=httpx.Response(500))
            assert github_pm.release_claim(REPO_URL, 42) is False
