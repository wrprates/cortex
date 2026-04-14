from __future__ import annotations

from functools import lru_cache

import docker
from docker import DockerClient


@lru_cache
def get_docker() -> DockerClient:
    # Usa /var/run/docker.sock montado no container agent-core.
    # timeout alto para aguardar runs longas sem quebrar o container.wait().
    return docker.from_env(timeout=600)
