"""
GitHub como sistema de gestão: milestones, issues e PRs.

Sync por design — chamado de dentro de nós do grafo LangGraph (que rodam em
thread via services/runs.py). Nenhum `gh` CLI: só REST API via httpx.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

_BASE = "https://api.github.com"

# Labels convencionais do teammate mode (ver docs/PLAN_TEAMMATE_MODE.md).
# Uma das labels `cortex:<kind>` identifica a etapa a executar;
# `cortex:in-progress` sinaliza que uma execução está ativa na issue.
_KIND_LABELS = {"cortex:quality", "cortex:eda", "cortex:modeling", "cortex:review"}
_CLAIM_LABEL = "cortex:in-progress"


def _headers() -> dict[str, str]:
    settings = get_settings()
    token = settings.github_token
    if not token:
        raise RuntimeError("GITHUB_TOKEN não configurado")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo_path(repo_url: str) -> str:
    """Extrai 'owner/name' de uma URL do tipo https://github.com/owner/name."""
    url = repo_url.rstrip("/")
    if url.startswith("https://github.com/"):
        return url[len("https://github.com/"):]
    raise ValueError(f"URL de repo GitHub inesperada: {repo_url}")


def _client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers=_headers())


def ensure_milestone(repo_url: str, title: str, description: str = "") -> int | None:
    """
    Retorna o número do milestone com esse título, criando se não existir.

    Idempotente: se já existe, retorna o número existente sem erro.
    """
    repo = _repo_path(repo_url)
    with _client() as http:
        r = http.get(f"{_BASE}/repos/{repo}/milestones", params={"state": "all", "per_page": 100})
        if r.status_code == 200:
            for m in r.json():
                if m.get("title") == title:
                    return m.get("number")

        r = http.post(
            f"{_BASE}/repos/{repo}/milestones",
            json={"title": title, "description": description, "state": "open"},
        )
        if r.status_code == 201:
            num = r.json().get("number")
            logger.info("milestone criado: %s #%s", title, num)
            return num
        logger.error(
            "falha criando milestone %s: %s %s", title, r.status_code, r.text[:300]
        )
        return None


def create_issue(
    repo_url: str,
    title: str,
    body: str,
    *,
    milestone: int | None = None,
    labels: list[str] | None = None,
) -> int | None:
    """Cria uma issue e retorna o número; None em erro."""
    repo = _repo_path(repo_url)
    payload: dict[str, Any] = {"title": title, "body": body}
    if milestone is not None:
        payload["milestone"] = milestone
    if labels:
        payload["labels"] = labels

    with _client() as http:
        r = http.post(f"{_BASE}/repos/{repo}/issues", json=payload)
        if r.status_code == 201:
            num = r.json().get("number")
            logger.info("issue criada: %s #%s", title, num)
            return num
        logger.error(
            "falha criando issue %s: %s %s", title, r.status_code, r.text[:300]
        )
        return None


def create_pr(
    repo_url: str,
    *,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool = False,
) -> dict | None:
    """Abre um PR. Retorna {number, url, html_url} ou None em erro."""
    repo = _repo_path(repo_url)
    payload = {"title": title, "body": body, "head": head, "base": base, "draft": draft}

    with _client(timeout=45.0) as http:
        r = http.post(f"{_BASE}/repos/{repo}/pulls", json=payload)
        if r.status_code == 201:
            j = r.json()
            info = {"number": j["number"], "url": j["url"], "html_url": j["html_url"]}
            logger.info("PR aberto: %s", info["html_url"])
            return info
        # Já existe? (422 "A pull request already exists")
        if r.status_code == 422 and "already exists" in r.text.lower():
            logger.info("PR já existente pra %s → %s; tentando recuperar", head, base)
            rlist = http.get(
                f"{_BASE}/repos/{repo}/pulls",
                params={"head": f"{repo.split('/')[0]}:{head}", "base": base, "state": "open"},
            )
            if rlist.status_code == 200 and rlist.json():
                j = rlist.json()[0]
                return {"number": j["number"], "url": j["url"], "html_url": j["html_url"]}
        logger.error("falha criando PR: %s %s", r.status_code, r.text[:300])
        return None


