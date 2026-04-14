from __future__ import annotations

import json
from typing import Any

from ..sandbox.runner import run_code
from .base import call_llm

MODELER_SYSTEM_PROMPT = """\
Você é o **Modeler** de uma equipe virtual de ciência de dados.
Objetivo: feature engineering, treinar modelos, comparar métricas, selecionar o melhor.

Ao gerar código Python:
- Assuma dados em ./inputs/.
- Use train/validation/test splits apropriados (nunca vaze dados do teste).
- Registre métricas (accuracy, AUC, RMSE, F1, etc. conforme tarefa) em ./outputs/metrics.json.
- Salve o modelo vencedor em ./outputs/model.pkl (via joblib ou pickle).
- Salve tabela comparativa em ./outputs/leaderboard.csv.
- NÃO acesse rede. NÃO instale pacotes.

Responda APENAS com código Python puro, sem cercas markdown, sem explicação.
"""


def run_modeler(
    task: str,
    context: dict[str, Any],
    inputs: dict[str, bytes] | None = None,
    *,
    final_training: bool = False,
) -> dict:
    prompt = (
        f"Tarefa de modelagem: {task}\n\n"
        f"Contexto (inclui resultados da EDA):\n{json.dumps(context, default=str, ensure_ascii=False)}\n\n"
        f"{'Este é o TREINO FINAL — use a melhor config encontrada.' if final_training else 'Fase exploratória — compare múltiplos baselines.'}"
    )
    code_result = call_llm(
        system=MODELER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        complex=True,
        max_tokens=6000,
    )
    code = _strip(code_result.text)

    sandbox_result = run_code(code=code, language="python", inputs=inputs)

    metrics: dict = {}
    metrics_path = sandbox_result.outputs_dir / "metrics.json"
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text())
        except json.JSONDecodeError:
            metrics = {"_parse_error": True}

    return {
        "code": code,
        "final_training": final_training,
        "exit_code": sandbox_result.exit_code,
        "timed_out": sandbox_result.timed_out,
        "stdout_tail": sandbox_result.stdout[-2000:],
        "stderr_tail": sandbox_result.stderr[-2000:],
        "metrics": metrics,
        "artifact_paths": [str(p) for p in sandbox_result.artifacts],
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
        if t.startswith("python"):
            t = t[6:].lstrip()
    return t
