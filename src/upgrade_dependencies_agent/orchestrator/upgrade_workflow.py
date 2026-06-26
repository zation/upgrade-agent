"""Concrete upgrade workflow built on the full LangGraph backbone."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..core import LoopResult
from ..skills import BASE_AGENT, BREAKING_CHANGE_RESEARCHER, UPGRADE
from .state import (
    AgentReport,
    BaselineState,
    ResearchBrief,
    UpgradeGraphState,
    UpgradePlan,
    VerificationResult,
)
from .upgrade_backbone import UpgradeBackboneResult, UpgradeBackboneRunner


@dataclass(frozen=True)
class StageLoopRequest:
    """One ReAct loop invocation requested by a graph stage."""

    stage: str
    system_prompt: str
    task: str
    read_only: bool = False
    enforce_baseline_guardrail: bool = False


StageLoopRunner = Callable[[StageLoopRequest], LoopResult]


def run_upgrade_backbone_workflow(
    target: str,
    *,
    max_heal_attempts: int,
    run_loop: StageLoopRunner,
) -> UpgradeBackboneResult:
    """Run the concrete upgrade workflow using caller-provided ReAct loop execution."""

    def baseline(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="baseline",
                system_prompt=BASE_AGENT,
                task=_baseline_task(target),
            )
        )
        return {
            **state,
            "baseline": BaselineState(
                ran=True,
                green=_result_passed(result),
                command="npm test",
                summary=result.final_text,
            ),
        }

    def research(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="research",
                system_prompt=BREAKING_CHANGE_RESEARCHER,
                task=_research_task(target),
                read_only=True,
            )
        )
        return {
            **state,
            "research": ResearchBrief(
                package=_dependency_name(target),
                target_version=_target_version(target),
                relevant_risks=[result.final_text],
            ),
        }

    def plan(state: UpgradeGraphState) -> UpgradeGraphState:
        dependency = _dependency_name(target)
        return {
            **state,
            "current_dependency": dependency,
            "plan": UpgradePlan(
                dependency=dependency,
                target_version=_target_version(target),
                steps=[
                    "confirm baseline",
                    "apply minimal dependency version change",
                    "install/update lockfile",
                    "run verification tests",
                ],
                allowed_files=["package.json", "package-lock.json"],
            ),
        }

    def execute(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="execute",
                system_prompt=UPGRADE,
                task=_execute_task(target, state),
                enforce_baseline_guardrail=True,
            )
        )
        return {**state, "execute_result": result, "final_result": result}

    def verify(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="verify",
                system_prompt=BASE_AGENT,
                task=_verify_task(state),
            )
        )
        return {
            **state,
            "verify_result": result,
            "verification": VerificationResult(
                ok=_result_passed(result),
                command="npm test",
                summary=result.final_text,
            ),
            "final_result": result,
        }

    def heal(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="heal",
                system_prompt=UPGRADE,
                task=_heal_task(state),
                enforce_baseline_guardrail=True,
            )
        )
        return {**state, "heal_result": result, "final_result": result}

    def report(state: UpgradeGraphState) -> UpgradeGraphState:
        verification = state.get("verification")
        ok = bool(verification and verification.ok)
        summary = verification.summary if verification else "verification did not run"
        return {
            **state,
            "report": AgentReport(
                ok=ok,
                summary=summary,
                changed_files=state.get("changed_files", []),
                remaining_risks=[] if ok else ["upgrade verification failed"],
            ),
        }

    runner = UpgradeBackboneRunner(
        baseline=baseline,
        research=research,
        plan=plan,
        execute=execute,
        verify=verify,
        heal=heal,
        report=report,
        max_heal_attempts=max_heal_attempts,
    )
    return runner.run(f"Upgrade the dependency: {target}")


def _baseline_task(target: str) -> str:
    return (
        f"Establish the pre-upgrade baseline for this dependency upgrade: {target}.\n\n"
        "Run the project's existing npm test command, inspect the real output, "
        "and report whether the baseline is green. Do not edit files. End with "
        "`VERDICT: PASS` or `VERDICT: FAIL` on its own line."
    )


def _research_task(target: str) -> str:
    return (
        f"Research this dependency upgrade without editing files: {target}.\n\n"
        "Use package metadata, release/changelog sources, and project usage search "
        "to identify relevant breaking changes. End with the required verdict line."
    )


def _execute_task(target: str, state: UpgradeGraphState) -> str:
    research = state.get("research")
    risks = "\n".join(research.relevant_risks if research else []) or "(no research summary)"
    return (
        f"Upgrade the dependency: {target}.\n\n"
        "Before mutating files, run npm test once in this loop so the runtime "
        "baseline guardrail observes a green baseline. Then make the minimal "
        "dependency/version change, update the lockfile if needed, adapt only "
        "code required by this upgrade, and run npm test again.\n\n"
        f"Research summary:\n{risks}"
    )


def _verify_task(state: UpgradeGraphState) -> str:
    prior = state.get("heal_result") or state.get("execute_result")
    summary = prior.final_text if prior else "(no prior result)"
    return (
        "Verify the dependency upgrade result independently.\n\n"
        "Run the project's test command, read the actual output, inspect git diff, "
        "and decide whether the project is green. Do not make edits in this "
        "verification pass. If verification fails, report the exact failing "
        "command/output and the smallest repair needed. End your response with "
        "`VERDICT: PASS` or `VERDICT: FAIL` on its own line.\n\n"
        f"Previous step summary:\n{summary}"
    )


def _heal_task(state: UpgradeGraphState) -> str:
    verify_result = state.get("verify_result")
    failure = verify_result.final_text if verify_result else "(no verification result)"
    return (
        "Self-heal the failed dependency upgrade.\n\n"
        "Use the verification output below as the source of truth. First run npm "
        "test so the runtime baseline guardrail has current evidence, then make "
        "the smallest targeted edit required, rerun tests, and inspect git diff. "
        "If you cannot safely fix it, revert your own attempted fix and report "
        "the blocker.\n\n"
        f"Verification failure:\n{failure}"
    )


def _result_passed(result: LoopResult) -> bool:
    text = result.final_text.lower()
    if "verdict: pass" in text:
        return result.ok
    if "verdict: fail" in text:
        return False
    failure_markers = ("failing", "failed", "error", "red baseline", "cannot verify")
    return result.ok and not any(marker in text for marker in failure_markers)


def _dependency_name(target: str) -> str:
    return target.split()[0] if target.split() else target


def _target_version(target: str) -> str | None:
    if "->" not in target:
        return None
    return target.rsplit("->", 1)[1].strip() or None
