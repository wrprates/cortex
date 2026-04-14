from __future__ import annotations

import json
from typing import Any

from ..sandbox.runner import run_code
from .base import call_llm

ANALYST_R_SYSTEM_PROMPT = """\
Você é o **Data Analyst** de uma equipe virtual de ciência de dados.
Linguagem: R (tidyverse stack).
Objetivo: inspecionar datasets, fazer EDA, gerar hipóteses, testes estatísticos e produzir visualizações interativas.

Ao gerar código R:
- Assuma que os dados estão em ./inputs/.
- Salve plots e tabelas em ./outputs/.
- Salve um sumário estruturado em ./outputs/summary.json usando jsonlite::write_json().
- NÃO acesse rede. NÃO tente instalar pacotes.
- Bibliotecas disponíveis: tidyverse (dplyr, ggplot2, readr, tidyr, purrr, stringr, forcats),
  data.table, echarts4r, plotly, DT, jsonlite, rmarkdown.

Para visualizações interativas:
- Use echarts4r para gráficos HTML interativos.
- Salve widgets HTML com htmlwidgets::saveWidget() em ./outputs/.

Para testes de hipóteses:
- Use t.test(), chisq.test(), wilcox.test(), cor.test() conforme apropriado.
- Inclua p-values, intervalos de confiança e effect sizes no summary.json.

Quando pedirem código, responda APENAS com o bloco de código R (sem cercas markdown, sem explicação).
Quando pedirem interpretação ou plano de EDA, responda em JSON estrito.
"""


def run_analyst_r(
    task: str,
    context: dict[str, Any],
    inputs: dict[str, bytes] | None = None,
    workflow_type: str = "full_ml",
) -> dict:
    """
    Gera código R de EDA, executa no sandbox, interpreta resultados.
    """
    task_detail = _task_by_workflow(task, workflow_type)

    code_prompt = (
        f"Gere código R de EDA para esta tarefa:\n{task_detail}\n\n"
        f"Contexto:\n{json.dumps(context, default=str, ensure_ascii=False)}\n\n"
        "Inclua: tipos de colunas, missing values, estatísticas descritivas, distribuições, "
        "correlações relevantes, e alertas sobre qualidade dos dados. "
        "Use echarts4r para gráficos interativos. "
        "Escreva o sumário em ./outputs/summary.json usando jsonlite::write_json()."
    )
    MAX_ATTEMPTS = 3
    attempts: list[dict] = []
    messages = [{"role": "user", "content": code_prompt}]

    for attempt in range(1, MAX_ATTEMPTS + 1):
        code_result = call_llm(
            system=ANALYST_R_SYSTEM_PROMPT,
            messages=messages,
            complex=False,
            max_tokens=16384,
        )
        code = _strip_code_fences(code_result.text)

        sandbox_result = run_code(code=code, language="r", inputs=inputs, timeout=900, keep_workspace=True)

        summary_path = sandbox_result.outputs_dir / "summary.json"
        summary_json: dict = {}
        if summary_path.exists():
            try:
                summary_json = json.loads(summary_path.read_text())
            except json.JSONDecodeError:
                summary_json = {"_parse_error": True}

        attempts.append({
            "attempt": attempt,
            "exit_code": sandbox_result.exit_code,
            "has_summary": bool(summary_json),
            "tokens_in": code_result.tokens_in,
            "tokens_out": code_result.tokens_out,
        })

        # Sucesso: código rodou limpo E produziu summary.json
        success = sandbox_result.exit_code == 0 and bool(summary_json)
        if success or attempt == MAX_ATTEMPTS:
            return {
                "code": code,
                "language": "r",
                "exit_code": sandbox_result.exit_code,
                "timed_out": sandbox_result.timed_out,
                "stdout_tail": sandbox_result.stdout[-2000:],
                "stderr_tail": sandbox_result.stderr[-2000:],
                "summary": summary_json,
                "artifact_paths": [str(p) for p in sandbox_result.artifacts],
                "attempts": attempts,
                "_usage": {
                    "tokens_in": sum(a["tokens_in"] for a in attempts),
                    "tokens_out": sum(a["tokens_out"] for a in attempts),
                    "model": code_result.model,
                },
            }

        # Falhou e ainda tem tentativa: regenera com contexto do erro
        messages.append({"role": "assistant", "content": code_result.text})
        messages.append({
            "role": "user",
            "content": (
                f"O código anterior falhou com exit_code={sandbox_result.exit_code}.\n"
                f"STDERR (últimos 1500 chars):\n{sandbox_result.stderr[-1500:]}\n\n"
                f"STDOUT tail:\n{sandbox_result.stdout[-800:]}\n\n"
                "Regenere o código R corrigindo o erro específico. Se o erro for de "
                "uma análise não-aplicável (ex: variável constante, tamanho amostral "
                "insuficiente), PULE essa análise com um cat() explicando o motivo, "
                "não trave o script inteiro. Use tryCatch() nas análises opcionais. "
                "Responda APENAS com o código R corrigido."
            ),
        })


def _task_by_workflow(task: str, workflow_type: str) -> str:
    """Ajusta a tarefa baseado no tipo de workflow."""
    if workflow_type == "data_quality":
        return (
            f"{task}\n\n"
            "FOCO: Análise de qualidade de dados.\n"
            "- Completude (% missing por coluna)\n"
            "- Consistência (tipos de dados, formatos)\n"
            "- Unicidade (duplicatas, chaves)\n"
            "- Validade (ranges, outliers extremos)\n"
            "- Recomendações de limpeza"
        )
    elif workflow_type == "eda_hypothesis":
        return (
            f"{task}\n\n"
            "FOCO: EDA completa + testes de hipóteses.\n"
            "- Estatísticas descritivas\n"
            "- Distribuições e visualizações\n"
            "- Testes de hipóteses apropriados (t-test, chi-square, correlação)\n"
            "- Intervalos de confiança\n"
            "- Conclusões estatísticas com p-values"
        )
    else:
        return task


def _strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
        if t.startswith("r") or t.startswith("R"):
            t = t[1:].lstrip()
    return t
