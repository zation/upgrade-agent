"""Tests for the real upgrade graph workflow wiring."""

from __future__ import annotations

from pathlib import Path

from upgrade_dependencies_agent.core import LoopResult
from upgrade_dependencies_agent.orchestrator.upgrade_workflow import (
    StageLoopRequest,
    run_upgrade_all_backbone_workflow,
    run_upgrade_backbone_workflow,
)


def _result(text: str, *, ok: bool = True) -> LoopResult:
    return LoopResult(
        final_text=text,
        stop_reason="end_turn" if ok else "error",
        iterations=1,
        messages=[],
        run_id="test",
        trace_path=Path("trace.jsonl"),
        error=None if ok else "failed",
    )


def test_upgrade_workflow_runs_backbone_stages_with_expected_loop_contracts() -> None:
    requests: list[StageLoopRequest] = []

    def run_loop(request: StageLoopRequest) -> LoopResult:
        requests.append(request)
        if "Verify the dependency upgrade result independently" in request.task:
            return _result('{"ok": true, "command": "npm test", "summary": "tests passed"}')
        return _result("stage complete")

    result = run_upgrade_backbone_workflow(
        "mocha 4 -> 11",
        max_heal_attempts=1,
        run_loop=run_loop,
    )

    assert result.ok
    assert result.history == (
        "baseline",
        "research",
        "plan",
        "execute",
        "verify:ok",
        "report",
    )
    assert [request.stage for request in requests] == ["baseline", "research", "execute", "verify"]
    assert requests[0].read_only is False
    assert "pre-upgrade baseline" in requests[0].task
    assert requests[1].read_only is True
    assert requests[2].enforce_baseline_guardrail is True
    assert requests[2].current_dependency == "mocha"
    assert requests[2].allowed_files == ("package.json", "package-lock.json")
    assert requests[3].read_only is False
    assert '"ok"' in requests[3].task
    assert result.report is not None
    assert result.report.ok is True


def test_upgrade_workflow_routes_failed_verification_through_heal() -> None:
    verify_calls = 0
    requests: list[StageLoopRequest] = []

    def run_loop(request: StageLoopRequest) -> LoopResult:
        nonlocal verify_calls
        requests.append(request)
        if request.stage == "verify":
            verify_calls += 1
            if verify_calls == 1:
                return _result('{"ok": false, "command": "npm test", "summary": "tests failed"}')
            return _result('{"ok": true, "command": "npm test", "summary": "tests passed"}')
        return _result("stage complete")

    result = run_upgrade_backbone_workflow(
        "mocha 4 -> 11",
        max_heal_attempts=1,
        run_loop=run_loop,
    )

    assert result.ok
    assert result.heal_attempts == 1
    assert [request.stage for request in requests] == [
        "baseline",
        "research",
        "execute",
        "verify",
        "heal",
        "verify",
    ]
    heal_request = requests[4]
    assert heal_request.enforce_baseline_guardrail is True
    assert heal_request.current_dependency == "mocha"
    assert heal_request.allowed_files == ("package.json", "package-lock.json")
    assert "Self-heal" in heal_request.task


def test_upgrade_workflow_report_classifies_failed_verification() -> None:
    def run_loop(request: StageLoopRequest) -> LoopResult:
        if request.stage == "verify":
            return _result(
                '{"ok": false, "command": "npm test", "summary": "1 failing in mocha reporter"}'
            )
        return _result("stage complete")

    result = run_upgrade_backbone_workflow(
        "mocha 4 -> 11",
        max_heal_attempts=0,
        run_loop=run_loop,
    )

    assert result.report is not None
    assert result.report.ok is False
    assert result.report.failure_reason == "verification_failed"
    assert "Inspect verification failure: 1 failing in mocha reporter" in (
        result.report.recovery_suggestions
    )


def test_upgrade_workflow_keeps_legacy_verdict_fallback() -> None:
    def run_loop(request: StageLoopRequest) -> LoopResult:
        if request.stage == "verify":
            return _result("tests passed\nVERDICT: PASS")
        return _result("stage complete")

    result = run_upgrade_backbone_workflow(
        "mocha 4 -> 11",
        max_heal_attempts=1,
        run_loop=run_loop,
    )

    assert result.ok
    assert result.state["verification"].ok is True


def test_upgrade_workflow_parses_structured_baseline_state() -> None:
    def run_loop(request: StageLoopRequest) -> LoopResult:
        if request.stage == "baseline":
            return _result(
                '{"ran": true, "green": true, "command": "npm test", "summary": "28 passing"}'
            )
        if request.stage == "verify":
            return _result('{"ok": true, "command": "npm test", "summary": "tests passed"}')
        return _result("stage complete")

    result = run_upgrade_backbone_workflow(
        "mocha 4 -> 11",
        max_heal_attempts=1,
        run_loop=run_loop,
    )

    assert result.state["baseline"].green is True
    assert result.state["baseline"].command == "npm test"
    assert result.state["baseline"].summary == "28 passing"


