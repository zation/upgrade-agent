"""Tests for workflow preflight checks."""

from __future__ import annotations

import subprocess

from upgrade_dependencies_agent.orchestrator.preflight import check_clean_worktree


def test_check_clean_worktree_reports_dirty_status(tmp_path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    result = check_clean_worktree(str(tmp_path))

    assert not result.ok
    assert result.reason == "dirty_worktree"
    assert "package.json" in result.details


def test_check_clean_worktree_allows_non_git_directory(tmp_path) -> None:
    result = check_clean_worktree(str(tmp_path))

    assert result.ok
    assert result.reason is None
