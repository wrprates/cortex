from __future__ import annotations

import json
import logging
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from docker.errors import ContainerError, ImageNotFound

from ..config import get_settings
from .docker_client import get_docker

logger = logging.getLogger(__name__)

Language = Literal["python", "r", "notebook"]


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    outputs_dir: Path
    timed_out: bool = False
    artifacts: list[Path] = field(default_factory=list)


def run_code(
    code: str,
    language: Language = "python",
    inputs: dict[str, bytes] | None = None,
    meta: dict | None = None,
    timeout: int | None = None,
    keep_workspace: bool = False,
) -> SandboxResult:
    """
    Spawna um container sandbox efêmero, executa o código, coleta output.

    Segurança: network_mode=none, sem mounts no host, limites de mem/cpu e timeout.
    """
    settings = get_settings()
    client = get_docker()

    workspace = Path(tempfile.mkdtemp(prefix="cortex-sbx-", dir="/tmp/sandbox"))
    (workspace / "inputs").mkdir(parents=True, exist_ok=True)
    (workspace / "outputs").mkdir(parents=True, exist_ok=True)

    script_name = {"python": "script.py", "r": "script.R", "notebook": "notebook.ipynb"}[language]
    (workspace / script_name).write_text(code, encoding="utf-8")

    if meta:
        (workspace / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    for filename, data in (inputs or {}).items():
        (workspace / "inputs" / filename).write_bytes(data)

    effective_timeout = timeout or settings.sandbox_timeout_seconds
    container_name = f"cortex-sbx-{uuid.uuid4().hex[:10]}"

    logger.info(
        "Spawning sandbox container=%s lang=%s timeout=%s mem=%s",
        container_name, language, effective_timeout, settings.sandbox_memory_limit,
    )

    try:
        try:
            client.images.get(settings.sandbox_image)
        except ImageNotFound as e:
            raise RuntimeError(
                f"Sandbox image {settings.sandbox_image!r} not found. "
                "Run: docker compose --profile tools build sandbox"
            ) from e

        container = client.containers.run(
            image=settings.sandbox_image,
            name=container_name,
            detach=True,
            network_mode="none",
            mem_limit=settings.sandbox_memory_limit,
            nano_cpus=int(settings.sandbox_cpu_limit * 1e9),
            pids_limit=256,
            read_only=False,
            volumes={str(workspace): {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            remove=False,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
        )

        timed_out = False
        try:
            result = container.wait(timeout=effective_timeout)
            exit_code = result.get("StatusCode", -1)
        except Exception as e:
            logger.warning("Sandbox timeout/error: %s", e)
            timed_out = True
            exit_code = -1
            try:
                container.kill()
            except Exception:
                pass

        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

        try:
            container.remove(force=True)
        except Exception as e:
            logger.warning("Failed to remove sandbox container: %s", e)

        outputs_dir = workspace / "outputs"
        artifacts = sorted(p for p in outputs_dir.rglob("*") if p.is_file())

        return SandboxResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            outputs_dir=outputs_dir,
            timed_out=timed_out,
            artifacts=artifacts,
        )
    except ContainerError as e:
        logger.exception("Sandbox container error")
        return SandboxResult(
            exit_code=e.exit_status,
            stdout="",
            stderr=str(e),
            outputs_dir=workspace / "outputs",
            timed_out=False,
        )
    finally:
        if not keep_workspace:
            # Mantém só em erro de debug se o chamador pedir.
            try:
                shutil.rmtree(workspace, ignore_errors=True)
            except Exception:
                pass
