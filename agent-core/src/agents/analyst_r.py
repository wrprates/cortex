from __future__ import annotations

import json
from typing import Any

from ..sandbox.runner import run_code
from ..templates import compose_prompts
from .base import call_llm


_VALID_STAGES = ("quality", "hypothesis")


def _system_prompt(stage: str) -> str:
    """Compõe o system prompt: base + prompt da stage + regras de narrativa."""
    if stage not in _VALID_STAGES:
        raise ValueError(f"stage inválida: {stage!r}; use uma de {_VALID_STAGES}")
    return compose_prompts("analyst_base", stage, "report_narrative")


def run_analyst_r(
    task: str,
    context: dict[str, Any],
    inputs: dict[str, bytes] | None = None,
    *,
    stage: str = "hypothesis",
) -> dict:
    """Gera código R de análise, executa no sandbox, coleta resultado.

    stage: "quality" ou "hypothesis" — define o prompt especializado usado.
    """
    system_prompt = _system_prompt(stage)

    code_prompt = (
        f"Tarefa: {task}\n\n"
        f"Contexto:\n{json.dumps(context, default=str, ensure_ascii=False)}\n\n"
        "Gere o código R completo que: (1) executa a análise, (2) serializa objetos em "
        "`./outputs/analysis.rds`, (3) escreve `./outputs/summary.json` seguindo o schema "
        "da fase, (4) escreve um `.qmd` inline com a narrativa em pt-BR e renderiza para "
        "`./outputs/report.html`.\n\n"
        "Responda APENAS com o código R, sem markdown."
    )

    MAX_ATTEMPTS = 3
    attempts: list[dict] = []
    messages = [{"role": "user", "content": code_prompt}]

    for attempt in range(1, MAX_ATTEMPTS + 1):
        code_result = call_llm(
            system=system_prompt,
            messages=messages,
            complex=False,
            max_tokens=16384,
        )
        code = _strip_code_fences(code_result.text)

        sandbox_result = run_code(
            code=code, language="r", inputs=inputs, timeout=900, keep_workspace=True
        )

        summary_path = sandbox_result.outputs_dir / "summary.json"
        summary_json: dict = {}
        summary_parse_error: str | None = None
        if summary_path.exists():
            try:
                summary_json = json.loads(summary_path.read_text())
            except json.JSONDecodeError as e:
                summary_parse_error = f"summary.json malformado: {e}"

        report_html_path = sandbox_result.outputs_dir / "report.html"
        has_report = report_html_path.exists() and report_html_path.stat().st_size > 1000

        attempts.append({
            "attempt": attempt,
            "exit_code": sandbox_result.exit_code,
            "has_summary": bool(summary_json),
            "has_report": has_report,
            "tokens_in": code_result.tokens_in,
            "tokens_out": code_result.tokens_out,
        })

        success = sandbox_result.exit_code == 0 and bool(summary_json) and has_report
        if success or attempt == MAX_ATTEMPTS:
            return {
                "code": code,
                "language": "r",
                "stage": stage,
                "exit_code": sandbox_result.exit_code,
                "timed_out": sandbox_result.timed_out,
                "stdout_tail": sandbox_result.stdout[-2000:],
                "stderr_tail": sandbox_result.stderr[-2000:],
                "summary": summary_json,
                "artifact_paths": [str(p) for p in sandbox_result.artifacts],
                "outputs_dir": str(sandbox_result.outputs_dir),
                "attempts": attempts,
                "success": success,
                "_usage": {
                    "tokens_in": sum(a["tokens_in"] for a in attempts),
                    "tokens_out": sum(a["tokens_out"] for a in attempts),
                    "model": code_result.model,
                },
            }

        # Retry com feedback cru do erro. Sem fallback: o agente precisa consertar.
        messages.append({"role": "assistant", "content": code_result.text})
        messages.append({
            "role": "user",
            "content": (
                f"O código falhou. exit_code={sandbox_result.exit_code}, "
                f"summary.json={summary_parse_error or ('OK' if summary_json else 'AUSENTE')}, "
                f"report.html={'OK' if has_report else 'AUSENTE'}.\n\n"
                f"STDERR:\n{sandbox_result.stderr[-2500:]}\n\n"
                f"STDOUT (tail):\n{sandbox_result.stdout[-1000:]}\n\n"
                "Analise o erro e regenere o código R inteiro corrigido. "
                "Não mascare falha com tryCatch; conserte a raiz do problema. "
                "Responda APENAS com o código R."
            ),
        })


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
        if t.startswith("r") or t.startswith("R"):
            t = t[1:].lstrip()
    return t
