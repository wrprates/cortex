from __future__ import annotations

from typing import Any

from .base import call_llm, parse_json

ORCHESTRATOR_SYSTEM_PROMPT = """\
Você é o **Orchestrator** de uma equipe virtual de ciência de dados.
Sua missão: transformar o briefing de um projeto em um plano de trabalho executável
por agentes especializados (Data Analyst, Modeler, Reviewer) e consolidar resultados.

Tipos de workflow suportados:
- data_quality: Apenas análise de qualidade de dados (completude, consistência, unicidade, validade)
- eda_hypothesis: EDA completa + testes de hipóteses estatísticos (sem modelagem ML)
- full_ml: Workflow completo com EDA, modelagem e revisão

Responsabilidades:
- Ler a descrição do problema e dos dados disponíveis.
- Gerar um plano adaptado ao tipo de workflow solicitado.
- Delegar tarefas específicas a cada agente, com objetivos mensuráveis.
- Identificar pontos que exigem aprovação humana (plano inicial e antes de treino final).
- Ao final, consolidar os artefatos produzidos em um relatório HTML interativo.

Regras:
- Seja específico e pragmático; evite jargão sem valor.
- Todas as decisões técnicas devem ter justificativa curta (1-2 frases).
- Quando gerar plano ou relatório, responda **APENAS em JSON válido**, sem texto extra.
- Relatórios devem ser gerados em formato compatível com Quarto/echarts4r.
"""


def _get_plan_prompt(workflow_type: str) -> str:
    """Retorna o prompt de planejamento adequado ao tipo de workflow."""
    base_schema = (
        '{"phases": [{"name": str, "agent": "analyst|modeler|reviewer|orchestrator", '
        '"objective": str, "success_criteria": str, "requires_human_approval": bool}], '
        '"summary": str, "risks": [str]}'
    )

    if workflow_type == "data_quality":
        return (
            "Gere um plano de ANÁLISE DE QUALIDADE DE DADOS. "
            "Este workflow foca em: completude, consistência, unicidade e validade dos dados. "
            "NÃO inclua fases de modelagem ML. "
            f"Responda em JSON com o schema:\n{base_schema}"
        )
    elif workflow_type == "eda_hypothesis":
        return (
            "Gere um plano de EDA + TESTES DE HIPÓTESES. "
            "Este workflow inclui: análise exploratória completa, estatísticas descritivas, "
            "visualizações, e testes de hipóteses estatísticos (t-test, chi-square, correlações). "
            "NÃO inclua fases de modelagem ML. "
            f"Responda em JSON com o schema:\n{base_schema}"
        )
    else:  # full_ml
        return (
            "Gere um plano completo de DATA SCIENCE com ML. "
            "Fases: EDA → feature engineering → modelagem → revisão → relatório. "
            f"Responda em JSON com o schema:\n{base_schema}"
        )


def run_orchestrator(
    action: str,
    context: dict[str, Any],
    *,
    complex: bool = True,
) -> dict:
    """
    action: 'plan' | 'decide_next' | 'compile_report'
    context: dados relevantes para a ação.
    """
    workflow_type = context.get("workflow_type", "full_ml")

    plan_prompt = _get_plan_prompt(workflow_type)

    prompts = {
        "plan": plan_prompt,
        "decide_next": (
            "Dado o estado atual do projeto, decida a próxima fase e o agente a chamar. "
            'Responda em JSON: {"next_phase": str, "next_agent": str, "reasoning": str, '
            '"done": bool}'
        ),
        "compile_report": (
            "Consolide os resultados em um relatório final HTML interativo. "
            "O relatório será renderizado com Quarto e echarts4r. "
            "Responda em JSON com o schema:\n"
            '{"title": str, "subtitle": str, "executive_summary": str, '
            '"quality_alerts": str (markdown), "key_findings": [str], '
            '"has_hypothesis_tests": bool, "hypothesis_interpretation": str, '
            '"has_modeling": bool, "conclusions": str (markdown), '
            '"metrics": dict, "recommendations": [str], "caveats": [str]}'
        ),
    }
    if action not in prompts:
        raise ValueError(f"unknown orchestrator action: {action}")

    user_msg = f"{prompts[action]}\n\n---\nContexto:\n{_format_context(context)}"

    result = call_llm(
        system=ORCHESTRATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        complex=complex,
    )
    parsed = parse_json(result.text)
    parsed["_usage"] = {"tokens_in": result.tokens_in, "tokens_out": result.tokens_out, "model": result.model}
    return parsed


def _format_context(ctx: dict) -> str:
    import json
    return json.dumps(ctx, indent=2, default=str, ensure_ascii=False)
