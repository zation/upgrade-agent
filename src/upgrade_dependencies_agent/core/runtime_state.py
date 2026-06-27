"""Runtime state used by loop-level guardrails."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from .types import ToolResult, ToolUseBlock

MUTATING_FS_TOOLS = {"write_file", "edit_file"}
SCOPED_PATH_TOOLS = {*MUTATING_FS_TOOLS, "revert_files"}


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
    if not allowed_files or call.name not in SCOPED_PATH_TOOLS:
        return None

    requested_paths = _requested_mutation_paths(call)
    allowed = {_normalize_relative_path(path) for path in allowed_files}
    outside_scope = sorted(path for path in requested_paths if path not in allowed)
    if not outside_scope:
        return None

    return ToolResult(
        output=(
            "Tool call blocked by runtime guardrail: file mutation target "
            f"'{', '.join(outside_scope)}' is outside the allowed mutation scope. Allowed files: "
            f"{', '.join(sorted(allowed))}."
        ),
        is_error=True,
        metadata={
            "guardrail": "allowed_files_scope",
            "requested_paths": requested_paths,
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


def shell_safety_guardrail(call: ToolUseBlock, workdir: str) -> ToolResult | None:
    """Block shell commands that can escape the target project or hide changes."""
    if call.name != "run_command":
        return None
    command = str(call.input.get("command", ""))
    reason = _unsafe_shell_reason(command, workdir)
    if reason is None:
        return None
    return ToolResult(
        output=f"Tool call blocked by runtime guardrail: unsafe shell command. {reason}",
        is_error=True,
        metadata={"guardrail": "unsafe_shell_command", "command": command, "reason": reason},
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


def _requested_mutation_paths(call: ToolUseBlock) -> list[str]:
    if call.name in MUTATING_FS_TOOLS:
        return [_normalize_relative_path(str(call.input.get("path", "")))]
    if call.name == "revert_files":
        paths = call.input.get("paths", [])
        if not isinstance(paths, list):
            return [""]
        return [_normalize_relative_path(str(path)) for path in paths]
    return []


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


def _unsafe_shell_reason(command: str, workdir: str) -> str | None:
    normalized = " ".join(command.strip().split())
    if re.search(r"\bgit\s+stash\b", normalized):
        return "Use structured revert or package-level snapshots instead of git stash."
    if re.search(r"\bsudo\b", normalized):
        return "sudo is not allowed."
    if re.search(r"\b(curl|wget)\b[^|;&]*\|\s*(sh|bash|zsh)\b", normalized):
        return "Piping remote content into a shell is not allowed."
    if re.search(r"\bgit\s+config\s+--global\b", normalized):
        return "Global git configuration is outside the target project."
    if re.search(r"\bnpm\s+config\s+set\b", normalized):
        return "Global or user npm configuration changes are not allowed."
    if re.search(r"(^|[;&|]\s*)rm\s+-[^\n;&|]*[rf][^\n;&|]*\s+(/|~|\$HOME)", normalized):
        return "Broad rm commands against absolute or home paths are not allowed."

    outside_path = _outside_project_write_target(command, workdir)
    if outside_path is not None:
        return f"Shell command writes outside the target project: {outside_path}"
    return None


def _outside_project_write_target(command: str, workdir: str) -> str | None:
    root = Path(workdir).resolve()
    for raw_path in _shell_write_targets(command):
        path = raw_path.strip().strip("'\"")
        if not path or path.startswith(("-", "$")):
            continue
        if path.startswith("~"):
            return path
        candidate = Path(path)
        if not candidate.is_absolute():
            continue
        try:
            candidate.resolve(strict=False).relative_to(root)
        except ValueError:
            return path
    return None


def _shell_write_targets(command: str) -> list[str]:
    targets: list[str] = []
    targets.extend(
        match.group("path")
        for match in re.finditer(r"(?:^|\s)>>?\s*(?P<path>[^\s;&|]+)", command)
    )
    targets.extend(
        match.group("path")
        for match in re.finditer(r"(?:^|\s)tee(?:\s+-a)?\s+(?P<path>[^\s;&|]+)", command)
    )
    targets.extend(
        match.group("path")
        for match in re.finditer(
            r"(?:^|\s)(?:sed\s+-i(?:\s+''|\s+\"\"|\s+\S+)?|perl\s+-p?i(?:\s+\S+)?)\s+.*?\s(?P<path>/[^\s;&|]+)",
            command,
        )
    )
    targets.extend(
        match.group("path")
        for match in re.finditer(r"(?:^|\s)(?:cp|mv)\s+\S+\s+(?P<path>/[^\s;&|]+)", command)
    )
    return targets


def _normalize_relative_path(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/").strip())
    return normalized.removeprefix("./")