def test_upgrade_workflow_parses_structured_research_brief() -> None:
    def run_loop(request: StageLoopRequest) -> LoopResult:
        if request.stage == "research":
            return _result(
                """
                {
                  "package": "mocha",
                  "current_version": "4.0.0",
                  "target_version": "11.0.0",
                  "sources": ["https://example.test/mocha"],
                  "relevant_risks": ["Node version requirement changed"]
                }
                """
            )
        if request.stage == "verify":
            return _result('{"ok": true, "command": "npm test", "summary": "tests passed"}')
        return _result("stage complete")

    result = run_upgrade_backbone_workflow(
        "mocha 4 -> 11",
        max_heal_attempts=1,
        run_loop=run_loop,
    )

    assert result.state["research"] is not None
    assert result.state["research"].package == "mocha"
    assert result.state["research"].target_version == "11.0.0"
    assert result.state["research"].sources == ["https://example.test/mocha"]
    assert result.state["research"].relevant_risks == ["Node version requirement changed"]


def test_upgrade_workflow_plan_uses_structured_research_target_version() -> None:
    def run_loop(request: StageLoopRequest) -> LoopResult:
        if request.stage == "research":
            return _result(
                '{"package": "mocha", "current_version": "4.0.0", '
                '"target_version": "11.0.0", "sources": [], "relevant_risks": []}'
            )
        if request.stage == "verify":
            return _result('{"ok": true, "command": "npm test", "summary": "tests passed"}')
        return _result("stage complete")

    result = run_upgrade_backbone_workflow(
        "mocha 4 -> 11",
        max_heal_attempts=1,
        run_loop=run_loop,
    )

    assert result.state["plan"] is not None
    assert result.state["plan"].dependency == "mocha"
    assert result.state["plan"].target_version == "11.0.0"


def test_upgrade_workflow_report_collects_changed_files() -> None:
    def run_loop(request: StageLoopRequest) -> LoopResult:
        if request.stage == "verify":
            return _result('{"ok": true, "command": "npm test", "summary": "tests passed"}')
        return _result("stage complete")

    result = run_upgrade_backbone_workflow(
        "mocha 4 -> 11",
        max_heal_attempts=1,
        run_loop=run_loop,
        collect_changed_files=lambda: ["package-lock.json", "package.json"],
    )

    assert result.ok
    assert result.report is not None
    assert result.report.changed_files == ["package-lock.json", "package.json"]
    assert result.state["changed_files"] == ["package-lock.json", "package.json"]


def test_upgrade_all_workflow_runs_batch_backbone_stages() -> None:
    requests: list[StageLoopRequest] = []

    def run_loop(request: StageLoopRequest) -> LoopResult:
        requests.append(request)
        if request.stage == "queue":
            return _result(
                """
                {
                  "packages": [
                    {
                      "name": "mocha",
                      "current_version": "4.0.0",
                      "target_version": "11.0.0",
                      "dependency_type": "devDependency"
                    },
                    {
                      "name": "chai",
                      "current_version": "4.3.0",
                      "target_version": "5.1.0",
                      "dependency_type": "dependency"
                    }
                  ]
                }
                """
            )
        if request.stage == "verify_package":
            return _result('{"ok": true, "command": "npm test", "summary": "package passed"}')
        if request.stage == "verify":
            return _result('{"ok": true, "command": "npm test", "summary": "tests passed"}')
        return _result("stage complete")

    result = run_upgrade_all_backbone_workflow(
        max_heal_attempts=1,
        run_loop=run_loop,
    )

    assert result.ok
    assert result.history == (
        "baseline",
        "queue",
        "plan",
        "select_package:mocha",
        "execute_package:mocha",
        "verify_package:mocha:ok",
        "select_package:chai",
        "execute_package:chai",
        "verify_package:chai:ok",
        "final_verify:ok",
        "report",
    )
    assert [request.stage for request in requests] == [
        "baseline",
        "queue",
        "execute_package",
        "verify_package",
        "execute_package",
        "verify_package",
        "verify",
    ]
    assert requests[0].read_only is False
    assert requests[1].read_only is True
    assert "npm_outdated" in requests[1].task
    assert '"packages"' in requests[1].task
    assert requests[2].enforce_baseline_guardrail is True
    assert requests[2].current_dependency == "mocha"
    assert requests[2].allowed_files == ("package.json", "package-lock.json")
    assert "mocha" in requests[2].task
    assert "Do not upgrade any other package intentionally" in requests[2].task
    assert "mocha" in requests[3].task
    assert "chai" in requests[4].task
    assert '"ok"' in requests[6].task
    assert result.state["queue"].packages[0].name == "mocha"
    assert [item.status for item in result.state["queue"].packages] == ["done", "done"]
    assert [record.name for record in result.state["package_results"]] == ["mocha", "chai"]
    assert [record.status for record in result.state["package_results"]] == ["done", "done"]
    assert result.state["package_results"][0].summary == "package passed"


