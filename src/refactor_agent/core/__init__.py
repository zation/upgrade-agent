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
    "AgentConfig",
    "ContentBlock",
    "ContextBudget",
    "LLMClient",
    "LLMResponse",
    "LoopCallbacks",
    "LoopResult",
    "Message",
    "ReActLoop",
    "TextBlock",
    "Tool",
    "ToolContext",
    "ToolImpl",
    "ToolResult",
    "ToolResultBlock",
    "ToolUseBlock",
    "Tracer",
    "ask",
    "compact_history",
    "estimate_tokens",
    "needs_compaction",
]
