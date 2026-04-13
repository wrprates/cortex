from __future__ import annotations

from typing import Any

from .base import call_llm, parse_json

ORCHESTRATOR_SYSTEM_PROMPT = """\
Você é o **Orchestrator** de uma equipe virtual de ciência de dados.
Sua missão: transformar o briefing de um projeto em um plano de trabalho executável
por agentes especializados (Data Analyst, Modeler, Reviewer) e consolidar resultados.

Responsabilidades:
- Ler a descrição do problema e dos dados disponíveis.
- Gerar um plano em fases: EDA → feature engineering → modelagem → revisão → relatório.
- Delegar tarefas específicas a cada agente, com objetivos mensuráveis.
- Identificar pontos que exigem aprovação humana (plano inicial e antes de treino final).
- Ao final, consolidar os artefatos produzidos em um relatório conciso e acionável.

Regras:
- Seja específico e pragmático; evite jargão sem valor.
- Todas as decisões técnicas devem ter justificativa curta (1-2 frases).
- Quando gerar plano ou relatório, responda **APENAS em JSON válido**, sem texto extra.
"""


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
    prompts = {
        "plan": (
            "Gere um plano inicial de trabalho. Responda em JSON com o schema:\n"
            '{"phases": [{"name": str, "agent": "analyst|modeler|reviewer|orchestrator", '
            '"objective": str, "success_criteria": str, "requires_human_approval": bool}], '
            '"summary": str, "risks": [str]}'
        ),
        "decide_next": (
            "Dado o estado atual do projeto, decida a próxima fase e o agente a chamar. "
            'Responda em JSON: {"next_phase": str, "next_agent": str, "reasoning": str, '
            '"done": bool}'
        ),
        "compile_report": (
            "Consolide os resultados em um relatório final. Responda em JSON: "
            '{"executive_summary": str, "key_findings": [str], "metrics": dict, '
            '"recommendations": [str], "caveats": [str]}'
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