def comment_issue(repo_url: str, issue_number: int, body: str) -> bool:
    """
    Posta um comentário numa issue ou PR (GitHub compartilha namespace).
    """
    repo = _repo_path(repo_url)
    with _client() as http:
        r = http.post(
            f"{_BASE}/repos/{repo}/issues/{issue_number}/comments", json={"body": body}
        )
        if r.status_code == 201:
            return True
        logger.error(
            "falha comentando #%s: %s %s", issue_number, r.status_code, r.text[:300]
        )
        return False


def comment_pr(repo_url: str, pr_number: int, body: str) -> bool:
    """Alias de `comment_issue` pra callers legacy focados em PR."""
    return comment_issue(repo_url, pr_number, body)


def close_issue(
    repo_url: str, number: int, reason: str = "completed"
) -> bool:
    """
    Fecha uma issue explicitamente. `reason` aceita 'completed' ou 'not_planned'.

    Usado para garantir fechamento fase-a-fase, sem depender do merge final
    de um PR com `Closes #N` (que só dispara após merge pra main).
    """
    repo = _repo_path(repo_url)
    with _client() as http:
        r = http.patch(
            f"{_BASE}/repos/{repo}/issues/{number}",
            json={"state": "closed", "state_reason": reason},
        )
        if r.status_code == 200:
            logger.info("issue fechada: #%s (%s)", number, reason)
            return True
        logger.error(
            "falha fechando issue #%s: %s %s", number, r.status_code, r.text[:300]
        )
        return False


def update_pr_body(repo_url: str, pr_number: int, body: str) -> bool:
    """Atualiza corpo do PR (útil pra refletir progresso do run)."""
    repo = _repo_path(repo_url)
    with _client() as http:
        r = http.patch(
            f"{_BASE}/repos/{repo}/pulls/{pr_number}", json={"body": body}
        )
        return r.status_code == 200


def get_issue_kind(issue: dict) -> str | None:
    """
    Extrai a 'kind' de uma issue pelas labels `cortex:<kind>` convencionais.
    Retorna None se nenhuma label kind for encontrada.

    Se houver mais de uma label kind na mesma issue, retorna a primeira em
    ordem alfabética — comportamento determinístico pra evitar flakiness.
    """
    names = sorted(
        (lb.get("name") or "") for lb in (issue.get("labels") or [])
    )
    for name in names:
        if name in _KIND_LABELS:
            return name.split(":", 1)[1]
    return None


def list_claimable_issues(repo_url: str) -> list[dict]:
    """
    Lista issues abertas do repo elegíveis pra claim pelo Cortex.

    Critério: estão `open`, têm pelo menos uma label `cortex:<kind>` e **não**
    têm a label `cortex:in-progress`. Pull requests (que compartilham namespace
    de issues) são filtrados — só issues puras.

    Ordenadas por `updated_at` ascendente (mais antigas primeiro, pra evitar
    starvation de issues que ficaram pra trás).
    """
    repo = _repo_path(repo_url)
    with _client() as http:
        # Pedimos explicitamente issues com alguma label kind OR in-progress
        # e filtramos local; a API não suporta filtro "com label X sem label Y".
        r = http.get(
            f"{_BASE}/repos/{repo}/issues",
            params={"state": "open", "per_page": 100, "sort": "updated", "direction": "asc"},
        )
        if r.status_code != 200:
            logger.error(
                "list_claimable_issues falhou: %s %s",
                r.status_code, r.text[:300],
            )
            return []

        out: list[dict] = []
        for issue in r.json():
            if issue.get("pull_request"):
                continue  # PRs entram no endpoint /issues; ignorar
            label_names = {(lb.get("name") or "") for lb in issue.get("labels") or []}
            if _CLAIM_LABEL in label_names:
                continue
            if not (label_names & _KIND_LABELS):
                continue
            out.append(issue)
        return out