def test_upgrade_all_workflow_parses_structured_baseline_state() -> None:
    def run_loop(request: StageLoopRequest) -> LoopResult:
        if request.stage == "baseline":
            return _result(
                '{"ran": true, "green": false, "command": "npm test", "summary": "1 failing"}'
            )
        if request.stage == "queue":
            return _result('{"packages": []}')
        if request.stage == "verify":
            return _result('{"ok": false, "command": "npm test", "summary": "still failing"}')
        return _result("stage complete")

    result = run_upgrade_all_backbone_workflow(
        max_heal_attempts=0,
        run_loop=run_loop,
    )

    assert result.state["baseline"].green is False
    assert result.state["baseline"].command == "npm test"
    assert result.state["baseline"].summary == "1 failing"


def test_upgrade_all_workflow_routes_failed_final_verify_through_heal() -> None:
    verify_calls = 0
    requests: list[StageLoopRequest] = []

    def run_loop(request: StageLoopRequest) -> LoopResult:
        nonlocal verify_calls
        requests.append(request)
        if request.stage == "queue":
            return _result(
                '{"packages": [{"name": "mocha", "current_version": "4.0.0", '
                '"target_version": "11.0.0", "dependency_type": "devDependency"}]}'
            )
        if request.stage == "verify_package":
            return _result('{"ok": true, "command": "npm test", "summary": "package passed"}')
        if request.stage == "verify":
            verify_calls += 1
            if verify_calls == 1:
                return _result('{"ok": false, "command": "npm test", "summary": "batch failed"}')
            return _result('{"ok": true, "command": "npm test", "summary": "batch passed"}')
        return _result("stage complete")

    result = run_upgrade_all_backbone_workflow(
        max_heal_attempts=1,
        run_loop=run_loop,
    )

    assert result.ok
    assert result.heal_attempts == 1
    assert result.history == (
        "baseline",
        "queue",
        "plan",
        "select_package:mocha",
        "execute_package:mocha",
        "verify_package:mocha:ok",
        "final_verify:fail",
        "heal:1",
        "final_verify:ok",
        "report",
    )
    assert [request.stage for request in requests] == [
        "baseline",
        "queue",
        "execute_package",
        "verify_package",
        "verify",
        "heal",
        "verify",
    ]
    assert requests[5].enforce_baseline_guardrail is True
    assert requests[5].current_dependency == "all direct dependencies"
    assert requests[5].allowed_files == ("package.json", "package-lock.json")
    assert "batch upgrade" in requests[5].task


def test_upgrade_all_workflow_report_summarizes_package_results_and_failures() -> None:
    def run_loop(request: StageLoopRequest) -> LoopResult:
        if request.stage == "queue":
            return _result(
                """
                {
                  "packages": [
                    {"name": "mocha", "current_version": "4.0.0", "target_version": "11.0.0",
                     "dependency_type": "devDependency"},
                    {"name": "chai", "current_version": "4.3.0", "target_version": "5.1.0",
                     "dependency_type": "dependency"}
                  ]
                }
                """
            )
        if request.stage == "verify_package" and "chai" in request.task:
            return _result(
                '{"ok": false, "command": "npm test", "summary": "chai ESM import failed"}'
            )
        if request.stage == "verify_package":
            return _result('{"ok": true, "command": "npm test", "summary": "mocha passed"}')
        if request.stage == "verify":
            return _result('{"ok": false, "command": "npm test", "summary": "batch failed"}')
        return _result("stage complete")

    result = run_upgrade_all_backbone_workflow(
        max_heal_attempts=0,
        run_loop=run_loop,
    )

    assert result.report is not None
    assert result.report.ok is False
    assert "mocha: done" in result.report.summary
    assert "chai: failed" in result.report.summary
    assert result.report.failure_reason == "package_failed"
    assert "chai: chai ESM import failed" in result.report.remaining_risks
    assert "Review or revert failed package: chai" in result.report.recovery_suggestions


def test_upgrade_all_workflow_report_collects_changed_files() -> None:
    def run_loop(request: StageLoopRequest) -> LoopResult:
        if request.stage == "queue":
            return _result(
                '{"packages": [{"name": "mocha", "current_version": "4.0.0", '
                '"target_version": "11.0.0", "dependency_type": "devDependency"}]}'
            )
        if request.stage in {"verify", "verify_package"}:
            return _result('{"ok": true, "command": "npm test", "summary": "tests passed"}')
        return _result("stage complete")

    result = run_upgrade_all_backbone_workflow(
        max_heal_attempts=1,
        run_loop=run_loop,
        collect_changed_files=lambda: ["package.json"],
    )

    assert result.ok
    assert result.report is not None
    assert result.report.changed_files == ["package.json"]
    assert result.state["changed_files"] == ["package.json"]
