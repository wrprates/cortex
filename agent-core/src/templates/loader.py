from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """
    Carrega um prompt markdown em `templates/prompts/`.

    `name` é o basename sem extensão (ex: 'analyst_base', 'quality').
    """
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def compose_prompts(*names: str, separator: str = "\n\n---\n\n") -> str:
    """Concatena múltiplos prompts em ordem, separados por uma régua markdown."""
    return separator.join(load_prompt(n) for n in names)
