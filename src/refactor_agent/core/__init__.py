"""The hand-written agent core: model-agnostic, task-agnostic.

These are the primitives the ReAct pattern needs. Nothing here knows about
"dependencies" or "chai" — that's all in ``tools/`` and ``skills/``.
"""

from __future__ import annotations

from .context import ContextBudget, compact_history, estimate_tokens, needs_compaction
from .llm_client import LLMClient, LLMResponse, ask
from .react_loop import LoopCallbacks, LoopResult, ReActLoop
from .trace import Tracer
from .types import (
    AgentConfig,
    ContentBlock,
    Message,
    TextBlock,
    Tool,
    ToolContext,
    ToolImpl,
    ToolResult,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = [
    # loop
    "ReActLoop",
    "LoopResult",
    "LoopCallbacks",
    "AgentConfig",
    # llm
    "LLMClient",
    "LLMResponse",
    "ask",
    # context
    "ContextBudget",
    "estimate_tokens",
    "needs_compaction",
    "compact_history",
    # trace
    "Tracer",
    # types
    "Message",
    "ContentBlock",
    "TextBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "Tool",
    "ToolImpl",
    "ToolContext",
    "ToolResult",
]
