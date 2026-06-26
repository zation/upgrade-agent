"""Tests for the full upgrade graph backbone."""

from __future__ import annotations

from upgrade_dependencies_agent.orchestrator import (
    AgentReport,
    BaselineState,
    ResearchBrief,
    UpgradeBackboneRunner,
    UpgradeGraphState,
    UpgradePlan,
    VerificationResult,
)


def test_upgrade_backbone_runs_full_stage_order_when_verify_passes() -> None:
    runner = UpgradeBackboneRunner(
        baseline=_baseline,
        research=_research,
        plan=_plan,
        execute=_execute,
        verify=lambda state: {**state, "verification": _verification(ok=True)},
        heal=_heal,
        report=_report,
    )

    result = runner.run("upgrade mocha")

    assert result.ok
    assert result.history == (
        "baseline",
        "research",
        "plan",
        "execute",
        "verify:ok",
        "report",
    )
    assert result.report == AgentReport(
        ok=True,
        summary="upgrade complete",
        changed_files=["package.json"],
    )


def test_upgrade_backbone_routes_failed_verify_to_heal_then_reverify() -> None:
    verify_calls = 0

    def verify(state: UpgradeGraphState) -> UpgradeGraphState:
        nonlocal verify_calls
        verify_calls += 1
        return {**state, "verification": _verification(ok=verify_calls > 1)}

    runner = UpgradeBackboneRunner(
        baseline=_baseline,
        research=_research,
        plan=_plan,
        execute=_execute,
        verify=verify,
        heal=_heal,
        report=_report,
        max_heal_attempts=1,
    )

    result = runner.run("upgrade mocha")

    assert result.ok
    assert result.heal_attempts == 1
    assert result.history == (
        "baseline",
        "research",
        "plan",
        "execute",
        "verify:fail",
        "heal:1",
        "verify:ok",
        "report",
    )


def test_upgrade_backbone_reports_failure_after_heal_budget() -> None:
    runner = UpgradeBackboneRunner(
        baseline=_baseline,
        research=_research,
        plan=_plan,
        execute=_execute,
        verify=lambda state: {**state, "verification": _verification(ok=False)},
        heal=_heal,
        report=_report,
        max_heal_attempts=1,
    )

    result = runner.run("upgrade mocha")

    assert not result.ok
    assert result.heal_attempts == 1
    assert result.history == (
        "baseline",
        "research",
        "plan",
        "execute",
        "verify:fail",
        "heal:1",
        "verify:fail",
        "report",
    )


def _baseline(state: UpgradeGraphState) -> UpgradeGraphState:
    return {
        **state,
        "baseline": BaselineState(ran=True, green=True, command="npm test", summary="28 passing"),
    }


def _research(state: UpgradeGraphState) -> UpgradeGraphState:
    return {
        **state,
        "research": ResearchBrief(
            package="mocha",
            current_version="4.0.0",
            target_version="11.0.0",
            sources=["https://example.test/releases"],
        ),
    }


def _plan(state: UpgradeGraphState) -> UpgradeGraphState:
    return {
        **state,
        "plan": UpgradePlan(
            dependency="mocha",
            target_version="11.0.0",
            steps=["update package.json"],
            allowed_files=["package.json"],
        ),
    }


def _execute(state: UpgradeGraphState) -> UpgradeGraphState:
    return {**state, "changed_files": ["package.json"]}


def _heal(state: UpgradeGraphState) -> UpgradeGraphState:
    return state


def _report(state: UpgradeGraphState) -> UpgradeGraphState:
    verification = state.get("verification")
    ok = bool(verification and verification.ok)
    return {
        **state,
        "report": AgentReport(
            ok=ok,
            summary="upgrade complete" if ok else "upgrade failed",
            changed_files=state.get("changed_files", []),
        ),
    }


def _verification(*, ok: bool) -> VerificationResult:
    summary = "28 passing" if ok else "1 failing"
    return VerificationResult(ok=ok, command="npm test", summary=summary)
