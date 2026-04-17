from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger(__name__)


async def create_client_repo(client_name: str, private: bool = True) -> str | None:
    """
    Cria um repositório GitHub para o cliente via REST API.
    Idempotente: se já existir, retorna a URL existente.
    """
    import httpx

    settings = get_settings()
    token = settings.github_token
    org = settings.github_org
    if not token:
        logger.error("GITHUB_TOKEN not configured")
        return None

    repo_name = _sanitize_repo_name(client_name)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {
        "name": repo_name,
        "description": f"Cortex DS projects for {client_name}",
        "private": private,
        "auto_init": True,
    }

    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.post(
            "https://api.github.com/user/repos", headers=headers, json=payload
        )
        if r.status_code == 201:
            url = r.json()["html_url"]
            logger.info("Created GitHub repo: %s", url)
            return url
        if r.status_code == 422 and "already exists" in r.text.lower():
            url = (
                f"https://github.com/{org}/{repo_name}"
                if org
                else f"https://github.com/{repo_name}"
            )
            logger.info("Repo already exists (idempotent): %s", url)
            return url
        logger.error("Failed to create repo (%s): %s", r.status_code, r.text[:300])
        return None


def push_to_branch(
    github_repo: str,
    branch_name: str,
    files: dict[str, bytes],
    *,
    commit_message: str,
    base_branch: str = "main",
) -> dict:
    """
    Escreve `files` na raiz de um branch novo em `github_repo` e dá push.

    - Clona o repo inteiro (sem --depth=1: precisamos de histórico pra branchar).
    - Faz checkout de `base_branch` e cria `branch_name` a partir dele.
    - Arquivos são escritos com o path relativo tal como está nas chaves do dict
      (ex: "R/01_analyst.R", "outputs/report.html", "README.md").
    - Commita e dá push da branch (set-upstream).

    Returns:
      {"ok": bool, "branch": str, "error": str|None}
    """
    settings = get_settings()
    token = settings.github_token
    if not token:
        return {"ok": False, "branch": branch_name, "error": "GITHUB_TOKEN não configurado"}

    auth_url = github_repo.replace(
        "https://github.com/", f"https://{token}@github.com/"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "repo"

        clone = subprocess.run(
            ["git", "clone", auth_url, str(repo_path)],
            capture_output=True, timeout=120,
        )
        if clone.returncode != 0:
            err = clone.stderr.decode("utf-8", errors="replace")[-500:]
            logger.error("git clone falhou: %s", err)
            return {"ok": False, "branch": branch_name, "error": f"clone: {err}"}

        # Identity (sem fallback: precisamos commitar)
        subprocess.run(
            ["git", "config", "user.email", "cortex@cortex.local"],
            cwd=repo_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Cortex Agent"],
            cwd=repo_path, capture_output=True, check=True,
        )

        # Se o branch já existe no remote, faz checkout dele (preserva commits
        # anteriores do mesmo run). Senão, cria a partir do base.
        ls_remote = subprocess.run(
            ["git", "ls-remote", "--exit-code", "origin",
             f"refs/heads/{branch_name}"],
            cwd=repo_path, capture_output=True,
        )
        branch_exists = ls_remote.returncode == 0

        if branch_exists:
            fetch = subprocess.run(
                ["git", "fetch", "origin", branch_name],
                cwd=repo_path, capture_output=True,
            )
            if fetch.returncode != 0:
                err = fetch.stderr.decode("utf-8", errors="replace")[-500:]
                return {"ok": False, "branch": branch_name, "error": f"fetch: {err}"}
            co = subprocess.run(
                ["git", "checkout", branch_name],
                cwd=repo_path, capture_output=True,
            )
            if co.returncode != 0:
                err = co.stderr.decode("utf-8", errors="replace")[-500:]
                return {"ok": False, "branch": branch_name, "error": f"checkout existing: {err}"}
        else:
            checkout_base = subprocess.run(
                ["git", "checkout", base_branch],
                cwd=repo_path, capture_output=True,
            )
            if checkout_base.returncode != 0:
                logger.warning(
                    "checkout %s falhou (%s); tentando master como fallback",
                    base_branch, checkout_base.stderr.decode()[-200:],
                )
                subprocess.run(["git", "checkout", "master"], cwd=repo_path, capture_output=True)

            branch_new = subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=repo_path, capture_output=True,
            )
            if branch_new.returncode != 0:
                err = branch_new.stderr.decode("utf-8", errors="replace")[-500:]
                return {"ok": False, "branch": branch_name, "error": f"checkout -b: {err}"}

        # Grava arquivos
        for rel_path, content in files.items():
            full = repo_path / rel_path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(content)

        subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True, check=True)

        commit = subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=repo_path, capture_output=True,
        )
        if commit.returncode != 0:
            msg = commit.stdout.decode() + commit.stderr.decode()
            if "nothing to commit" in msg:
                logger.info("nada pra commitar em %s — branch igual à base", branch_name)
                return {"ok": True, "branch": branch_name, "error": None, "no_changes": True}
            err = msg[-500:]
            return {"ok": False, "branch": branch_name, "error": f"commit: {err}"}

        push = subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            cwd=repo_path, capture_output=True, timeout=120,
        )
        if push.returncode != 0:
            err = push.stderr.decode("utf-8", errors="replace")[-500:]
            return {"ok": False, "branch": branch_name, "error": f"push: {err}"}

        logger.info("push ok: %s branch=%s", github_repo, branch_name)
        return {"ok": True, "branch": branch_name, "error": None}


def _sanitize_repo_name(name: str) -> str:
    import re

    slug = name.lower()
    slug = re.sub(r"[^a-z0-9\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")
