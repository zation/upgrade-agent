"""Tests for context compaction (no network, no model)."""

from __future__ import annotations

from upgrade_dependencies_agent.core.context import (
    ContextBudget,
    compact_history,
    estimate_tokens,
    needs_compaction,
)
from upgrade_dependencies_agent.core.types import Message, TextBlock


def _msgs(n: int) -> list[Message]:
    return [Message(role="user", content=[TextBlock(text="x" * 100)]) for _ in range(n)]


def test_compaction_keeps_short_history_intact() -> None:
    msgs = _msgs(4)
    budget = ContextBudget(keep_turns=6)
    assert compact_history(msgs, budget) is msgs  # unchanged, short enough


def test_compaction_inserts_summary_and_keeps_tail() -> None:
    msgs = _msgs(20)
    budget = ContextBudget(keep_turns=4)
    out = compact_history(msgs, budget)
    # head + summary + keep_turns
    assert len(out) == 1 + 1 + 4
    # tail preserved verbatim
    assert out[-1] is msgs[-1]
    stub = out[1].text()
    assert "baseline" in stub
    assert "changed files" in stub
    assert "failure reason" in stub
    assert "verification" in stub
    assert "remaining TODO" in stub


def test_needs_compaction_threshold() -> None:
    msgs = _msgs(10)  # 10 * 100 chars / 4 ≈ 250 tokens
    assert needs_compaction(msgs, ContextBudget(input_budget=200))
    assert not needs_compaction(msgs, ContextBudget(input_budget=10_000))


def test_estimate_tokens_is_positive() -> None:
    assert estimate_tokens(_msgs(1)) > 0
