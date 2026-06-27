"""Tests for the Rich CLI progress renderer."""

from __future__ import annotations

from rich.console import Console

from upgrade_dependencies_agent.cli.ui import RichUI
from upgrade_dependencies_agent.core.llm_client import LLMResponse
from upgrade_dependencies_agent.core.react_loop import LoopResult
from upgrade_dependencies_agent.core.types import Message, TextBlock, ToolResult


def _response(*, input_tokens: int = 10, output_tokens: int = 5) -> LLMResponse:
    return LLMResponse(
        assistant=Message(role="assistant", content=[TextBlock(text="thinking")]),
        stop_reason="tool_use",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def test_rich_ui_shows_elapsed_time_during_iterations() -> None:
    ticks = iter([10.0, 11.25])
    console = Console(record=True, force_terminal=False, width=100)
    ui = RichUI(console=console, clock=lambda: next(ticks))

    ui.on_iteration(1, _response())
    ui.on_iteration(2, _response(input_tokens=20, output_tokens=8))

    output = console.export_text()
    assert "elapsed 1.2s" in output
    assert "iteration 2" in output


def test_rich_ui_finish_summarizes_tool_progress() -> None:
    ticks = iter([0.0, 12.0])
    console = Console(record=True, force_terminal=False, width=100)
    ui = RichUI(console=console, clock=lambda: next(ticks))

    ui.on_iteration(1, _response())
    ui.on_tool_call("read_file", {"path": "package.json"})
    ui.on_tool_result("read_file", ToolResult(output="ok"))
    ui.on_tool_call("npm_outdated", {})
    ui.on_tool_result("npm_outdated", ToolResult(output="registry failed", is_error=True))
    ui.on_finish(
        LoopResult(
            final_text="final report",
            stop_reason="end_turn",
            iterations=1,
            messages=[],
            run_id="test",
            total_input_tokens=10,
            total_output_tokens=5,
        )
    )

    output = console.export_text()
    assert "elapsed: 12.0s" in output
    assert "tools:  2 calls / 1 ok / 1 error" in output
