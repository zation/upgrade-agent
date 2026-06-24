"""Anthropic LLM client.

The ONLY module that knows about Anthropic's wire format. It:
  * owns the SDK client and retries on transient errors,
  * translates between our neutral :mod:`types` and SDK dicts,
  * returns a normalized response the ReAct loop can consume blindly.

Swapping to another provider later means rewriting just this file.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import anthropic
from dotenv import load_dotenv

from .types import (
    AgentConfig,
    Message,
    StopReason,
    TextBlock,
    parse_blocks,
    to_sdk_content,
)

__all__ = ["LLMClient", "LLMResponse", "ask"]

log = logging.getLogger(__name__)

# Load .env once on import so the SDK picks up ANTHROPIC_API_KEY.
load_dotenv()

# Retryable HTTP status codes from the SDK. We back off exponentially.
_RETRYABLE = (408, 429, 500, 502, 503, 504)
_MAX_RETRIES = 4


@dataclass
class LLMResponse:
    """Normalized result of one model turn."""

    assistant: Message  # the full assistant message (text + tool_use blocks)
    stop_reason: StopReason
    # Token accounting — useful for cost reporting and context budgeting.
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient:
    """Thin wrapper around ``anthropic.Anthropic`` with retry + normalization."""

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        self._client = anthropic.Anthropic(api_key=key)

    def ask(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        config: AgentConfig,
    ) -> LLMResponse:
        """Send one turn to the model and return a normalized response.

        ``messages`` is the full conversation so far (user + assistant turns).
        ``tools`` is the list of Anthropic tool definitions. ``config`` carries
        model name, max_tokens, temperature.
        """
        sdk_messages = [
            {"role": m.role, "content": to_sdk_content(m)} for m in messages
        ]

        # Retry loop for transient failures. We do NOT retry content errors
        # (e.g. 400 invalid_request) — those need the caller to fix the input.
        last_err: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._client.messages.create(
                    model=config.model,
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    system=system,
                    tools=tools,
                    messages=sdk_messages,
                )
                return self._normalize(resp)
            except anthropic.APIStatusError as e:  # pragma: no cover - network path
                last_err = e
                if e.status_code not in _RETRYABLE:
                    raise
                _sleep_backoff(attempt)
            except anthropic.APIConnectionError as e:  # pragma: no cover
                last_err = e
                _sleep_backoff(attempt)

        # Exhausted retries.
        raise RuntimeError(f"LLM call failed after {_MAX_RETRIES} attempts") from last_err

    def _normalize(self, resp: Any) -> LLMResponse:
        """Turn an SDK response into our :class:`LLMResponse`."""
        blocks = parse_blocks(resp.content)
        # Synthesize stop_reason into our union; unknown values become "end_turn".
        stop: StopReason = getattr(resp, "stop_reason", None) or "end_turn"
        if stop not in ("end_turn", "tool_use", "max_tokens", "stop_sequence"):
            stop = "end_turn"
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) if usage else 0
        out_tok = getattr(usage, "output_tokens", 0) if usage else 0
        return LLMResponse(
            assistant=Message(role="assistant", content=blocks),
            stop_reason=stop,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )


def _sleep_backoff(attempt: int) -> None:
    """Exponential backoff: ~0.5s, 1s, 2s, 4s."""
    time.sleep(0.5 * (2**attempt))


# Module-level convenience for one-shot prompts (no tools, no loop).
# Used by simple demo/eval helpers; the ReAct loop uses the class directly.
def ask(
    prompt: str,
    *,
    model: str = "claude-sonnet-4-5",
    system: str = "You are a helpful assistant.",
    max_tokens: int = 1024,
) -> str:
    """Single-shot text completion. Returns the assistant's text."""
    client = LLMClient()
    cfg = AgentConfig(model=model, system_prompt=system, max_tokens=max_tokens)
    resp = client.ask(
        system=system,
        messages=[Message(role="user", content=[TextBlock(text=prompt)])],
        tools=[],
        config=cfg,
    )
    return resp.assistant.text()
