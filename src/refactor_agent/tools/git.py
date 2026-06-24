"""Git tools.

The agent uses git to inspect repo state (what changed, what's untracked) and
to checkpoint work. We wrap the porcelain commands we need rather than letting
the model run arbitrary git via ``run_command`` — that keeps commit messages,
branch handling, etc. structured and reviewable.

For M1 we expose read-only inspection; committing is added later once we trust
the agent's edits.
"""

from __future__ import annotations

from typing import Any

import git

from ..core.types import ToolImpl, ToolResult

__all__ = ["GitDiff", "GitStatus"]


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
