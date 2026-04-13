from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from anthropic import Anthropic

from ..config import get_settings

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        settings = get_settings()
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


@dataclass
class AgentCall:
    text: str
    raw: Any
    tokens_in: int
    tokens_out: int
    model: str


def call_llm(
    system: str,
    messages: list[dict[str, Any]],
    *,
    complex: bool = False,
    max_tokens: int = 4096,
    temperature: float = 0.2,
    tools: list[dict] | None = None,
) -> AgentCall:
    settings = get_settings()
    model = settings.claude_model_complex if complex else settings.claude_model_routine

    client = get_client()
    kwargs: dict[str, Any] = dict(
        model=model,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if tools:
        kwargs["tools"] = tools

    response = client.messages.create(**kwargs)

    text_parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)

    return AgentCall(
        text="\n".join(text_parts).strip(),
        raw=response,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        model=model,
    )


def parse_json(text: str) -> dict:
    """Extrai JSON da resposta do LLM (tolera cercas markdown)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
        if t.startswith("json"):
            t = t[4:].lstrip()
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse JSON from LLM: %s", e)
        return {"_raw": text, "_error": str(e)}
