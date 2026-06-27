"""Concrete upgrade workflow built on the full LangGraph backbone."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from langgraph.graph import END, StateGraph

from ..core import LoopResult
from ..core.structured import (
    StructuredParseError,
    parse_structured_text,
    response_format_for_schema,
)
from ..skills import BASE_AGENT, BREAKING_CHANGE_RESEARCHER, UPGRADE, UPGRADE_ALL
from .state import (
    AgentReport,
    BaselineState,
    PackageUpgradeRecord,
    ResearchBrief,
    UpgradeGraphState,
    UpgradePlan,
    UpgradeQueue,
    UpgradeQueueItem,
    VerificationResult,
    make_upgrade_graph_state,
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
    current_dependency: str | None = None
    allowed_files: tuple[str, ...] = ()
    max_iterations: int | None = None
    response_format: dict[str, object] | None = None


StageLoopRunner = Callable[[StageLoopRequest], LoopResult]
ChangedFilesCollector = Callable[[], list[str] | None]
BASELINE_RESPONSE_FORMAT = response_format_for_schema(BaselineState)
RESEARCH_RESPONSE_FORMAT = response_format_for_schema(ResearchBrief)
QUEUE_RESPONSE_FORMAT = response_format_for_schema(UpgradeQueue)
VERIFICATION_RESPONSE_FORMAT = response_format_for_schema(VerificationResult)
PACKAGE_EXECUTE_MAX_ITERATIONS = 30


def run_upgrade_backbone_workflow(
    target: str,
    *,
    max_heal_attempts: int,
    run_loop: StageLoopRunner,
    collect_changed_files: ChangedFilesCollector | None = None,
) -> UpgradeBackboneResult:
    """Run the concrete upgrade workflow using caller-provided ReAct loop execution."""

    def baseline(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="baseline",
                system_prompt=BASE_AGENT,
                task=_baseline_task(target),
                response_format=BASELINE_RESPONSE_FORMAT,
            )
        )
        return {
            **state,
            "baseline": _baseline_from_result(result),
        }

    def research(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="research",
                system_prompt=BREAKING_CHANGE_RESEARCHER,
                task=_research_task(target),
                read_only=True,
                response_format=RESEARCH_RESPONSE_FORMAT,
            )
        )
        return {
            **state,
            "research": _research_from_result(result, target),
        }

    def plan(state: UpgradeGraphState) -> UpgradeGraphState:
        plan_artifact = _single_upgrade_plan(target, state)
        return {
            **state,
            "current_dependency": plan_artifact.dependency,
            "plan": plan_artifact,
        }

    def execute(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="execute",
                system_prompt=UPGRADE,
                task=_execute_task(target, state),
                enforce_baseline_guardrail=True,
                current_dependency=state.get("current_dependency"),
                allowed_files=_allowed_files_from_state(state),
            )
        )
        return {**state, "execute_result": result, "final_result": result}

    def verify(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="verify",
                system_prompt=BASE_AGENT,
                task=_verify_task(state),
                response_format=VERIFICATION_RESPONSE_FORMAT,
            )
        )
        return {
            **state,
            "verify_result": result,
            "verification": _verification_from_result(result),
            "final_result": result,
        }

    def heal(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="heal",
                system_prompt=UPGRADE,
                task=_heal_task(state),
                enforce_baseline_guardrail=True,
                current_dependency=state.get("current_dependency"),
                allowed_files=_allowed_files_from_state(state),
            )
        )
        return {**state, "heal_result": result, "final_result": result}

    def report(state: UpgradeGraphState) -> UpgradeGraphState:
        verification = state.get("verification")
        changed_files = _collect_changed_files(state, collect_changed_files)
        report_artifact = _single_agent_report(state, changed_files, verification)
        return {
            **state,
            "changed_files": changed_files,
            "report": report_artifact,
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


def run_upgrade_dry_run_workflow(
    target: str,
    *,
    run_loop: StageLoopRunner,
) -> UpgradeBackboneResult:
    """Plan a single dependency upgrade without mutating the target project."""
    state = make_upgrade_graph_state(
        f"Dry-run upgrade plan: {target}",
        max_heal_attempts=0,
        phase="research",
    )
    research_result = run_loop(
        StageLoopRequest(
            stage="research",
            system_prompt=BREAKING_CHANGE_RESEARCHER,
            task=_research_task(target),
            read_only=True,
            response_format=RESEARCH_RESPONSE_FORMAT,
        )
    )
    state = {
        **state,
        "research": _research_from_result(research_result, target),
        "history": [*state.get("history", []), "research"],
    }
    plan = _single_upgrade_plan(target, state)
    report = _dry_run_agent_report(
        summary=f"Dry run complete for {target}",
        risks=state["research"].relevant_risks if state.get("research") else [],
    )
    state = {
        **state,
        "phase": "done",
        "current_dependency": plan.dependency,
        "plan": plan,
        "report": report,
        "history": [*state.get("history", []), "plan", "report"],
    }
    return UpgradeBackboneResult(
        ok=report.ok,
        state=state,
        report=report,
        heal_attempts=0,
        history=tuple(state.get("history", [])),
    )


def run_upgrade_all_backbone_workflow(
    *,
    max_heal_attempts: int,
    run_loop: StageLoopRunner,
    collect_changed_files: ChangedFilesCollector | None = None,
) -> UpgradeBackboneResult:
    """Run the concrete batch-upgrade workflow with explicit package-level graph steps."""
    target = "all direct dependencies"

    def baseline(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="baseline",
                system_prompt=BASE_AGENT,
                task=_batch_baseline_task(),
                response_format=BASELINE_RESPONSE_FORMAT,
            )
        )
        return {
            **state,
            "baseline": _baseline_from_result(result),
        }

    def queue(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="queue",
                system_prompt=BASE_AGENT,
                task=_batch_queue_task(),
                read_only=True,
                response_format=QUEUE_RESPONSE_FORMAT,
            )
        )
        return {
            **state,
            "queue": _queue_from_result(result),
            "research": ResearchBrief(
                package=target,
                relevant_risks=[result.final_text],
            ),
        }

    def plan(state: UpgradeGraphState) -> UpgradeGraphState:
        return {
            **state,
            "current_dependency": target,
            "plan": UpgradePlan(
                dependency=target,
                steps=[
                    "confirm green baseline",
                    "build queue from direct dependencies only",
                    "upgrade one package at a time",
                    "verify after each package",
                    "run final verification",
                ],
                allowed_files=[],
            ),
        }

    def select_package(state: UpgradeGraphState) -> UpgradeGraphState:
        queue = state.get("queue") or UpgradeQueue()
        item = _current_queue_item({**state, "queue": queue})
        if item is None:
            return {
                **state,
                "queue": queue,
                "current_dependency": target,
            }
        return {
            **state,
            "queue": queue,
            "current_dependency": item.name,
            "history": [*state.get("history", []), f"select_package:{item.name}"],
        }

    def execute_package(state: UpgradeGraphState) -> UpgradeGraphState:
        queue = state.get("queue") or UpgradeQueue()
        item = _current_queue_item({**state, "queue": queue})
        if item is None:
            return {**state, "queue": queue, "current_dependency": target}
        package_state = {
            **state,
            "queue": queue,
            "current_dependency": item.name,
        }
        execute_result = run_loop(
            StageLoopRequest(
                stage="execute_package",
                system_prompt=UPGRADE,
                task=_batch_execute_package_task(
                    item,
                    _queue_item_position(queue, item),
                    len(queue.packages),
                    package_state,
                ),
                enforce_baseline_guardrail=True,
                current_dependency=item.name,
                allowed_files=_allowed_files_from_state(package_state),
                max_iterations=PACKAGE_EXECUTE_MAX_ITERATIONS,
            )
        )
        return {
            **package_state,
            "execute_result": execute_result,
            "final_result": execute_result,
            "history": [*state.get("history", []), f"execute_package:{item.name}"],
        }

    def verify_package(state: UpgradeGraphState) -> UpgradeGraphState:
        queue = state.get("queue") or UpgradeQueue()
        item = _current_queue_item({**state, "queue": queue})
        execute_result = state.get("execute_result")
        if item is None or execute_result is None:
            return {**state, "queue": queue, "current_dependency": target}

        verify_result = run_loop(
            StageLoopRequest(
                stage="verify_package",
                system_prompt=BASE_AGENT,
                task=_batch_verify_package_task(item, execute_result),
                response_format=VERIFICATION_RESPONSE_FORMAT,
            )
        )
        package_verification = _verification_from_result(verify_result)
        if package_verification.ok:
            item.status = "done"
            item.reason = None
        else:
            item.status = "failed"
            item.reason = package_verification.summary

        package_results = [
            *state.get("package_results", []),
            PackageUpgradeRecord(
                name=item.name,
                status=item.status,
                summary=package_verification.summary,
                changed_files=state.get("changed_files", []),
            ),
        ]
        verdict = "ok" if package_verification.ok else "fail"
        return {
            **state,
            "queue": queue,
            "current_dependency": None,
            "package_results": package_results,
            "verify_result": verify_result,
            "final_result": verify_result,
            "history": [*state.get("history", []), f"verify_package:{item.name}:{verdict}"],
        }

    def final_verify(state: UpgradeGraphState) -> UpgradeGraphState:
        result = run_loop(
            StageLoopRequest(
                stage="verify",
                system_prompt=BASE_AGENT,
                task=_batch_verify_task(state),
                response_format=VERIFICATION_RESPONSE_FORMAT,
            )
        )
        verification = _verification_from_result(result)
        verdict = "ok" if verification.ok else "fail"
        return {
            **state,
            "verify_result": result,
            "verification": verification,
            "final_result": result,
            "needs_heal": not verification.ok,
            "history": [*state.get("history", []), f"final_verify:{verdict}"],
        }

    def heal(state: UpgradeGraphState) -> UpgradeGraphState:
        attempts = state.get("heal_attempts", 0) + 1
        result = run_loop(
            StageLoopRequest(
                stage="heal",
                system_prompt=UPGRADE_ALL,
                task=_batch_heal_task(state),
                enforce_baseline_guardrail=True,
                current_dependency=state.get("current_dependency"),
                allowed_files=_allowed_files_from_state(state),
            )
        )
        return {
            **state,
            "heal_attempts": attempts,
            "heal_result": result,
            "final_result": result,
            "history": [*state.get("history", []), f"heal:{attempts}"],
        }

    def report(state: UpgradeGraphState) -> UpgradeGraphState:
        verification = state.get("verification")
        changed_files = _collect_changed_files(state, collect_changed_files)
        report_artifact = _batch_agent_report(state, changed_files, verification)
        return {
            **state,
            "changed_files": changed_files,
            "report": report_artifact,
            "history": [*state.get("history", []), "report"],
        }

    graph = StateGraph(UpgradeGraphState)
    graph.add_node("baseline", _history_stage(baseline, "baseline"))
    graph.add_node("queue", _history_stage(queue, "queue"))
    graph.add_node("plan", _history_stage(plan, "plan"))
    graph.add_node("select_package", select_package)
    graph.add_node("execute_package", execute_package)
    graph.add_node("verify_package", verify_package)
    graph.add_node("final_verify", final_verify)
    graph.add_node("heal", heal)
    graph.add_node("report", report)
    graph.set_entry_point("baseline")
    graph.add_conditional_edges(
        "baseline",
        _route_after_baseline,
        {
            "queue": "queue",
            "report": "report",
        },
    )
    graph.add_edge("queue", "plan")
    graph.add_edge("plan", "select_package")
    graph.add_conditional_edges(
        "select_package",
        _route_after_batch_select,
        {
            "execute_package": "execute_package",
            "final_verify": "final_verify",
        },
    )
    graph.add_edge("execute_package", "verify_package")
    graph.add_edge("verify_package", "select_package")
    graph.add_conditional_edges(
        "final_verify",
        _route_after_batch_verify,
        {
            "heal": "heal",
            "report": "report",
        },
    )
    graph.add_edge("heal", "final_verify")
    graph.add_edge("report", END)

    app = graph.compile()
    state = app.invoke(
        make_upgrade_graph_state(
            "Upgrade all direct dependencies",
            max_heal_attempts=max_heal_attempts,
        )
    )
    report_result = state.get("report")
    return UpgradeBackboneResult(
        ok=bool(report_result and report_result.ok),
        state=state,
        report=report_result,
        heal_attempts=state.get("heal_attempts", 0),
        history=tuple(state.get("history", [])),
    )


def run_upgrade_all_dry_run_workflow(
    *,
    run_loop: StageLoopRunner,
) -> UpgradeBackboneResult:
    """Plan a batch upgrade queue without mutating the target project."""
    target = "all direct dependencies"
    state = make_upgrade_graph_state(
        "Dry-run upgrade plan: all direct dependencies",
        max_heal_attempts=0,
        phase="research",
    )
    queue_result = run_loop(
        StageLoopRequest(
            stage="queue",
            system_prompt=BASE_AGENT,
            task=_batch_queue_task(),
            read_only=True,
            response_format=QUEUE_RESPONSE_FORMAT,
        )
    )
    queue = _queue_from_result(queue_result)
    plan = UpgradePlan(
        dependency=target,
        steps=[
            "confirm green baseline",
            "upgrade one package at a time from the planned queue",
            "verify after each package",
            "run final verification",
        ],
        allowed_files=[],
    )
    package_names = ", ".join(item.name for item in queue.packages)
    risks = [f"Planned packages: {package_names}"] if package_names else []
    report = _dry_run_agent_report(
        summary="Dry run complete for all direct dependencies",
        risks=risks,
    )
    state = {
        **state,
        "phase": "done",
        "current_dependency": target,
        "queue": queue,
        "plan": plan,
        "report": report,
        "history": [*state.get("history", []), "queue", "plan", "report"],
    }
    return UpgradeBackboneResult(
        ok=report.ok,
        state=state,
        report=report,
        heal_attempts=0,
        history=tuple(state.get("history", [])),
    )


def _history_stage(
    runner: Callable[[UpgradeGraphState], UpgradeGraphState],
    history_item: str,
) -> Callable[[UpgradeGraphState], UpgradeGraphState]:
    def wrapped(state: UpgradeGraphState) -> UpgradeGraphState:
        updated = runner(state)
        return {**updated, "history": [*updated.get("history", []), history_item]}

    return wrapped


def _route_after_batch_select(state: UpgradeGraphState) -> str:
    return "execute_package" if _current_queue_item(state) else "final_verify"


def _route_after_baseline(state: UpgradeGraphState) -> str:
    baseline = state.get("baseline")
    return "queue" if baseline and baseline.green else "report"


def _route_after_batch_verify(state: UpgradeGraphState) -> str:
    verification = state.get("verification")
    if verification and verification.ok:
        return "report"
    if state.get("heal_attempts", 0) < state.get("max_heal_attempts", 0):
        return "heal"
    return "report"


def _current_queue_item(state: UpgradeGraphState) -> UpgradeQueueItem | None:
    queue = state.get("queue")
    dependency = state.get("current_dependency")
    if queue is None:
        return None
    if dependency:
        for item in queue.packages:
            if item.name == dependency and item.status == "pending":
                return item
    pending = queue.pending()
    return pending[0] if pending else None


def _queue_item_position(queue: UpgradeQueue, item: UpgradeQueueItem) -> int:
    for index, queue_item in enumerate(queue.packages, start=1):
        if queue_item.name == item.name:
            return index
    return 1


def _baseline_task(target: str) -> str:
    return (
        f"Establish the pre-upgrade baseline for this dependency upgrade: {target}.\n\n"
        "Run the project's existing npm test command, inspect the real output, "
        "and report whether the baseline is green. Do not edit files. Return "
        "exactly one JSON object with this shape: "
        '{"ran": true, "green": true|false, "command": "npm test", '
        '"summary": "28 passing or exact failure summary"}.'
    )


def _research_task(target: str) -> str:
    return (
        f"Research this dependency upgrade without editing files: {target}.\n\n"
        "Use package metadata, release/changelog sources, retrieve_source_chunks "
        "for focused retrieval from changelog, migration guide, docs, or npm README "
        "sources, and project usage search to identify relevant breaking changes. "
        "If source discovery or retrieval has a source gap, include that gap and "
        "explain that tests must drive verification. Return exactly one JSON "
        "object with this shape: "
        '{"package": "mocha", "current_version": "4.0.0", '
        '"target_version": "11.0.0", "sources": ["https://..."], '
        '"relevant_risks": ["risk summary"]}.'
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
        "verification pass. Return exactly one JSON object with this shape: "
        '{"ok": true|false, "command": "npm test", "summary": "...", '
        '"passing_count": 28|null}. If verification fails, put the exact failing '
        "command/output and smallest repair needed in summary.\n\n"
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


def _batch_baseline_task() -> str:
    return (
        "Establish the pre-upgrade baseline for upgrading all direct dependencies.\n\n"
        "Run the project's existing npm test command, inspect the real output, "
        "and report whether the baseline is green. Do not edit files. Return "
        "exactly one JSON object with this shape: "
        '{"ran": true, "green": true|false, "command": "npm test", '
        '"summary": "28 passing or exact failure summary"}.'
    )


def _batch_queue_task() -> str:
    return (
        "Build the batch upgrade queue without editing files.\n\n"
        "Run npm_outdated and inspect package.json. Identify direct dependencies "
        "and devDependencies only, exclude transitive dependencies, and recommend "
        "a safe one-package-at-a-time order. Do not edit files. Return exactly one "
        "JSON object with this shape: "
        '{"packages": [{"name": "mocha", "current_version": "4.0.0", '
        '"target_version": "11.0.0", "dependency_type": "devDependency", '
        '"status": "pending", "reason": null}]}.'
    )


def _batch_execute_package_task(
    item: UpgradeQueueItem,
    index: int,
    total: int,
    state: UpgradeGraphState,
) -> str:
    queue = state.get("queue")
    queue_summary = (
        "\n".join(
            f"- {item.name}: {item.current_version or '?'} -> {item.target_version or '?'} "
            f"({item.dependency_type}, {item.status})"
            for item in queue.packages
        )
        if queue
        else "(no structured queue)"
    )
    package_target = (
        f"{item.name}: {item.current_version or '?'} -> {item.target_version or 'latest'} "
        f"({item.dependency_type})"
    )
    return (
        f"Upgrade package {index}/{total}: {package_target}.\n\n"
        "Before mutating files, run npm test once in this loop so the runtime "
        "baseline guardrail observes a green baseline. Then upgrade only this "
        "direct package to the target/latest stable version, update the lockfile "
        "if needed, and fix only breakages caused by this package. Do not upgrade "
        "any other package intentionally. If this package cannot be fixed safely, "
        "revert only this package's attempted changes and report the blocker.\n\n"
        f"Queue summary:\n{queue_summary}"
    )


def _batch_verify_package_task(item: UpgradeQueueItem, execute_result: LoopResult) -> str:
    return (
        f"Verify the package upgrade independently: {item.name}.\n\n"
        "Run the project's test command, read the actual output, inspect git diff, "
        "and decide whether this package's upgrade can be kept. Do not make edits "
        "in this verification pass. Return exactly one JSON object with this shape: "
        '{"ok": true|false, "command": "npm test", "summary": "...", '
        '"passing_count": 28|null}. If verification fails, include the exact '
        "failing command/output and whether this package should be reverted.\n\n"
        f"Package execution summary:\n{execute_result.final_text}"
    )


def _batch_verify_task(state: UpgradeGraphState) -> str:
    prior = state.get("heal_result") or state.get("execute_result")
    summary = prior.final_text if prior else "(no prior result)"
    return (
        "Verify the batch dependency upgrade independently.\n\n"
        "Run the project's test command, read the actual output, inspect git diff, "
        "and decide whether the final project state is green. Do not make edits "
        "in this verification pass. Return exactly one JSON object with this shape: "
        '{"ok": true|false, "command": "npm test", "summary": "...", '
        '"passing_count": 28|null}.\n\n'
        f"Previous step summary:\n{summary}"
    )


def _batch_heal_task(state: UpgradeGraphState) -> str:
    verify_result = state.get("verify_result")
    failure = verify_result.final_text if verify_result else "(no verification result)"
    return (
        "Self-heal the failed batch upgrade.\n\n"
        "Use the verification output below as the source of truth. First run npm "
        "test so the runtime baseline guardrail has current evidence, then make "
        "the smallest targeted edit required. Only fix breakages caused by the "
        "batch upgrade. Rerun tests and inspect git diff. If a package cannot be "
        "safely fixed, revert that package's attempted change and report the blocker.\n\n"
        f"Verification failure:\n{failure}"
    )


def _baseline_from_result(result: LoopResult) -> BaselineState:
    try:
        return parse_structured_text(result.final_text, BaselineState)
    except StructuredParseError:
        return BaselineState(
            ran=True,
            green=_result_passed(result),
            command="npm test",
            summary=result.final_text,
        )


def _research_from_result(result: LoopResult, target: str) -> ResearchBrief:
    try:
        return parse_structured_text(result.final_text, ResearchBrief)
    except StructuredParseError:
        return ResearchBrief(
            package=_dependency_name(target),
            target_version=_target_version(target),
            relevant_risks=[result.final_text],
        )


def _single_upgrade_plan(target: str, state: UpgradeGraphState) -> UpgradePlan:
    dependency = _dependency_name(target)
    research = state.get("research")
    target_version = research.target_version if research else None
    return UpgradePlan(
        dependency=dependency,
        target_version=target_version or _target_version(target),
        steps=[
            "confirm baseline",
            "apply minimal dependency version change",
            "install/update lockfile",
            "run verification tests",
        ],
        allowed_files=[],
    )


def _batch_agent_report(
    state: UpgradeGraphState,
    changed_files: list[str],
    verification: VerificationResult | None,
) -> AgentReport:
    baseline = state.get("baseline")
    if not baseline or not baseline.green:
        return _baseline_failure_report(baseline, changed_files)

    ok = bool(verification and verification.ok)
    verification_summary = verification.summary if verification else "verification did not run"
    package_results = state.get("package_results", [])
    package_summary = ", ".join(f"{record.name}: {record.status}" for record in package_results)
    summary = (
        verification_summary
        if not package_summary
        else f"{verification_summary}; {package_summary}"
    )
    failed_packages = [
        f"{record.name}: {record.summary}"
        for record in package_results
        if record.status == "failed"
    ]
    failed_package_names = [record.name for record in package_results if record.status == "failed"]
    failure_reason = None
    if not ok:
        failure_reason = "package_failed" if failed_package_names else "verification_failed"
    remaining_risks = [] if ok else ["batch upgrade verification failed", *failed_packages]
    recovery_suggestions = [
        f"Review or revert failed package: {package_name}" for package_name in failed_package_names
    ]
    if not ok and not recovery_suggestions:
        recovery_suggestions = [f"Inspect verification failure: {verification_summary}"]
    return AgentReport(
        ok=ok,
        summary=summary,
        changed_files=changed_files,
        remaining_risks=remaining_risks,
        failure_reason=failure_reason,
        recovery_suggestions=recovery_suggestions,
    )


def _single_agent_report(
    state: UpgradeGraphState,
    changed_files: list[str],
    verification: VerificationResult | None,
) -> AgentReport:
    baseline = state.get("baseline")
    if not baseline or not baseline.green:
        return _baseline_failure_report(baseline, changed_files)

    ok = bool(verification and verification.ok)
    summary = verification.summary if verification else "verification did not run"
    return AgentReport(
        ok=ok,
        summary=summary,
        changed_files=changed_files,
        remaining_risks=[] if ok else ["upgrade verification failed"],
        failure_reason=None if ok else "verification_failed",
        recovery_suggestions=[] if ok else [f"Inspect verification failure: {summary}"],
    )


def _baseline_failure_report(
    baseline: BaselineState | None,
    changed_files: list[str],
) -> AgentReport:
    command = baseline.command if baseline and baseline.command else "npm test"
    summary = baseline.summary if baseline and baseline.summary else "baseline did not pass"
    return AgentReport(
        ok=False,
        summary=(
            "Target project baseline is not green; aborting before dependency upgrade work. "
            f"{command}: {summary}"
        ),
        changed_files=changed_files,
        remaining_risks=["target project baseline is red"],
        failure_reason="baseline_failed",
        recovery_suggestions=[
            "Fix the target project's existing baseline failures before upgrading.",
        ],
    )


def _dry_run_agent_report(*, summary: str, risks: list[str]) -> AgentReport:
    return AgentReport(
        ok=True,
        summary=summary,
        changed_files=[],
        remaining_risks=risks,
        recovery_suggestions=[],
    )


def _result_passed(result: LoopResult) -> bool:
    text = result.final_text.lower()
    if "verdict: pass" in text:
        return result.ok
    if "verdict: fail" in text:
        return False
    failure_markers = ("failing", "failed", "error", "red baseline", "cannot verify")
    return result.ok and not any(marker in text for marker in failure_markers)


def _verification_from_result(result: LoopResult) -> VerificationResult:
    try:
        return parse_structured_text(result.final_text, VerificationResult)
    except StructuredParseError:
        return VerificationResult(
            ok=False,
            command="npm test",
            summary=result.final_text,
        )


def _queue_from_result(result: LoopResult) -> UpgradeQueue:
    try:
        return parse_structured_text(result.final_text, UpgradeQueue)
    except StructuredParseError:
        return UpgradeQueue()


def _allowed_files_from_state(state: UpgradeGraphState) -> tuple[str, ...]:
    plan = state.get("plan")
    return tuple(plan.allowed_files) if plan else ()


def _collect_changed_files(
    state: UpgradeGraphState,
    collect_changed_files: ChangedFilesCollector | None,
) -> list[str]:
    if collect_changed_files is None:
        return state.get("changed_files", [])
    changed_files = collect_changed_files()
    return changed_files if changed_files is not None else state.get("changed_files", [])


def _dependency_name(target: str) -> str:
    return target.split()[0] if target.split() else target


def _target_version(target: str) -> str | None:
    if "->" not in target:
        return None
    return target.rsplit("->", 1)[1].strip() or None
