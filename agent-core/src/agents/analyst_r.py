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
- Assuma dados em ./inputs/.
- Salve plots, tabelas e artefatos em ./outputs/.
- Salve um sumário estruturado em ./outputs/summary.json usando jsonlite::write_json().
- NÃO acesse rede. NÃO tente instalar pacotes.
- Bibliotecas: tidyverse, data.table, echarts4r, plotly, DT, jsonlite, rmarkdown,
  quarto, htmlwidgets, broom, skimr.

ENTREGÁVEL PRINCIPAL: relatório HTML interativo em ./outputs/report.html
- Estrutura o código assim:
  1. Faz TODA a análise e grava resultados em objetos R (usar saveRDS() em ./outputs/analysis.rds).
  2. Gera um report.qmd inline via writeLines() contendo a narrativa interpretativa em pt-BR.
  3. Renderiza com quarto::quarto_render("report.qmd", output_format = "html") ou
     rmarkdown::render() como fallback.
  4. Move/copia o HTML final pra ./outputs/report.html.
- O report.qmd deve ter seções com títulos (##), texto em pt-BR interpretando cada achado,
  e blocos de código R que carregam analysis.rds e geram as visualizações interativas
  com echarts4r (e |> e_charts()) para o leitor.
- Use tryCatch() em análises opcionais — se falhar uma hipótese específica, logue com cat()
  e siga; NÃO deixe o script inteiro crashar por um teste específico que não seja aplicável.

Para testes de hipóteses: t.test/wilcox.test/chisq.test/cor.test com p-values, IC95%, effect
sizes (cohen's d, r, cramer's V). Aplicar Bonferroni ou BH quando houver múltiplos testes.

Quando pedirem código, responda APENAS com o bloco R (sem cercas markdown).
Quando pedirem interpretação/plano, responda em JSON estrito.
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

        # Sucesso: código rodou limpo, produziu summary.json E report.html
        report_html_path = sandbox_result.outputs_dir / "report.html"
        has_report = report_html_path.exists() and report_html_path.stat().st_size > 1000
        success = sandbox_result.exit_code == 0 and bool(summary_json) and has_report
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
                "outputs_dir": str(sandbox_result.outputs_dir),
                "attempts": attempts,
                "_usage": {
                    "tokens_in": sum(a["tokens_in"] for a in attempts),
                    "tokens_out": sum(a["tokens_out"] for a in attempts),
                    "model": code_result.model,
                },
            }

        # Falhou e ainda tem tentativa: regenera com contexto do erro
        messages.append({"role": "assistant", "content": code_result.text})
        feedback_lines = [
            f"O código anterior não produziu entregável completo.",
            f"exit_code={sandbox_result.exit_code}, summary.json={'OK' if summary_json else 'AUSENTE'}, report.html={'OK' if has_report else 'AUSENTE ou vazio'}.",
            "",
            f"STDERR (últimos 1500 chars):\n{sandbox_result.stderr[-1500:]}",
            "",
            f"STDOUT tail:\n{sandbox_result.stdout[-1200:]}",
            "",
            "CORRIJA o código R. Requisitos OBRIGATÓRIOS:",
            "1. Produzir ./outputs/summary.json (via jsonlite::write_json).",
            "2. Produzir ./outputs/report.html (via quarto::quarto_render ou rmarkdown::render).",
            "3. Nos chunks do .qmd/.Rmd, JAMAIS referencie colunas por nome dinâmico sem verificar com if (col %in% names(df)) antes. Cuidado com dplyr::all_of() — só passe nomes que EXISTEM no df.",
            "4. Envolva CADA chunk de visualização em tryCatch() para que UM chunk com erro não mate o render inteiro.",
            "5. Se o Quarto falhar, use rmarkdown::render() como fallback. Se ambos falharem, construa o HTML manualmente com htmltools.",
            "",
            "Responda APENAS com o código R corrigido, sem explicação, sem cercas markdown.",
        ]
        messages.append({
            "role": "user",
            "content": "\n".join(feedback_lines),
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
