"""Reusable workflow preflight checks."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class WorktreePreflightResult:
    """Result of checking target worktree readiness before mutation."""

    ok: bool
    reason: str | None = None
    details: str = ""


def check_clean_worktree(workdir: str) -> WorktreePreflightResult:
    """Require a clean git worktree when the target is a git checkout."""
    status = dirty_worktree_status(workdir)
    if status is None:
        return WorktreePreflightResult(ok=True)
    return WorktreePreflightResult(
        ok=False,
        reason="dirty_worktree",
        details=status,
    )


def dirty_worktree_status(workdir: str) -> str | None:
    """Return ``git status --porcelain`` output, or ``None`` when clean/non-git."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    status = proc.stdout.strip()
    return status or None
