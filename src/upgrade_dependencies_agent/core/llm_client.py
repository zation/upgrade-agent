"""LLM clients — the only modules that know about provider wire formats.

Architecture (this is the "model-agnostic" pattern interviewers love):

  ``LLMClient`` is a Protocol — a duck-typed interface the ReAct loop uses.
  Concrete implementations translate between our neutral types and each
  provider's SDK. Adding a new provider = one new class here + zero changes
  elsewhere.

  Provider mapping:
  - ``anthropic``  → AnthropicClient (native Claude SDK)
  - ``openai-compat`` → OpenAICompatibleClient (works with DeepSeek, Ollama,
    vLLM, any OpenAI-compatible server)

  Which provider to use is controlled by the ``LLM_PROVIDER`` env var
  (default: ``anthropic``). Each provider reads its own ``*_API_KEY`` and
  optional ``*_BASE_URL`` from the environment.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from dotenv import load_dotenv

from .types import (
    AgentConfig,
    Message,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    parse_blocks,
    to_sdk_content,
)

__all__ = [
    "AnthropicClient",
    "LLMClient",
    "LLMResponse",
    "OpenAICompatibleClient",
    "create_client",
]

log = logging.getLogger(__name__)

# Load .env once on import.
load_dotenv()

_RETRYABLE = (408, 429, 500, 502, 503, 504)
_MAX_RETRIES = 4


# --------------------------------------------------------------------------- #
# Shared response type
# --------------------------------------------------------------------------- #
@dataclass
class LLMResponse:
    """Normalized result of one model turn — provider-agnostic."""

    assistant: Message
    stop_reason: StopReason
    input_tokens: int = 0
    output_tokens: int = 0


# --------------------------------------------------------------------------- #
# Provider protocol
# --------------------------------------------------------------------------- #
@runtime_checkable
class LLMClient(Protocol):
    """The contract the ReAct loop depends on.

    Implementations translate between our neutral types and their provider SDK,
    handle retries, and return a normalized :class:`LLMResponse`.
    """

    def ask(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        config: AgentConfig,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse: ...


# --------------------------------------------------------------------------- #
# Anthropic implementation
# --------------------------------------------------------------------------- #
def _sleep_backoff(attempt: int) -> None:
    time.sleep(0.5 * (2**attempt))


def _should_fallback_to_json_object(
    error: Any,
    response_format: dict[str, Any] | None,
) -> bool:
    """Return true when an OpenAI-compatible server rejected native JSON Schema."""
    return (
        bool(response_format)
        and response_format.get("type") == "json_schema"
        and getattr(error, "status_code", None) in (400, 422)
    )


class AnthropicClient:
    """Claude via the native Anthropic SDK."""

    def __init__(self, api_key: str | None = None) -> None:
        import anthropic

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
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        import anthropic

        sdk_messages = [{"role": m.role, "content": to_sdk_content(m)} for m in messages]

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
            except anthropic.APIStatusError as e:
                last_err = e
                if e.status_code not in _RETRYABLE:
                    raise
                _sleep_backoff(attempt)
            except anthropic.APIConnectionError as e:
                last_err = e
                _sleep_backoff(attempt)

        raise RuntimeError(f"Anthropic call failed after {_MAX_RETRIES} attempts") from last_err

    @staticmethod
    def _normalize(resp: Any) -> LLMResponse:
        blocks = parse_blocks(resp.content)
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


# --------------------------------------------------------------------------- #
# OpenAI-compatible implementation (DeepSeek, Ollama, vLLM, etc.)
# --------------------------------------------------------------------------- #
class OpenAICompatibleClient:
    """Any OpenAI-compatible API server.

    Works with DeepSeek, Ollama, vLLM, Together, etc. — anything that speaks
    the OpenAI chat completions protocol.

    Env vars:
      LLM_API_KEY      — the API key (required)
      LLM_BASE_URL     — base URL (default: https://api.deepseek.com)
      LLM_MODEL        — default model (default: deepseek-chat)
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
    ) -> None:
        from openai import OpenAI

        key = api_key or os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError(
                "LLM_API_KEY (or DEEPSEEK_API_KEY) is not set. "
                "Copy .env.example to .env and fill it in."
            )
        url = base_url or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
        self._default_model = default_model or os.environ.get("LLM_MODEL", "deepseek-chat")
        self._client = OpenAI(api_key=key, base_url=url)

    def ask(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[dict[str, Any]],
        config: AgentConfig,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Translate our neutral types → OpenAI format, call, translate back."""
        from openai import APIStatusError

        # --- build OpenAI messages ---
        # OpenAI puts system prompt as a message; Anthropic uses a separate param.
        sdk_messages: list[dict[str, Any]] = []
        if system.strip():
            sdk_messages.append({"role": "system", "content": system})

        for msg in messages:
            if msg.role == "assistant":
                sdk_messages.append(self._assistant_to_sdk(msg))
            else:
                # user turn — may expand to MULTIPLE OpenAI messages, because
                # OpenAI requires each tool_result as a separate role:"tool"
                # message (Anthropic packs them into one user turn).
                sdk_messages.extend(self._user_to_sdk(msg))

        # --- convert tool defs (Anthropic → OpenAI format) ---
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

        # --- call with retry ---
        model = config.model or self._default_model
        last_err: Exception | None = None
        requested_response_format = response_format
        for attempt in range(_MAX_RETRIES):
            try:
                kwargs: dict[str, Any] = dict(
                    model=model,
                    max_tokens=config.max_tokens,
                    temperature=config.temperature,
                    messages=sdk_messages,
                )
                if openai_tools:
                    kwargs["tools"] = openai_tools
                if requested_response_format:
                    kwargs["response_format"] = requested_response_format
                resp = self._client.chat.completions.create(**kwargs)
                return self._normalize(resp)
            except APIStatusError as e:
                last_err = e
                if _should_fallback_to_json_object(e, requested_response_format):
                    requested_response_format = {"type": "json_object"}
                    continue
                if e.status_code not in _RETRYABLE:
                    raise
                _sleep_backoff(attempt)
            except Exception as e:
                last_err = e
                _sleep_backoff(attempt)

        raise RuntimeError(f"OpenAI-compat call failed after {_MAX_RETRIES} attempts") from last_err

    # ---- wire-format translators ---- #

    @staticmethod
    def _assistant_to_sdk(msg: Message) -> dict[str, Any]:
        """Convert our assistant Message to an OpenAI assistant message dict."""
        content_parts: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []

        for blk in msg.content:
            if isinstance(blk, TextBlock):
                content_parts.append({"type": "text", "text": blk.text})
            elif isinstance(blk, ToolUseBlock):
                # OpenAI puts tool calls in a separate array.
                tool_calls.append(
                    {
                        "id": blk.id,
                        "type": "function",
                        "function": {
                            "name": blk.name,
                            "arguments": json.dumps(blk.input, ensure_ascii=False),
                        },
                    }
                )

        out: dict[str, Any] = {"role": "assistant"}
        # OpenAI requires `content` to be present even when null (tool_calls only).
        out["content"] = content_parts if content_parts else None
        if tool_calls:
            out["tool_calls"] = tool_calls
        return out

    @staticmethod
    def _user_to_sdk(msg: Message) -> list[dict[str, Any]]:
        """Convert our user Message to one or more OpenAI messages.

        Anthropic packs multiple tool results into a single user turn as
        content blocks. OpenAI instead requires each tool result as a
        SEPARATE ``role: "tool"`` message, each keyed by ``tool_call_id``.
        So one of our Messages may expand to several OpenAI messages:
        optionally a user text message, then one tool message per result.

        Returns a list (always) so the caller can ``extend`` unconditionally.
        """
        out: list[dict[str, Any]] = []
        text_parts: list[str] = []

        for blk in msg.content:
            if isinstance(blk, TextBlock):
                text_parts.append(blk.text)
            elif isinstance(blk, ToolResultBlock):
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": blk.tool_use_id,
                        "content": blk.content,
                    }
                )

        # A user turn with only tool results has no text; a turn with text
        # (e.g. the initial task) has only text. Either way, emit text first.
        if text_parts:
            out.insert(0, {"role": "user", "content": "\n".join(text_parts)})
        return out

    @staticmethod
    def _normalize(resp: Any) -> LLMResponse:
        """Convert OpenAI ChatCompletion response to our LLMResponse."""
        choice = resp.choices[0]
        message = choice.message

        # Build our content blocks from the OpenAI response.
        blocks: list[Any] = []

        # Text content.
        if message.content:
            blocks.append(TextBlock(text=message.content))

        # Tool calls → ToolUseBlock.
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                args = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {"raw": tc.function.arguments}
                blocks.append(
                    ToolUseBlock(
                        id=tc.id,
                        name=tc.function.name,
                        input=args,
                    )
                )

        # Stop reason mapping.
        finish = getattr(choice, "finish_reason", None) or "stop"
        stop_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        stop: StopReason = stop_map.get(finish, "end_turn")

        # Token usage.
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage else 0

        return LLMResponse(
            assistant=Message(role="assistant", content=blocks),
            stop_reason=stop,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def create_client(provider: str | None = None) -> LLMClient:
    """Create an LLM client based on the ``LLM_PROVIDER`` env var.

    Providers:
      ``anthropic``     — native Anthropic SDK (default)
      ``openai-compat`` — OpenAI-compatible (DeepSeek, Ollama, vLLM, …)

    Falls back to ``anthropic`` if the env var is not set.
    """
    p = (provider or os.environ.get("LLM_PROVIDER", "anthropic")).lower()

    if p == "anthropic":
        return AnthropicClient()  # type: ignore[return-value]
    if p in ("openai-compat", "deepseek", "ollama", "openai"):
        return OpenAICompatibleClient()  # type: ignore[return-value]

    raise ValueError(
        f"Unknown LLM provider: {p!r}. Set LLM_PROVIDER to 'anthropic' or 'openai-compat'."
    )
