"""Tests for the real upgrade graph workflow wiring."""

from __future__ import annotations

from pathlib import Path

from upgrade_dependencies_agent.core import LoopResult
from upgrade_dependencies_agent.orchestrator.upgrade_workflow import (
    StageLoopRequest,
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
            return _result("tests passed\nVERDICT: PASS")
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
    assert requests[3].read_only is False
    assert "VERDICT: PASS" in requests[3].task
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
                return _result("tests failed\nVERDICT: FAIL")
            return _result("tests passed\nVERDICT: PASS")
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
    assert "Self-heal" in heal_request.task
