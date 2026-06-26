"""Tests for upgrade graph state and structured artifacts."""

from __future__ import annotations

from upgrade_dependencies_agent.orchestrator.state import (
    BaselineState,
    GraphPhase,
    ResearchBrief,
    UpgradePlan,
    VerificationResult,
    make_upgrade_graph_state,
)


def test_make_upgrade_graph_state_sets_baseline_first_defaults() -> None:
    state = make_upgrade_graph_state("upgrade mocha", max_heal_attempts=2)

    assert state["task"] == "upgrade mocha"
    assert state["phase"] == "baseline"
    assert state["baseline"] == BaselineState()
    assert state["heal_attempts"] == 0
    assert state["max_heal_attempts"] == 2
    assert state["history"] == []
    assert state["changed_files"] == []


def test_make_upgrade_graph_state_can_start_at_execute_for_legacy_runner() -> None:
    state = make_upgrade_graph_state(
        "upgrade mocha",
        max_heal_attempts=1,
        phase="execute",
    )

    assert state["phase"] == "execute"
    assert state["execute_result"] is None
    assert state["verify_result"] is None
    assert state["final_result"] is None


def test_graph_phase_lists_full_upgrade_backbone() -> None:
    phases = set(GraphPhase.__args__)

    assert phases == {
        "baseline",
        "research",
        "plan",
        "execute",
        "verify",
        "heal",
        "report",
        "done",
    }


def test_structured_artifacts_are_json_serializable() -> None:
    research = ResearchBrief(
        package="mocha",
        current_version="4.0.0",
        target_version="11.0.0",
        sources=["https://example.test/releases"],
        relevant_risks=["Node minimum changed"],
    )
    plan = UpgradePlan(
        dependency="mocha",
        target_version="11.0.0",
        steps=["update package.json", "run npm install", "run npm test"],
        allowed_files=["package.json", "package-lock.json"],
    )
    verification = VerificationResult(
        ok=True,
        command="npm test",
        summary="28 passing",
        passing_count=28,
    )

    assert research.model_dump(mode="json")["package"] == "mocha"
    assert plan.model_dump(mode="json")["allowed_files"] == [
        "package.json",
        "package-lock.json",
    ]
    assert verification.model_dump(mode="json")["ok"] is True
