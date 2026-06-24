"""Context window management.

The naive approach — keep every message forever — breaks on long runs: tool
outputs (file dumps, command stdout) blow past the model's context window and
the call 400s. This module owns two responsibilities:

1. **Estimate** token usage of the current message list (cheap heuristic —
   we don't pull in a tokenizer; ~4 chars/token is within 10% and instant).
2. **Compact** the history when it nears a budget: keep the system prompt and
   the most recent N turns verbatim, and replace everything older with a short
   summary stub. The model never sees a hole — the stub explains it.

This is deliberately a *strategy* the ReAct loop can opt into, not something
baked into the loop. Early runs won't need it; long upgrade sessions will.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import Message, TextBlock, ToolResultBlock

__all__ = ["ContextBudget", "compact_history", "estimate_tokens"]

# Rough chars-per-token for mixed English code/json. Over-estimating slightly
# is safe (we compact earlier than strictly necessary); under-estimating is not.
_CHARS_PER_TOKEN = 4.0

# Default budget leaves headroom under Claude's 200k window for the response.
DEFAULT_INPUT_BUDGET = 150_000  # tokens

# Never compact below this many turns of recent history — we always keep the
# "working memory" the model is actively reasoning about intact.
_MIN_KEEP_TURNS = 6


@dataclass
class ContextBudget:
    """Describes the compaction policy.

    ``input_budget`` is the soft cap in estimated tokens; ``keep_turns`` is how
    many recent *message pairs* to preserve verbatim when compacting.
    """

    input_budget: int = DEFAULT_INPUT_BUDGET
    keep_turns: int = _MIN_KEEP_TURNS


def estimate_tokens(messages: list[Message]) -> int:
    """Cheap token estimate for the whole conversation."""
    total_chars = 0
    for msg in messages:
        for blk in msg.content:
            if isinstance(blk, TextBlock):
                total_chars += len(blk.text)
            elif isinstance(blk, ToolResultBlock):
                total_chars += len(blk.content)
            else:  # tool_use: serialize the args roughly
                total_chars += len(str(getattr(blk, "input", "")))
    return int(total_chars / _CHARS_PER_TOKEN)


def needs_compaction(messages: list[Message], budget: ContextBudget) -> bool:
    """True when current history exceeds the soft budget."""
    return estimate_tokens(messages) > budget.input_budget


def compact_history(
    messages: list[Message], budget: ContextBudget, summary: str | None = None
) -> list[Message]:
    """Return a possibly-shortened copy of ``messages``.

    Strategy: keep the first user message (the task) and the last
    ``keep_turns`` messages; insert one summary message where the omitted
    middle was. If history is already short, returns it unchanged.

    ``summary`` lets a caller (e.g. a reflection step) supply a richer summary
    of the dropped work; if None we use a generic placeholder noting that older
    turns were elided.
    """
    if len(messages) <= budget.keep_turns + 1:
        return messages

    head = messages[0]
    tail = messages[-budget.keep_turns :]

    dropped = len(messages) - 1 - budget.keep_turns
    stub_text = summary or (
        f"[context compaction] {dropped} earlier message(s) omitted to fit the "
        "context window. They contained tool exploration whose conclusions are "
        "summarized above; re-investigate only if a specific detail is needed."
    )
    stub = Message(role="user", content=[TextBlock(text=stub_text)])
    return [head, stub, *tail]
