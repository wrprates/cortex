from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from ..config import get_settings

logger = logging.getLogger(__name__)


async def create_client_repo(client_name: str, private: bool = True) -> str | None:
    """
    Cria um repositório GitHub para o cliente usando gh CLI.
    Retorna a URL do repositório ou None se falhar.
    """
    settings = get_settings()
    github_org = settings.github_org

    repo_name = _sanitize_repo_name(client_name)
    full_name = f"{github_org}/{repo_name}" if github_org else repo_name

    try:
        cmd = [
            "gh", "repo", "create", full_name,
            "--private" if private else "--public",
            "--description", f"Cortex DS projects for {client_name}",
            "--clone=false",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            repo_url = f"https://github.com/{full_name}"
            logger.info("Created GitHub repo: %s", repo_url)
            return repo_url
        else:
            logger.error("Failed to create repo: %s", result.stderr)
            return None
    except subprocess.TimeoutExpired:
        logger.error("Timeout creating GitHub repo")
        return None
    except FileNotFoundError:
        logger.error("gh CLI not found. Install: https://cli.github.com/")
        return None


async def push_analysis(
    github_repo: str,
    project_name: str,
    files: dict[str, bytes],
) -> bool:
    """
    Faz push de arquivos para uma subpasta do repositório do cliente.

    Args:
        github_repo: URL do repositório (ex: https://github.com/user/repo)
        project_name: Nome do projeto (será a subpasta)
        files: Dict de {nome_arquivo: conteúdo_bytes}

    Returns:
        True se sucesso, False se falhar.
    """
    settings = get_settings()
    github_token = settings.github_token

    if not github_token:
        logger.error("GITHUB_TOKEN not configured")
        return False

    project_folder = _sanitize_repo_name(project_name)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        try:
            # Clone repo
            repo_with_token = github_repo.replace(
                "https://github.com/",
                f"https://{github_token}@github.com/"
            )
            subprocess.run(
                ["git", "clone", "--depth=1", repo_with_token, "repo"],
                cwd=tmppath,
                capture_output=True,
                timeout=60,
            )

            repo_path = tmppath / "repo"
            project_path = repo_path / project_folder

            # Create project folder
            project_path.mkdir(parents=True, exist_ok=True)

            # Write files
            for filename, content in files.items():
                file_path = project_path / filename
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(content)

            # Git add, commit, push
            subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", f"Update {project_folder} analysis"],
                cwd=repo_path,
                capture_output=True,
            )
            result = subprocess.run(
                ["git", "push"],
                cwd=repo_path,
                capture_output=True,
                timeout=60,
            )

            if result.returncode == 0:
                logger.info("Pushed to %s/%s", github_repo, project_folder)
                return True
            else:
                logger.error("Push failed: %s", result.stderr.decode())
                return False

        except subprocess.TimeoutExpired:
            logger.error("Timeout during git operations")
            return False
        except Exception as e:
            logger.exception("Error pushing to GitHub: %s", e)
            return False


async def list_client_projects(github_repo: str) -> list[str]:
    """
    Lista subpastas (projetos) no repositório do cliente.
    """
    try:
        # Use gh api to list contents
        cmd = [
            "gh", "api",
            f"repos/{_extract_repo_path(github_repo)}/contents",
            "--jq", ".[].name",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            return [
                name.strip()
                for name in result.stdout.strip().split("\n")
                if name.strip()
            ]
        return []
    except Exception as e:
        logger.exception("Error listing projects: %s", e)
        return []


def _sanitize_repo_name(name: str) -> str:
    """Converte nome em slug válido para GitHub."""
    import re
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _extract_repo_path(url: str) -> str:
    """Extrai 'user/repo' de URL do GitHub."""
    url = url.rstrip("/")
    if url.startswith("https://github.com/"):
        return url.replace("https://github.com/", "")
    return url
