"""
Decision Maker: olha erro + objetivo + profile e decide retry/skip/abort.

Chamado por nodes quando um agente executor falha após seus retries mecânicos.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..templates import load_prompt
from .base import call_llm, parse_json

logger = logging.getLogger(__name__)

DECISION_MAKER_SYSTEM_PROMPT = load_prompt("decision_maker")


def run_decision_maker(
    *,
    failing_agent: str,
    objective: str,
    attempts: list[dict],
    stderr_tail: str,
    stdout_tail: str = "",
    profile: dict[str, Any] | None = None,
) -> dict:
    """
    Retorna dict: {"action": str, "rationale": str, "guidance": str?}.

    Nunca levanta exceção — em caso de resposta inválida do LLM, retorna
    action="abort" para deixar o chamador lidar (sem mascarar erro).
    """
    context = {
        "failing_agent": failing_agent,
        "objective": objective,
        "attempts": attempts,
        "stderr_tail": stderr_tail[-2000:],
        "stdout_tail": stdout_tail[-800:],
        "dataset_profile": profile,
    }

    user_msg = (
        "Analise o estado abaixo e decida a ação seguindo a política do seu prompt. "
        "Responda APENAS com JSON estrito.\n\n"
        f"{json.dumps(context, default=str, ensure_ascii=False)}"
    )

    result = call_llm(
        system=DECISION_MAKER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        complex=True,
        max_tokens=1024,
    )
    decision = parse_json(result.text)

    if "action" not in decision or decision["action"] not in (
        "retry_with_guidance", "skip", "abort"
    ):
        logger.error("decision_maker retornou payload inválido: %s", decision)
        return {
            "action": "abort",
            "rationale": "decision_maker retornou payload inválido",
            "_invalid_response": decision,
        }

    decision["_usage"] = {
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "model": result.model,
    }
    return decision
