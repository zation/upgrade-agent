"""Tests for the Typer CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

from refactor_agent.cli import app
from refactor_agent.skills import UPGRADE_ALL


def test_help_lists_upgrade_all_command():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "upgrade-all" in result.output
    assert "upgrade-graph" in result.output


def test_upgrade_all_prompt_encodes_incremental_workflow():
    assert "upgrading every direct npm dependency" in UPGRADE_ALL
    assert "upgrade exactly ONE package" in UPGRADE_ALL
    assert "Run npm_outdated" in UPGRADE_ALL
