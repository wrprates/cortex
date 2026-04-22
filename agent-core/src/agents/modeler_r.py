from __future__ import annotations

import json
from typing import Any

from ..sandbox.runner import run_code
from ..templates import compose_prompts
from .base import call_llm


def _system_prompt() -> str:
    """System prompt da fase modeling: analyst_base + modeling + report_narrative."""
    return compose_prompts("analyst_base", "modeling", "report_narrative")


def run_modeler_r(
    task: str,
    context: dict[str, Any],
    inputs: dict[str, bytes] | None = None,
    *,
    final_training: bool = False,
) -> dict:
    prompt = (
        f"Tarefa de modelagem: {task}\n\n"
        f"Contexto (inclui resultados da EDA):\n{json.dumps(context, default=str, ensure_ascii=False)}\n\n"
        f"{'Este é o TREINO FINAL — use a melhor config encontrada.' if final_training else 'Fase exploratória — compare múltiplos baselines conforme o checklist em modeling.md.'}\n\n"
        "Decida target, features e modelos conforme o contexto e o checklist. "
        "Responda APENAS com o código R."
    )
    code_result = call_llm(
        system=_system_prompt(),
        messages=[{"role": "user", "content": prompt}],
        complex=True,
        max_tokens=16384,
    )
    code = _strip(code_result.text)

    sandbox_result = run_code(code=code, language="r", inputs=inputs, keep_workspace=True)

    metrics: dict = {}
    metrics_parse_error: str | None = None
    metrics_path = sandbox_result.outputs_dir / "metrics.json"
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text())
        except json.JSONDecodeError as e:
            metrics_parse_error = f"metrics.json malformado: {e}"

    return {
        "code": code,
        "language": "r",
        "final_training": final_training,
        "exit_code": sandbox_result.exit_code,
        "timed_out": sandbox_result.timed_out,
        "stdout_tail": sandbox_result.stdout[-2000:],
        "stderr_tail": sandbox_result.stderr[-2000:],
        "metrics": metrics,
        "metrics_parse_error": metrics_parse_error,
        "artifact_paths": [str(p) for p in sandbox_result.artifacts],
        "outputs_dir": str(sandbox_result.outputs_dir),
        "_usage": {
            "tokens_in": code_result.tokens_in,
            "tokens_out": code_result.tokens_out,
            "model": code_result.model,
        },
    }


def _strip(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
        if t.startswith("r") or t.startswith("R"):
            t = t[1:].lstrip()
    return t
