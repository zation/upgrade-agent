"""Tests for the LangGraph upgrade orchestration layer."""

from __future__ import annotations

from pathlib import Path

from refactor_agent.core import LoopResult
from refactor_agent.orchestrator import UpgradeGraphRunner


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


def test_upgrade_graph_finishes_when_verify_passes():
    calls: list[str] = []

    def execute(task: str) -> LoopResult:
        calls.append(f"execute:{task}")
        return _result("upgrade done")

    def verify(task: str) -> LoopResult:
        calls.append(f"verify:{task}")
        return _result("28 passing")

    def heal(task: str) -> LoopResult:
        calls.append(f"heal:{task}")
        return _result("should not run")

    runner = UpgradeGraphRunner(
        execute=execute,
        verify=verify,
        heal=heal,
        is_verified=lambda result: "passing" in result.final_text,
    )

    result = runner.run("upgrade mocha")

    assert result.ok
    assert result.heal_attempts == 0
    assert result.history == ("execute", "verify:ok")
    assert [call.split(":", 1)[0] for call in calls] == ["execute", "verify"]


def test_upgrade_graph_default_verifier_reads_explicit_verdict():
    runner = UpgradeGraphRunner(
        execute=lambda task: _result("upgrade done"),
        verify=lambda task: _result("tests passed\nVERDICT: PASS"),
        heal=lambda task: _result("should not run"),
    )

    result = runner.run("upgrade mocha")

    assert result.ok


def test_upgrade_graph_routes_verify_failure_to_heal_then_reverify():
    verify_calls = 0

    def execute(task: str) -> LoopResult:
        return _result("upgrade done")

    def verify(task: str) -> LoopResult:
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 1:
            return _result("1 failing")
        return _result("28 passing")

    def heal(task: str) -> LoopResult:
        assert "Self-heal attempt 1" in task
        assert "1 failing" in task
        return _result("fixed test config")

    runner = UpgradeGraphRunner(
        execute=execute,
        verify=verify,
        heal=heal,
        max_heal_attempts=1,
        is_verified=lambda result: "passing" in result.final_text,
    )

    result = runner.run("upgrade mocha")

    assert result.ok
    assert result.heal_attempts == 1
    assert result.history == ("execute", "verify:fail", "heal:1", "verify:ok")


def test_upgrade_graph_stops_after_heal_budget():
    def execute(task: str) -> LoopResult:
        return _result("upgrade done")

    def verify(task: str) -> LoopResult:
        return _result("still failing")

    def heal(task: str) -> LoopResult:
        return _result("attempted fix")

    runner = UpgradeGraphRunner(
        execute=execute,
        verify=verify,
        heal=heal,
        max_heal_attempts=1,
        is_verified=lambda result: "passing" in result.final_text,
    )

    result = runner.run("upgrade mocha")

    assert not result.ok
    assert result.heal_attempts == 1
    assert result.history == ("execute", "verify:fail", "heal:1", "verify:fail")
