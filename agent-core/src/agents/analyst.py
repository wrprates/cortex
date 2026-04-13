from __future__ import annotations

import json
from typing import Any

from ..sandbox.runner import run_code
from .base import call_llm, parse_json

ANALYST_SYSTEM_PROMPT = """\
Você é o **Data Analyst** de uma equipe virtual de ciência de dados.
Objetivo: inspecionar datasets, fazer EDA, gerar hipóteses e produzir visualizações e estatísticas descritivas.

Ao gerar código Python:
- Assuma que os dados estão em /workspace/inputs/.
- Salve plots e tabelas em /workspace/outputs/.
- Salve um sumário estruturado em /workspace/outputs/summary.json.
- NÃO acesse rede. NÃO tente instalar pacotes.
- Bibliotecas disponíveis: pandas, numpy, scipy, scikit-learn, matplotlib, seaborn,
  plotly, statsmodels, xgboost, lightgbm, category_encoders, pyarrow.

Quando pedirem código, responda APENAS com o bloco de código Python (sem cercas markdown, sem explicação).
Quando pedirem interpretação ou plano de EDA, responda em JSON estrito.
"""


def run_analyst(
    task: str,
    context: dict[str, Any],
    inputs: dict[str, bytes] | None = None,
) -> dict:
    """
    Gera código de EDA, executa no sandbox, interpreta resultados.
    """
    code_prompt = (
        f"Gere código Python de EDA para esta tarefa:\n{task}\n\n"
        f"Contexto:\n{json.dumps(context, default=str, ensure_ascii=False)}\n\n"
        "Inclua: tipos/dtypes, missing, estatísticas descritivas, distribuições, "
        "correlações relevantes, e alertas sobre qualidade dos dados. "
        "Escreva o sumário em /workspace/outputs/summary.json."
    )
    code_result = call_llm(
        system=ANALYST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": code_prompt}],
        complex=False,
        max_tokens=4096,
    )
    code = _strip_code_fences(code_result.text)

    sandbox_result = run_code(code=code, language="python", inputs=inputs)

    summary_json: dict = {}
    summary_path = sandbox_result.outputs_dir / "summary.json"
    if summary_path.exists():
        try:
            summary_json = json.loads(summary_path.read_text())
        except json.JSONDecodeError:
            summary_json = {"_parse_error": True}

    return {
        "code": code,
        "exit_code": sandbox_result.exit_code,
        "timed_out": sandbox_result.timed_out,
        "stdout_tail": sandbox_result.stdout[-2000:],
        "stderr_tail": sandbox_result.stderr[-2000:],
        "summary": summary_json,
        "artifact_paths": [str(p) for p in sandbox_result.artifacts],
        "_usage": {
            "tokens_in": code_result.tokens_in,
            "tokens_out": code_result.tokens_out,
            "model": code_result.model,
        },
    }


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
        if t.startswith("python"):
            t = t[6:].lstrip()
    return t
