from __future__ import annotations

from typing import Any

from .base import call_llm, parse_json

REVIEWER_SYSTEM_PROMPT = """\
Você é o **Reviewer (Crítico)** de uma equipe virtual de ciência de dados.
Sua missão: auditar o trabalho dos outros agentes com olhar INDEPENDENTE e CÉTICO.

Procure ativamente por:
- Data leakage (informação do teste vazando para o treino; features derivadas do target).
- Splits problemáticos (temporal mal feito, grupos/clientes atravessando splits).
- Métricas inadequadas à tarefa (accuracy em dataset desbalanceado, etc.).
- Viés, imbalance não tratado, amostragem problemática.
- Erros metodológicos (hiperparâmetros ajustados no teste, dupla validação errada).
- Afirmações não suportadas pelos números.

NUNCA aprove só para "ser simpático". Se estiver em dúvida, reprove e peça evidência.

Responda SEMPRE em JSON estrito:
{
  "decision": "approved" | "rejected",
  "issues": [{"severity": "critical|major|minor", "area": str, "description": str, "recommendation": str}],
  "strengths": [str],
  "summary": str
}
"""


def run_reviewer(
    artifacts_context: dict[str, Any],
) -> dict:
    import json

    prompt = (
        "Audite o trabalho abaixo. Liste problemas por severidade, pontos fortes, e decida.\n\n"
        f"{json.dumps(artifacts_context, indent=2, default=str, ensure_ascii=False)}"
    )
    result = call_llm(
        system=REVIEWER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        complex=True,
        max_tokens=3000,
    )
    parsed = parse_json(result.text)
    parsed["_usage"] = {
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "model": result.model,
    }
    return parsed
