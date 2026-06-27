"""Git tools.

The agent uses git to inspect repo state (what changed, what's untracked) and
to checkpoint work. We wrap the porcelain commands we need rather than letting
the model run arbitrary git via ``run_command`` — that keeps commit messages,
branch handling, etc. structured and reviewable.

For M1 we expose read-only inspection; committing is added later once we trust
the agent's edits.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import git

from ..core.types import ToolImpl, ToolResult
from ._common import PathEscapeError, safe_resolve

__all__ = ["GitDiff", "GitStatus", "RevertFiles"]


def _repo(workdir: str) -> git.Repo:
    """Open the repo at ``workdir`` (walking up to find .git)."""
    return git.Repo(workdir, search_parent_directories=True)


class GitStatus(ToolImpl):
    name = "git_status"
    description = (
        "Show the working-tree status of the project (changed/new files), "
        "like `git status --short`. Use after edits to confirm what changed."
    )
    input_schema = {"type": "object", "properties": {}}

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        try:
            repo = _repo(ctx.workdir)
        except git.InvalidGitRepositoryError:
            return ToolResult(output="Not a git repository.", is_error=True)
        lines: list[str] = []
        for item in repo.index.diff(None):  # unstaged changes
            lines.append(f" M {item.a_path}")
        for item in repo.index.diff("HEAD"):  # staged vs HEAD
            lines.append(f"M  {item.a_path}")
        untracked = repo.untracked_files
        for u in untracked:
            lines.append(f"?? {u}")
        if not lines:
            return ToolResult(output="Working tree clean (no changes).")
        lines.sort()
        return ToolResult(output="\n".join(lines), metadata={"changed": len(lines)})


class GitDiff(ToolImpl):
    name = "git_diff"
    description = (
        "Show the unified diff of uncommitted changes in the project. Use to "
        "review edits before/after running tests."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "staged": {
                "type": "boolean",
                "description": "If true, show staged (cached) diff. Default false.",
            }
        },
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        staged = args.get("staged", False)
        try:
            repo = _repo(ctx.workdir)
        except git.InvalidGitRepositoryError:
            return ToolResult(output="Not a git repository.", is_error=True)
        diff = repo.git.diff("--cached" if staged else None)
        if not diff.strip():
            return ToolResult(output="No changes.")
        # Cap large diffs so they don't flood the context.
        if len(diff) > 8000:
            diff = diff[:8000] + "\n... [diff truncated] ..."
        return ToolResult(output=diff)


class RevertFiles(ToolImpl):
    name = "revert_files"
    description = (
        "Restore only the listed project files from git HEAD. Use this instead "
        "of broad git reset/checkout/restore commands when reverting a failed "
        "package step."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Relative project file paths to restore from HEAD.",
            }
        },
        "required": ["paths"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        paths = args.get("paths")
        if not isinstance(paths, list) or not paths:
            return ToolResult(
                output="revert_files requires a non-empty paths array.",
                is_error=True,
            )

        root = Path(ctx.workdir).resolve()
        rel_paths: list[str] = []
        for raw_path in paths:
            if not isinstance(raw_path, str):
                return ToolResult(output="All revert_files paths must be strings.", is_error=True)
            try:
                resolved = safe_resolve(ctx.workdir, raw_path)
            except PathEscapeError as e:
                return ToolResult(output=str(e), is_error=True)
            rel_paths.append(resolved.relative_to(root).as_posix())

        try:
            proc = subprocess.run(
                ["git", "restore", "--", *rel_paths],
                cwd=ctx.workdir,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return ToolResult(output=f"git restore failed: {e}", is_error=True)

        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            return ToolResult(
                output=output or "git restore failed.",
                is_error=True,
                metadata={"paths": rel_paths, "exit_code": proc.returncode},
            )
        return ToolResult(
            output=f"Reverted files from HEAD: {', '.join(rel_paths)}",
            metadata={"paths": rel_paths, "exit_code": proc.returncode},
        )