def claim_issue(
    repo_url: str,
    issue_number: int,
    *,
    lease_minutes: int = 30,
    actor: str = "cortex",
) -> bool:
    """
    Tenta reivindicar uma issue adicionando a label `cortex:in-progress`.

    Protocolo:
      1. Releia a issue. Se já tem `cortex:in-progress`, retorna False (outro
         já claimou); não sobrescreve trabalho em curso.
      2. Senão, adiciona a label via POST /labels (atômico do ponto de vista
         da API do GitHub — idempotente se mesma label voltar, mas útil pra
         nós porque a etapa 1 acabou de verificar que não existia).
      3. Posta comentário com `lease-until: <iso-ts>` pro sweeper referenciar.

    Nota sobre concorrência: existe janela pequena (~100ms) entre ler e
    gravar em que dois claimers simultâneos podem colidir. Pra v0.9 aceitamos
    o risco; sprint 2 do plano endurece com heartbeat e conditional request.
    """
    from datetime import datetime, timedelta, timezone

    repo = _repo_path(repo_url)
    lease_until = (
        datetime.now(timezone.utc) + timedelta(minutes=lease_minutes)
    ).isoformat()

    with _client() as http:
        # 1. Recheca antes de gravar
        r = http.get(f"{_BASE}/repos/{repo}/issues/{issue_number}")
        if r.status_code != 200:
            logger.error(
                "claim_issue: falha lendo #%s: %s",
                issue_number, r.status_code,
            )
            return False
        issue = r.json()
        current = {(lb.get("name") or "") for lb in issue.get("labels") or []}
        if _CLAIM_LABEL in current:
            logger.info("claim_issue: #%s já claimed, desistindo", issue_number)
            return False

        # 2. Adiciona label
        r = http.post(
            f"{_BASE}/repos/{repo}/issues/{issue_number}/labels",
            json={"labels": [_CLAIM_LABEL]},
        )
        if r.status_code not in (200, 201):
            logger.error(
                "claim_issue: falha adicionando label em #%s: %s %s",
                issue_number, r.status_code, r.text[:300],
            )
            return False

        # 3. Comentário com lease. Falhas aqui não invalidam o claim — só
        # dificultam o trabalho do sweeper de recuperar issues órfãs.
        comment = (
            f"🤖 **{actor}** pegou essa issue.\n\n"
            f"- lease-until: `{lease_until}`\n"
            f"- para liberar manualmente: remova a label `{_CLAIM_LABEL}`."
        )
        rc = http.post(
            f"{_BASE}/repos/{repo}/issues/{issue_number}/comments",
            json={"body": comment},
        )
        if rc.status_code != 201:
            logger.warning(
                "claim_issue: label OK mas comentário falhou em #%s: %s",
                issue_number, rc.status_code,
            )

        logger.info("claim_issue: #%s claimed (lease_until=%s)", issue_number, lease_until)
        return True


def release_claim(repo_url: str, issue_number: int) -> bool:
    """
    Libera uma issue removendo a label `cortex:in-progress`.

    Idempotente: se a label não estiver presente, considera sucesso (nada a
    fazer). Retorna False só em erro de rede ou permissão.
    """
    repo = _repo_path(repo_url)
    with _client() as http:
        r = http.delete(
            f"{_BASE}/repos/{repo}/issues/{issue_number}/labels/{_CLAIM_LABEL}"
        )
        # 200: removida. 404: já não estava — tratamos como sucesso pra
        # manter o release idempotente entre retries.
        if r.status_code in (200, 404):
            logger.info("release_claim: #%s liberada", issue_number)
            return True
        logger.error(
            "release_claim: falha em #%s: %s %s",
            issue_number, r.status_code, r.text[:300],
        )
        return False


def mark_pr_ready(repo_url: str, pr_number: int) -> bool:
    """
    Tira o PR de draft via GraphQL (REST não expõe o toggle de ready).

    Uso: ao fim do run (node_report), transforma o draft PR em ready-for-review.
    Retorna True em sucesso, False se falhar (não é crítico — log só).
    """
    # Descobre o node_id do PR
    repo = _repo_path(repo_url)
    with _client() as http:
        r = http.get(f"{_BASE}/repos/{repo}/pulls/{pr_number}")
        if r.status_code != 200:
            logger.error("mark_pr_ready: falha buscando PR #%s: %s", pr_number, r.status_code)
            return False
        node_id = r.json().get("node_id")
        if not node_id:
            return False

        mutation = """
        mutation($id: ID!) {
          markPullRequestReadyForReview(input: {pullRequestId: $id}) {
            pullRequest { isDraft }
          }
        }
        """
        gql = http.post(
            "https://api.github.com/graphql",
            json={"query": mutation, "variables": {"id": node_id}},
        )
        if gql.status_code == 200 and "errors" not in gql.json():
            logger.info("PR #%s marcado como ready-for-review", pr_number)
            return True
        logger.error("mark_pr_ready GraphQL falhou: %s %s", gql.status_code, gql.text[:300])
        return False
