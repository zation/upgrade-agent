"""Runtime state used by loop-level guardrails."""

from __future__ import annotations

import posixpath
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
    if call.name in MUTATING_FS_TOOLS or _is_package_manager_mutation(call):
        return ToolResult(
            output=(
                "Tool call blocked by runtime guardrail: establish a green test "
                "baseline before mutating files or changing packages."
            ),
            is_error=True,
            metadata={"guardrail": "baseline_before_mutation"},
        )
    return None


def mutation_scope_guardrail(
    call: ToolUseBlock,
    allowed_files: tuple[str, ...],
) -> ToolResult | None:
    """Return a blocking result when a file mutation targets a disallowed path."""
    if not allowed_files or call.name not in MUTATING_FS_TOOLS:
        return None

    requested = _normalize_relative_path(str(call.input.get("path", "")))
    allowed = {_normalize_relative_path(path) for path in allowed_files}
    if requested in allowed:
        return None

    return ToolResult(
        output=(
            "Tool call blocked by runtime guardrail: file mutation target "
            f"'{requested}' is outside the allowed mutation scope. Allowed files: "
            f"{', '.join(sorted(allowed))}."
        ),
        is_error=True,
        metadata={
            "guardrail": "allowed_files_scope",
            "requested_path": requested,
            "allowed_files": sorted(allowed),
        },
    )


def dangerous_revert_guardrail(call: ToolUseBlock) -> ToolResult | None:
    """Block broad git revert commands that could discard unrelated user work."""
    if call.name != "run_command":
        return None
    command = str(call.input.get("command", ""))
    if not _looks_like_dangerous_revert(command):
        return None
    return ToolResult(
        output=(
            "Tool call blocked by runtime guardrail: dangerous revert command. "
            "Use a structured package-level revert path instead of broad git "
            "reset/checkout/restore commands."
        ),
        is_error=True,
        metadata={"guardrail": "dangerous_revert_command", "command": command},
    )


def update_runtime_state(call: ToolUseBlock, result: ToolResult, state: RuntimeState) -> None:
    """Update state from tool results after a call executes."""
    if call.name != "run_command":
        return
    command = str(call.input.get("command", ""))
    if not _looks_like_test_command(command):
        return
    state.baseline_ran = True
    state.baseline_green = result.metadata.get("exit_code") == 0


def _is_package_manager_mutation(call: ToolUseBlock) -> bool:
    if call.name != "run_command":
        return False
    command = str(call.input.get("command", ""))
    normalized = " ".join(command.strip().split())
    return bool(
        re.search(r"\bnpm\s+(install|i|add|update|uninstall|remove|rm)\b", normalized)
        or re.search(r"\bpnpm\s+(add|install|i|update|remove|rm)\b", normalized)
        or re.search(r"\byarn\s+(add|install|upgrade|remove)\b", normalized)
    )


def _looks_like_test_command(command: str) -> bool:
    return bool(
        re.search(
            r"\b(npm\s+(test|t|run\s+test)|pnpm\s+test|yarn\s+test)\b",
            command,
        )
    )


def _looks_like_dangerous_revert(command: str) -> bool:
    normalized = " ".join(command.strip().split())
    return bool(
        re.search(r"\bgit\s+reset\s+--hard\b", normalized)
        or re.search(r"\bgit\s+checkout\s+(--\s+)?(\.|:/)\b", normalized)
        or re.search(r"\bgit\s+restore\s+(\.|:/)\b", normalized)
        or re.search(r"\bgit\s+clean\s+-[^\s]*[dfx][^\s]*\b", normalized)
    )


def _normalize_relative_path(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/").strip())
    return normalized.removeprefix("./")
