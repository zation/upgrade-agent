"""Runtime state used by loop-level guardrails."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .types import ToolResult, ToolUseBlock

MUTATING_FS_TOOLS = {"write_file", "edit_file"}


@dataclass
class RuntimeState:
    """Mutable facts learned during one agent run."""

    baseline_ran: bool = False
    baseline_green: bool = False


def baseline_guardrail(call: ToolUseBlock, state: RuntimeState) -> ToolResult | None:
    """Return a blocking result when a mutating call happens before green baseline."""
    if state.baseline_green:
        return None
    if call.name in MUTATING_FS_TOOLS or _is_npm_install(call):
        return ToolResult(
            output=(
                "Tool call blocked by runtime guardrail: establish a green test "
                "baseline before mutating files or installing packages."
            ),
            is_error=True,
            metadata={"guardrail": "baseline_before_mutation"},
        )
    return None


def update_runtime_state(call: ToolUseBlock, result: ToolResult, state: RuntimeState) -> None:
    """Update state from tool results after a call executes."""
    if call.name != "run_command":
        return
    command = str(call.input.get("command", ""))
    if not _looks_like_test_command(command):
        return
    state.baseline_ran = True
    state.baseline_green = result.metadata.get("exit_code") == 0


def _is_npm_install(call: ToolUseBlock) -> bool:
    if call.name != "run_command":
        return False
    command = str(call.input.get("command", ""))
    return bool(re.search(r"\bnpm\s+(install|i|add)\b", command))


def _looks_like_test_command(command: str) -> bool:
    return bool(
        re.search(
            r"\b(npm\s+(test|t|run\s+test)|pnpm\s+test|yarn\s+test)\b",
            command,
        )
    )
