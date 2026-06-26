"""Tests for the Typer CLI surface."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from typer.testing import CliRunner

import upgrade_dependencies_agent.cli as cli
import upgrade_dependencies_agent.skills as skills
from upgrade_dependencies_agent.cli import app
from upgrade_dependencies_agent.skills import BREAKING_CHANGE_RESEARCHER, UPGRADE_ALL


def test_help_lists_upgrade_all_command():
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "upgrade-dependencies-agent" in result.output
    assert "analyze-coverage" in result.output
    assert "generate-tests" in result.output
    assert "research-upgrade" in result.output
    assert "upgrade-all" in result.output
    assert "upgrade-graph" not in result.output


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
    assert "test/*.test.js" in skills.ADD_TESTS_GENERATE
    assert "If no npm test script exists" in skills.ADD_TESTS_GENERATE
    assert "Run npm test" in skills.ADD_TESTS_GENERATE
    assert "coverage improves" in skills.ADD_TESTS_GENERATE


def test_upgrade_graph_cli_is_removed(tmp_path):
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade-graph", str(tmp_path), "mocha 4 -> 11"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_upgrade_cli_uses_backbone_workflow(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def fake_workflow(target: str, *, max_heal_attempts: int, run_loop):
        calls["target"] = target
        calls["max_heal_attempts"] = max_heal_attempts
        calls["run_loop"] = run_loop
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(cli, "run_upgrade_backbone_workflow", fake_workflow, raising=False)
    monkeypatch.setattr(cli, "create_client", lambda: object())
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade", str(tmp_path), "mocha 4 -> 11"])

    assert result.exit_code == 0
    assert calls["target"] == "mocha 4 -> 11"
    assert calls["max_heal_attempts"] == 1
    assert callable(calls["run_loop"])


def test_upgrade_cli_writes_structured_report(monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"

    def fake_workflow(target: str, *, max_heal_attempts: int, run_loop):
        return SimpleNamespace(
            ok=True,
            report=SimpleNamespace(
                model_dump=lambda mode="python": {
                    "ok": True,
                    "summary": f"upgraded {target}",
                    "changed_files": ["package.json"],
                    "remaining_risks": [],
                }
            ),
        )

    monkeypatch.setattr(cli, "run_upgrade_backbone_workflow", fake_workflow, raising=False)
    monkeypatch.setattr(cli, "create_client", lambda: object())
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["upgrade", str(tmp_path), "mocha 4 -> 11", "--report-json", str(report_path)],
    )

    assert result.exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report == {
        "ok": True,
        "summary": "upgraded mocha 4 -> 11",
        "changed_files": ["package.json"],
        "remaining_risks": [],
    }


def test_upgrade_all_cli_uses_batch_backbone_workflow(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def fake_workflow(*, max_heal_attempts: int, run_loop):
        calls["max_heal_attempts"] = max_heal_attempts
        calls["run_loop"] = run_loop
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(cli, "run_upgrade_all_backbone_workflow", fake_workflow, raising=False)
    monkeypatch.setattr(cli, "create_client", lambda: object())
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade-all", str(tmp_path)])

    assert result.exit_code == 0
    assert calls["max_heal_attempts"] == 1
    assert callable(calls["run_loop"])


def test_upgrade_all_cli_writes_structured_report(monkeypatch, tmp_path):
    report_path = tmp_path / "reports" / "upgrade-all.json"

    def fake_workflow(*, max_heal_attempts: int, run_loop):
        return SimpleNamespace(
            ok=True,
            report=SimpleNamespace(
                model_dump=lambda mode="python": {
                    "ok": True,
                    "summary": "batch passed",
                    "changed_files": ["package.json", "package-lock.json"],
                    "remaining_risks": [],
                }
            ),
        )

    monkeypatch.setattr(cli, "run_upgrade_all_backbone_workflow", fake_workflow, raising=False)
    monkeypatch.setattr(cli, "create_client", lambda: object())
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade-all", str(tmp_path), "--report-json", str(report_path)])

    assert result.exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"] == "batch passed"
    assert report["changed_files"] == ["package.json", "package-lock.json"]


def test_stage_loop_runner_passes_runtime_scope_to_agent_config(monkeypatch):
    calls: dict[str, object] = {}

    class FakeLoop:
        def __init__(self, *, client, config, tools, workdir, callbacks):
            calls["config"] = config

        def run(self, task):
            calls["task"] = task
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(cli, "ReActLoop", FakeLoop)
    runner = cli._make_stage_loop_runner(
        client=object(),
        model="test-model",
        max_iterations=3,
        workdir="/tmp/project",
        ui=SimpleNamespace(),
    )

    runner(
        cli.StageLoopRequest(
            stage="execute",
            system_prompt="prompt",
            task="upgrade mocha",
            enforce_baseline_guardrail=True,
            current_dependency="mocha",
            allowed_files=("package.json", "package-lock.json"),
        )
    )

    config = calls["config"]
    assert config.current_dependency == "mocha"
    assert config.allowed_files == ("package.json", "package-lock.json")


def test_stage_loop_runner_blocks_dirty_worktree_before_first_mutation(
    monkeypatch,
    tmp_path,
):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    calls: dict[str, object] = {}

    class FakeLoop:
        def __init__(self, *, client, config, tools, workdir, callbacks):
            calls["constructed"] = True

        def run(self, task):
            calls["ran"] = True
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(cli, "ReActLoop", FakeLoop)
    runner = cli._make_stage_loop_runner(
        client=object(),
        model="test-model",
        max_iterations=3,
        workdir=str(tmp_path),
        ui=SimpleNamespace(),
    )

    result = runner(
        cli.StageLoopRequest(
            stage="execute",
            system_prompt="prompt",
            task="upgrade mocha",
            enforce_baseline_guardrail=True,
        )
    )

    assert not result.ok
    assert result.error == "dirty_worktree"
    assert "worktree is not clean" in result.final_text
    assert "constructed" not in calls
