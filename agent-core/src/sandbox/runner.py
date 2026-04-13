from __future__ import annotations

import json
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from docker.errors import ContainerError, ImageNotFound

from ..config import get_settings
from .docker_client import get_docker

logger = logging.getLogger(__name__)

Language = Literal["python", "r", "notebook"]

SANDBOX_ROOT_IN_SANDBOX = "/sandbox_root"


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
    Spawna container sandbox efêmero em um subdiretório de uma named volume
    compartilhada entre agent-core e sandbox.

    Portabilidade: usamos named volume (não bind mount do host) porque o Docker
    daemon que spawna o sandbox enxerga paths do HOST, não do agent-core.
    Named volume é resolvida pelo daemon igual no Mac e no Linux.
    """
    settings = get_settings()
    client = get_docker()

    run_id = uuid.uuid4().hex[:12]

    # Path visto de dentro do agent-core (onde a named volume está montada):
    workspace_in_core = Path(settings.sandbox_shared_mount) / run_id
    (workspace_in_core / "inputs").mkdir(parents=True, exist_ok=True)
    (workspace_in_core / "outputs").mkdir(parents=True, exist_ok=True)

    # Path visto de dentro do container sandbox (working_dir):
    workspace_in_sandbox = f"{SANDBOX_ROOT_IN_SANDBOX}/{run_id}"

    script_name = {"python": "script.py", "r": "script.R", "notebook": "notebook.ipynb"}[language]
    (workspace_in_core / script_name).write_text(code, encoding="utf-8")

    if meta:
        (workspace_in_core / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    for filename, data in (inputs or {}).items():
        (workspace_in_core / "inputs" / filename).write_bytes(data)

    effective_timeout = timeout or settings.sandbox_timeout_seconds
    container_name = f"cortex-sbx-{run_id}"

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
            volumes={
                settings.sandbox_shared_volume: {
                    "bind": SANDBOX_ROOT_IN_SANDBOX,
                    "mode": "rw",
                }
            },
            working_dir=workspace_in_sandbox,
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

        outputs_dir = workspace_in_core / "outputs"
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
            outputs_dir=workspace_in_core / "outputs",
            timed_out=False,
        )
    finally:
        if not keep_workspace:
            try:
                shutil.rmtree(workspace_in_core, ignore_errors=True)
            except Exception:
                pass
