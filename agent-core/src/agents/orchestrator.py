from __future__ import annotations

from typing import Any

from ..templates import compose_prompts, load_prompt
from .base import call_llm, parse_json

ORCHESTRATOR_SYSTEM_PROMPT = compose_prompts("orchestrator_base", "report_narrative")


def _plan_user_prompt(workflow_type: str) -> str:
    base_schema = (
        '{"phases": [{"name": str, "agent": "analyst|modeler|reviewer|orchestrator", '
        '"objective": str, "success_criteria": str, "requires_human_approval": bool, '
        '"rationale": str}], "summary": str, "risks": [str], '
        '"feasibility": {"assessment": "ok|ok_with_caveats|not_viable", "notes": str}}'
    )

    if workflow_type == "data_quality":
        focus = (
            "Plano de **qualidade de dados** seguindo `quality.md`: completude, consistência, "
            "unicidade, validade. Não inclua fases de ML."
        )
    elif workflow_type == "eda_hypothesis":
        focus = (
            "Plano de **qualidade + EDA por hipóteses** seguindo `quality.md` e `hypothesis.md`. "
            "Cada hipótese do plano precisa ser formulada em linguagem de negócio com seu teste "
            "estatístico correspondente. Não inclua fases de ML."
        )
    else:  # full_ml
        focus = (
            "Plano completo: **qualidade → EDA por hipóteses → modelagem → revisão → relatório**. "
            "Na fase de modelagem, avalie viabilidade: se não houver target claro ou N insuficiente, "
            "declare inviabilidade em `feasibility` e remova a fase."
        )

    return (
        f"{focus}\n\n"
        "Para cada fase, inclua `rationale` explicando **por que ela está no plano** "
        "em 1-2 frases. Se alguma etapa padrão for omitida, justifique em `feasibility.notes`.\n\n"
        f"Responda em JSON estrito seguindo o schema:\n{base_schema}"
    )


_REPORT_SCHEMA = (
    '{"title": str, "subtitle": str, "executive_summary": str (markdown, 3-6 parágrafos), '
    '"key_findings": [{"finding": str, "significance": str, "recommended_action": str}], '
    '"quality_verdict": str (apto|apto_com_ressalvas|inapto), '
    '"quality_notes": str (markdown), '
    '"has_hypothesis_tests": bool, '
    '"hypothesis_interpretation": str (markdown, com regra das 3 camadas), '
    '"has_modeling": bool, '
    '"model_interpretation": str (markdown), '
    '"conclusions": str (markdown), '
    '"recommendations": [{"action": str, "justification": str, "expected_impact": str}], '
    '"caveats": [str]}'
)

_PROMPTS = {
    "decide_next": (
        "Dado o estado atual do projeto, decida a próxima fase. Se houver erro em um agente, "
        "avalie: (a) pedir retry com correção, (b) pular a fase documentando no report, "
        "(c) abortar. Nunca invente dados para mascarar falha.\n"
        'Responda em JSON: {"next_phase": str, "next_agent": str, "reasoning": str, '
        '"action_on_error": "retry|skip|abort"|null, "done": bool}'
    ),
    "compile_report": (
        "Consolide os resultados em um relatório final seguindo `report_narrative.md`. "
        "Cada `key_finding` precisa respeitar a **regra das 3 camadas** (observação → "
        "significado → ação). Sem tabelas soltas, sem jargão cru. Português do Brasil.\n\n"
        f"Responda em JSON estrito:\n{_REPORT_SCHEMA}"
    ),
}


def run_orchestrator(
    action: str,
    context: dict[str, Any],
    *,
    complex: bool = True,
) -> dict:
    """action: 'plan' | 'decide_next' | 'compile_report'."""
    if action == "plan":
        workflow_type = context.get("workflow_type", "full_ml")
        user_prompt = _plan_user_prompt(workflow_type)
    elif action in _PROMPTS:
        user_prompt = _PROMPTS[action]
    else:
        raise ValueError(f"unknown orchestrator action: {action}")

    user_msg = f"{user_prompt}\n\n---\nContexto:\n{_format_context(context)}"

    result = call_llm(
        system=ORCHESTRATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        complex=complex,
    )
    parsed = parse_json(result.text)
    parsed["_usage"] = {
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "model": result.model,
    }
    return parsed


def _format_context(ctx: dict) -> str:
    import json

    return json.dumps(ctx, indent=2, default=str, ensure_ascii=False)
