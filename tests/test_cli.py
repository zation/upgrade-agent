"""Tests for the Typer CLI surface."""

from __future__ import annotations

from typer.testing import CliRunner

import refactor_agent.skills as skills
from refactor_agent.cli import app
from refactor_agent.skills import BREAKING_CHANGE_RESEARCHER, UPGRADE_ALL


def test_help_lists_upgrade_all_command():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "analyze-coverage" in result.output
    assert "generate-tests" in result.output
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


def test_add_tests_analyze_prompt_is_read_only_gap_finder():
    assert hasattr(skills, "ADD_TESTS_ANALYZE")
    assert "test gap list" in skills.ADD_TESTS_ANALYZE
    assert "coverage report" in skills.ADD_TESTS_ANALYZE
    assert "Do not edit files" in skills.ADD_TESTS_ANALYZE
    assert "file / function / suggested test scenarios" in skills.ADD_TESTS_ANALYZE


def test_add_tests_generate_prompt_requires_existing_style_and_verification():
    assert hasattr(skills, "ADD_TESTS_GENERATE")
    assert "generate tests" in skills.ADD_TESTS_GENERATE
    assert "Follow the existing test style" in skills.ADD_TESTS_GENERATE
    assert "Run npm test" in skills.ADD_TESTS_GENERATE
    assert "coverage improves" in skills.ADD_TESTS_GENERATE
