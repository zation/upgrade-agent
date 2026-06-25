"""Tests for the Typer CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

from refactor_agent.cli import app
from refactor_agent.skills import BREAKING_CHANGE_RESEARCHER, UPGRADE_ALL


def test_help_lists_upgrade_all_command():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "research-upgrade" in result.output
    assert "upgrade-all" in result.output
    assert "upgrade-graph" in result.output


def test_breaking_change_researcher_prompt_is_read_only():
    assert "read-only sub-agent" in BREAKING_CHANGE_RESEARCHER
    assert "dependency_research" in BREAKING_CHANGE_RESEARCHER
    assert "VERDICT: LOW" in BREAKING_CHANGE_RESEARCHER
    assert "Do not edit files" in BREAKING_CHANGE_RESEARCHER


def test_upgrade_all_prompt_encodes_incremental_workflow():
    assert "upgrading every direct npm dependency" in UPGRADE_ALL
    assert "upgrade exactly ONE package" in UPGRADE_ALL
    assert "Run npm_outdated" in UPGRADE_ALL
