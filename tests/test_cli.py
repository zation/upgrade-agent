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

    def fake_workflow(target: str, *, max_heal_attempts: int, run_loop, collect_changed_files):
        calls["target"] = target
        calls["max_heal_attempts"] = max_heal_attempts
        calls["run_loop"] = run_loop
        calls["collect_changed_files"] = collect_changed_files
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(cli, "run_upgrade_backbone_workflow", fake_workflow, raising=False)
    monkeypatch.setattr(cli, "create_client", lambda: object())
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade", str(tmp_path), "mocha 4 -> 11"])

    assert result.exit_code == 0
    assert calls["target"] == "mocha 4 -> 11"
    assert calls["max_heal_attempts"] == 1
    assert callable(calls["run_loop"])
    assert callable(calls["collect_changed_files"])


def test_upgrade_cli_writes_structured_report(monkeypatch, tmp_path):
    report_path = tmp_path / "report.json"

    def fake_workflow(target: str, *, max_heal_attempts: int, run_loop, collect_changed_files):
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


def test_upgrade_cli_prints_machine_readable_json(monkeypatch, tmp_path):
    def fake_workflow(target: str, *, max_heal_attempts: int, run_loop, collect_changed_files):
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

    result = runner.invoke(app, ["upgrade", str(tmp_path), "mocha 4 -> 11", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "ok": True,
        "summary": "upgraded mocha 4 -> 11",
        "changed_files": ["package.json"],
        "remaining_risks": [],
    }


def test_upgrade_cli_dry_run_uses_read_only_planner(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def fake_dry_run_workflow(target: str, *, run_loop):
        calls["target"] = target
        calls["run_loop"] = run_loop
        return SimpleNamespace(
            ok=True,
            report=SimpleNamespace(
                model_dump=lambda mode="python": {
                    "ok": True,
                    "summary": "dry run complete",
                    "changed_files": [],
                    "remaining_risks": ["node minimum changed"],
                }
            ),
        )

    def fail_mutating_workflow(*args, **kwargs):
        raise AssertionError("mutating workflow must not run during dry-run")

    monkeypatch.setattr(cli, "run_upgrade_dry_run_workflow", fake_dry_run_workflow, raising=False)
    monkeypatch.setattr(cli, "run_upgrade_backbone_workflow", fail_mutating_workflow, raising=False)
    monkeypatch.setattr(cli, "create_client", lambda: object())
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade", str(tmp_path), "mocha 4 -> 11", "--dry-run", "--json"])

    assert result.exit_code == 0
    assert calls["target"] == "mocha 4 -> 11"
    assert callable(calls["run_loop"])
    assert json.loads(result.output)["summary"] == "dry run complete"


def test_upgrade_cli_runs_explicit_dependency_list_sequentially(monkeypatch, tmp_path):
    calls: list[str] = []

    def fake_workflow(target: str, *, max_heal_attempts: int, run_loop, collect_changed_files):
        calls.append(target)
        return SimpleNamespace(
            ok=True,
            heal_attempts=0,
            history=("baseline", "research", "report"),
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

    result = runner.invoke(app, ["upgrade", str(tmp_path), "mocha, nyc", "--json"])

    assert result.exit_code == 0
    assert calls == ["mocha", "nyc"]
    report = json.loads(result.output)
    assert report["ok"] is True
    assert report["summary"] == "Upgraded 2 explicit dependencies: mocha, nyc"
    assert report["changed_files"] == ["package.json"]


def test_upgrade_cli_reports_failed_explicit_dependency(monkeypatch, tmp_path):
    def fake_workflow(target: str, *, max_heal_attempts: int, run_loop, collect_changed_files):
        ok = target == "mocha"
        return SimpleNamespace(
            ok=ok,
            heal_attempts=0,
            history=("baseline", "research", "report"),
            report=SimpleNamespace(
                model_dump=lambda mode="python": {
                    "ok": ok,
                    "summary": f"{target} {'passed' if ok else 'failed'}",
                    "changed_files": ["package.json"],
                    "remaining_risks": [] if ok else ["nyc verification failed"],
                    "failure_reason": None if ok else "verification_failed",
                    "recovery_suggestions": [] if ok else ["Inspect nyc"],
                }
            ),
        )

    monkeypatch.setattr(cli, "run_upgrade_backbone_workflow", fake_workflow, raising=False)
    monkeypatch.setattr(cli, "create_client", lambda: object())
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade", str(tmp_path), "mocha, nyc", "--json"])

    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["ok"] is False
    assert report["failure_reason"] == "explicit_dependency_failed"
    assert "nyc: nyc verification failed" in report["remaining_risks"]


def test_upgrade_all_cli_uses_batch_backbone_workflow(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def fake_workflow(*, max_heal_attempts: int, run_loop, collect_changed_files):
        calls["max_heal_attempts"] = max_heal_attempts
        calls["run_loop"] = run_loop
        calls["collect_changed_files"] = collect_changed_files
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(cli, "run_upgrade_all_backbone_workflow", fake_workflow, raising=False)
    monkeypatch.setattr(cli, "create_client", lambda: object())
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade-all", str(tmp_path)])

    assert result.exit_code == 0
    assert calls["max_heal_attempts"] == 1
    assert callable(calls["run_loop"])
    assert callable(calls["collect_changed_files"])


def test_upgrade_all_cli_writes_structured_report(monkeypatch, tmp_path):
    report_path = tmp_path / "reports" / "upgrade-all.json"

    def fake_workflow(*, max_heal_attempts: int, run_loop, collect_changed_files):
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


def test_upgrade_all_cli_prints_machine_readable_json(monkeypatch, tmp_path):
    def fake_workflow(*, max_heal_attempts: int, run_loop, collect_changed_files):
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

    result = runner.invoke(app, ["upgrade-all", str(tmp_path), "--json"])

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["summary"] == "batch passed"
    assert report["changed_files"] == ["package.json", "package-lock.json"]


def test_upgrade_all_cli_dry_run_uses_read_only_planner(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def fake_dry_run_workflow(*, run_loop):
        calls["run_loop"] = run_loop
        return SimpleNamespace(
            ok=True,
            report=SimpleNamespace(
                model_dump=lambda mode="python": {
                    "ok": True,
                    "summary": "batch dry run complete",
                    "changed_files": [],
                    "remaining_risks": ["Planned packages: mocha"],
                }
            ),
        )

    def fail_mutating_workflow(*args, **kwargs):
        raise AssertionError("mutating workflow must not run during dry-run")

    monkeypatch.setattr(
        cli,
        "run_upgrade_all_dry_run_workflow",
        fake_dry_run_workflow,
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "run_upgrade_all_backbone_workflow",
        fail_mutating_workflow,
        raising=False,
    )
    monkeypatch.setattr(cli, "create_client", lambda: object())
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade-all", str(tmp_path), "--dry-run", "--json"])

    assert result.exit_code == 0
    assert callable(calls["run_loop"])
    assert json.loads(result.output)["summary"] == "batch dry run complete"


def test_write_report_json_populates_changed_files_from_workdir(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    package_json = tmp_path / "package.json"
    package_json.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    package_json.write_text('{"changed": true}', encoding="utf-8")

    report_path = tmp_path / "agent-report.json"
    result = SimpleNamespace(
        report=SimpleNamespace(
            model_dump=lambda mode="python": {
                "ok": True,
                "summary": "done",
                "changed_files": [],
                "remaining_risks": [],
            }
        )
    )

    cli._write_report_json(result, report_path, workdir=str(tmp_path))

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["changed_files"] == ["package.json"]


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
            max_iterations=7,
            response_format={"type": "json_object"},
        )
    )

    config = calls["config"]
    assert config.max_iterations == 7
    assert config.current_dependency == "mocha"
    assert config.allowed_files == ("package.json", "package-lock.json")
    assert config.response_format == {"type": "json_object"}


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
