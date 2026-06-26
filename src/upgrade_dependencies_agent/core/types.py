"""Core types for the agent framework.

Kept deliberately model-agnostic and task-agnostic. The only place that knows
about Anthropic's wire format is :mod:`upgrade_dependencies_agent.core.llm_client`; everything
else (the ReAct loop, tools, traces) operates on the neutral types defined here.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, Field

__all__ = [
    "AgentConfig",
    "ContentBlock",
    "Message",
    "Role",
    "StopReason",
    "TextBlock",
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolResultBlock",
    "ToolUseBlock",
]

T = TypeVar("T", bound="ContentBlock")

# A message role as the loop sees it. We only ever send `user` turns to the model
# (assistant turns come back from it), so the set is intentionally small.
Role = Literal["user", "assistant"]

StopReason = Literal[
    "end_turn",  # model finished naturally
    "tool_use",  # model wants to call tools
    "max_tokens",  # we cut it off
    "stop_sequence",
    "error",  # synthesized by us when the call raised
]


# --------------------------------------------------------------------------- #
# Content blocks
# --------------------------------------------------------------------------- #
# Anthropic returns content as a list of typed blocks. We mirror that shape but
# with our own discriminated union so the rest of the codebase never imports
# SDK types directly. Three kinds matter to us: plain text, a tool call the
# model emitted, and the result we hand back for a tool call.


class ContentBlock(BaseModel):
    """Base for every block in a message's ``content`` list."""

    type: str


class TextBlock(ContentBlock):
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(ContentBlock):
    """A tool call the model produced. ``id`` ties it to the matching result."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    # We keep this as raw JSON (Any) rather than a typed dict because every tool
    # has its own input schema; validation happens in the Tool itself.
    input: dict[str, Any]


class ToolResultBlock(ContentBlock):
    """What we hand back to the model for a previous :class:`ToolUseBlock`."""

    type: Literal["tool_result"] = "tool_result"
    # Matches the id of the ToolUseBlock it answers.
    tool_use_id: str
    # We store the rendered string the model sees, plus the structured value for
    # traces/debugging. ``is_error`` lets us surface tool failures distinctly.
    content: str
    is_error: bool = False


class Message(BaseModel):
    """One turn in the conversation. Content is a list of typed blocks."""

    role: Role
    content: list[ContentBlock] = Field(default_factory=list)

    def text(self) -> str:
        """Convenience: concatenate all text blocks into one string."""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


class ToolResult(BaseModel):
    """Structured return value from executing a tool."""

    output: str  # what the model will see (rendered to text)
    is_error: bool = False
    # Optional structured payload kept only for tracing/debugging; never sent
    # to the model in full to avoid blowing up the context.
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolContext(BaseModel):
    """Execution-time context passed into every tool call.

    Holds the absolute working directory the agent operates on (the target
    project root) and a place for per-run scratch data. Tools should treat
    ``workdir`` as the root for any relative paths they receive.
    """

    model_config = {"arbitrary_types_allowed": True}

    workdir: str
    run_id: str


@runtime_checkable
class Tool(Protocol):
    """The minimal contract every tool satisfies.

    A tool declares a name, a description, a JSON-Schema for its inputs, and a
    ``run`` callable. We use a Protocol (duck typing) instead of inheritance so
    tools can be simple functions when that's clearer, or dataclasses when they
    need config — see ``tools/`` for both styles.
    """

    name: str
    description: str
    # JSON Schema (draft 2020-12). Anthropic accepts this directly as the
    # ``input_schema`` of a tool definition.
    input_schema: dict[str, Any]

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...


class ToolImpl(ABC):
    """Convenience base class for tools that prefer inheritance.

    Subclasses define ``name``, ``description``, ``input_schema`` and implement
    :meth:`run`. Using this is optional — the Protocol above is the real
    contract; this just saves boilerplate and gives a clear place to document
    a tool.
    """

    name: str
    description: str
    input_schema: dict[str, Any]

    @abstractmethod
    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult: ...

    # Make the class itself satisfy the Tool Protocol.
    def __call__(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return self.run(args, ctx)


# --------------------------------------------------------------------------- #
# Agent config
# --------------------------------------------------------------------------- #


class AgentConfig(BaseModel):
    """Tunables for one ReAct loop invocation."""

    model: str = "claude-sonnet-4-5"
    system_prompt: str = ""
    max_iterations: int = 25  # safety valve; the loop must always terminate
    max_tokens: int = 4096
    temperature: float = 1.0
    # When True, every block that flows through the loop is appended to a trace.
    trace: bool = True


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def parse_blocks(raw: Sequence[dict[str, Any]]) -> list[ContentBlock]:
    """Parse SDK content dicts into our typed blocks.

    Tolerates anything: unknown block types become a ``TextBlock`` containing
    the raw JSON, so we never throw on a shape the SDK might add later.
    """
    out: list[ContentBlock] = []
    for blk in raw:
        t = blk.get("type")
        if t == "text":
            out.append(TextBlock(text=blk.get("text", "")))
        elif t == "tool_use":
            out.append(
                ToolUseBlock(
                    id=blk["id"],
                    name=blk["name"],
                    input=blk.get("input") or {},
                )
            )
        elif t == "tool_result":
            content = blk.get("content", "")
            # Anthropic allows content to be a list of sub-blocks; flatten to text.
            if isinstance(content, list):
                content = "".join(sub.get("text", "") for sub in content if isinstance(sub, dict))
            out.append(
                ToolResultBlock(
                    tool_use_id=blk["tool_use_id"],
                    content=str(content),
                    is_error=bool(blk.get("is_error")),
                )
            )
        else:
            # Unknown block — keep it visible as text so it's never silently lost.
            out.append(TextBlock(text=json.dumps(blk, ensure_ascii=False)))
    return out


def to_sdk_content(message: Message) -> list[dict[str, Any]]:
    """Inverse of :func:`parse_blocks` — our blocks back to SDK dicts.

    Only used for the content we send to the model (user tool-result turns and
    replayed assistant turns)."""
    result: list[dict[str, Any]] = []
    for blk in message.content:
        if isinstance(blk, TextBlock):
            result.append({"type": "text", "text": blk.text})
        elif isinstance(blk, ToolUseBlock):
            result.append(
                {
                    "type": "tool_use",
                    "id": blk.id,
                    "name": blk.name,
                    "input": blk.input,
                }
            )
        elif isinstance(blk, ToolResultBlock):
            result.append(
                {
                    "type": "tool_result",
                    "tool_use_id": blk.tool_use_id,
                    "content": blk.content,
                    "is_error": blk.is_error,
                }
            )
        else:
            result.append({"type": blk.type, "text": json.dumps(blk.model_dump())})
    return result
