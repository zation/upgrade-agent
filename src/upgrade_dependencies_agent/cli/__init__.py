"""CLI entrypoint.

    uv run upgrade-dependencies-agent analyze ../some-project
    uv run upgrade-dependencies-agent analyze-coverage ../some-project
    uv run upgrade-dependencies-agent improve-tests ../some-project "cover src/foo edge cases"
    uv run upgrade-dependencies-agent research-upgrade ../some-project "mocha 4 -> 11"
    uv run upgrade-dependencies-agent upgrade ../some-project "mocha 4 -> 11"
    uv run upgrade-dependencies-agent upgrade-all ../some-project
    uv run upgrade-dependencies-agent ask ../some-project "any free-form task"

Commands:
- ``analyze`` — read-only ReAct run that profiles a project.
- ``analyze-coverage`` — read-only test-gap and coverage analysis.
- ``improve-tests`` — repair a failing test baseline, then add focused tests.
- ``research-upgrade`` — read-only breaking-change research for one upgrade.
- ``upgrade`` — LangGraph-backed upgrade of ONE dependency.
- ``upgrade-all`` — graph-backed batch upgrade of direct dependencies.
- ``ask``     — give the agent any task against a project, with full tools.

All wire the same primitives: create_client() → ReActLoop → tools, observed by
the RichUI. This is the user-facing surface; everything substantive is in core/.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console

from ..core import AgentConfig, LoopResult, ReActLoop, create_client
from ..orchestrator import (
    AgentReport,
    StageLoopRequest,
    UpgradeBackboneResult,
    run_upgrade_all_backbone_workflow,
    run_upgrade_all_dry_run_workflow,
    run_upgrade_backbone_workflow,
    run_upgrade_dry_run_workflow,
)
from ..orchestrator.preflight import check_clean_worktree, dirty_worktree_status
from ..skills import (
    ADD_TESTS_ANALYZE,
    ADD_TESTS_IMPROVE,
    ANALYZE,
    BASE_AGENT,
    BREAKING_CHANGE_RESEARCHER,
)
from ..tools import default_tools, read_only_tools
from .ui import RichUI


def _default_model() -> str:
    """Pick a sensible default model for the active provider.

    Read from LLM_MODEL if set, otherwise infer from LLM_PROVIDER (deepseek-chat
    for OpenAI-compat, claude-sonnet-4-5 for Anthropic).
    """
    if m := os.environ.get("LLM_MODEL"):
        return m
    if os.environ.get("LLM_PROVIDER", "anthropic").lower() in (
        "openai-compat",
        "deepseek",
        "ollama",
        "openai",
    ):
        return "deepseek-chat"
    return "claude-sonnet-4-5"


app = typer.Typer(
    name="upgrade-dependencies-agent",
    help="A ReAct + LangGraph agent for upgrading legacy JS/TS projects.",
    no_args_is_help=True,
)
console = Console()
CliStageLoopRunner = Callable[[StageLoopRequest], LoopResult]


def _resolve_workdir(path: Path) -> str:
    """Validate and normalize the target project path."""
    p = path.expanduser().resolve()
    if not p.exists():
        raise typer.BadParameter(f"Path does not exist: {p}")
    if not p.is_dir():
        raise typer.BadParameter(f"Path is not a directory: {p}")
    return str(p)


@app.command()
def analyze(
    project: Path = typer.Argument(..., help="Path to the target project."),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(20, "--max-iters"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Read-only analysis: profile a project and report upgrade risks."""
    workdir = _resolve_workdir(project)
    console.rule(f"[bold]analyzing[/bold] {workdir}")

    client = create_client()
    loop = ReActLoop(
        client=client,
        config=AgentConfig(model=model, system_prompt=ANALYZE, max_iterations=max_iterations),
        tools=read_only_tools(),
        workdir=workdir,
        callbacks=RichUI(verbose=verbose),
    )
    task = (
        "Analyze this project and produce an upgrade-readiness profile. "
        "Read package.json, the main source files, and CI config; then summarize "
        "dependencies, tech/style signals, and upgrade risks."
    )
    result = loop.run(task)
    raise typer.Exit(code=0 if result.ok else 1)


@app.command("analyze-coverage")
def analyze_coverage(
    project: Path = typer.Argument(..., help="Path to the target project."),
    focus: str | None = typer.Argument(
        None, help="Optional source area, module, or behavior to prioritize."
    ),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(25, "--max-iters"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Read-only analysis of missing or weak test coverage."""
    workdir = _resolve_workdir(project)
    console.rule(f"[bold]analyzing coverage[/bold] in {workdir}")

    client = create_client()
    loop = ReActLoop(
        client=client,
        config=AgentConfig(
            model=model,
            system_prompt=ADD_TESTS_ANALYZE,
            max_iterations=max_iterations,
        ),
        tools=read_only_tools(),
        workdir=workdir,
        callbacks=RichUI(verbose=verbose),
    )
    focus_text = f"\nPrioritize this focus area: {focus}." if focus else ""
    task = (
        "Analyze this project's current tests and coverage signals. "
        "Produce a prioritized test gap list with file / function / suggested "
        "test scenarios, evidence, and recommended test locations."
        f"{focus_text}"
    )
    result = loop.run(task)
    raise typer.Exit(code=0 if result.ok else 1)


@app.command("improve-tests")
def improve_tests(
    project: Path = typer.Argument(..., help="Path to the target project."),
    focus: str = typer.Argument(
        "highest-priority uncovered behavior",
        help="Test gap, source area, or behavior to cover.",
    ),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(45, "--max-iters"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Repair the test baseline if needed, add focused tests, then verify."""
    workdir = _resolve_workdir(project)
    console.rule(f"[bold]improving tests[/bold] in {workdir}")

    client = create_client()
    loop = ReActLoop(
        client=client,
        config=AgentConfig(
            model=model,
            system_prompt=ADD_TESTS_IMPROVE,
            max_iterations=max_iterations,
        ),
        tools=default_tools(),
        workdir=workdir,
        callbacks=RichUI(verbose=verbose),
    )
    task = (
        f"Improve tests for this focus area: {focus}.\n\n"
        "Start by establishing the existing npm test baseline. If it is red, "
        "repair the existing failing tests or genuine pre-existing bug with the "
        "smallest targeted edit, then rerun npm test until the baseline is green. "
        "After that, inspect current test style and coverage signals, add a "
        "focused reviewable batch of tests, verify with npm test, check coverage "
        "if available, then report baseline repairs, tests added, final result, "
        "and remaining gaps."
    )
    result = loop.run(task)
    raise typer.Exit(code=0 if result.ok else 1)


@app.command("research-upgrade")
def research_upgrade(
    project: Path = typer.Argument(..., help="Path to the target project."),
    target: str = typer.Argument(..., help='Upgrade target, e.g. "mocha 4 -> 11".'),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(20, "--max-iters"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Read-only breaking-change research for one dependency upgrade."""
    workdir = _resolve_workdir(project)
    console.rule(f"[bold]researching upgrade[/bold] {target} in {workdir}")

    client = create_client()
    loop = ReActLoop(
        client=client,
        config=AgentConfig(
            model=model,
            system_prompt=BREAKING_CHANGE_RESEARCHER,
            max_iterations=max_iterations,
        ),
        tools=read_only_tools(),
        workdir=workdir,
        callbacks=RichUI(verbose=verbose),
    )
    task = (
        f"Research this dependency upgrade without editing files: {target}.\n\n"
        "Use package metadata, release/changelog sources, and project usage search "
        "to identify relevant breaking changes. End with the required verdict line."
    )
    result = loop.run(task)
    raise typer.Exit(code=0 if result.ok else 1)


def _run_upgrade_backbone_cli(
    *,
    target: str,
    model: str,
    max_iterations: int,
    max_heal_attempts: int,
    workdir: str,
    ui: RichUI,
) -> UpgradeBackboneResult:
    client = create_client()
    result = run_upgrade_backbone_workflow(
        target,
        max_heal_attempts=max_heal_attempts,
        run_loop=_make_stage_loop_runner(
            client=client,
            model=model,
            max_iterations=max_iterations,
            workdir=workdir,
            ui=ui,
        ),
        collect_changed_files=lambda: _changed_worktree_paths(workdir),
    )
    return result


def _run_upgrade_dry_run_cli(
    *,
    target: str,
    model: str,
    max_iterations: int,
    workdir: str,
    ui: RichUI,
) -> UpgradeBackboneResult:
    client = create_client()
    return run_upgrade_dry_run_workflow(
        target,
        run_loop=_make_stage_loop_runner(
            client=client,
            model=model,
            max_iterations=max_iterations,
            workdir=workdir,
            ui=ui,
        ),
    )


def _run_explicit_upgrade_targets_cli(
    *,
    targets: list[str],
    model: str,
    max_iterations: int,
    max_heal_attempts: int,
    workdir: str,
    ui: RichUI,
    dry_run: bool,
) -> UpgradeBackboneResult:
    results: list[UpgradeBackboneResult] = []
    for target in targets:
        if dry_run:
            result = _run_upgrade_dry_run_cli(
                target=target,
                model=model,
                max_iterations=max_iterations,
                workdir=workdir,
                ui=ui,
            )
        else:
            result = _run_upgrade_backbone_cli(
                target=target,
                model=model,
                max_iterations=max_iterations,
                max_heal_attempts=max_heal_attempts,
                workdir=workdir,
                ui=ui,
            )
        results.append(result)
        if not result.ok:
            break
    return _combine_explicit_upgrade_results(targets, results, dry_run=dry_run)


def _run_upgrade_all_backbone_cli(
    *,
    model: str,
    max_iterations: int,
    max_heal_attempts: int,
    workdir: str,
    ui: RichUI,
) -> UpgradeBackboneResult:
    client = create_client()
    result = run_upgrade_all_backbone_workflow(
        max_heal_attempts=max_heal_attempts,
        run_loop=_make_stage_loop_runner(
            client=client,
            model=model,
            max_iterations=max_iterations,
            workdir=workdir,
            ui=ui,
        ),
        collect_changed_files=lambda: _changed_worktree_paths(workdir),
    )
    return result


def _run_upgrade_all_dry_run_cli(
    *,
    model: str,
    max_iterations: int,
    workdir: str,
    ui: RichUI,
) -> UpgradeBackboneResult:
    client = create_client()
    return run_upgrade_all_dry_run_workflow(
        run_loop=_make_stage_loop_runner(
            client=client,
            model=model,
            max_iterations=max_iterations,
            workdir=workdir,
            ui=ui,
        ),
    )


def _make_stage_loop_runner(
    *,
    client: object,
    model: str,
    max_iterations: int,
    workdir: str,
    ui: RichUI,
) -> CliStageLoopRunner:
    clean_worktree_checked = False

    def run_loop(request: StageLoopRequest) -> LoopResult:
        nonlocal clean_worktree_checked
        if request.enforce_baseline_guardrail and not clean_worktree_checked:
            clean_worktree_checked = True
            preflight = check_clean_worktree(workdir)
            if not preflight.ok:
                message = (
                    "Target worktree is not clean before the first mutation stage. "
                    "Commit, stash, or remove existing changes before running an upgrade.\n\n"
                    f"git status --porcelain:\n{preflight.details}"
                )
                return LoopResult(
                    final_text=message,
                    stop_reason="error",
                    iterations=0,
                    messages=[],
                    run_id="dirty-worktree-preflight",
                    error="dirty_worktree",
                )

        loop = ReActLoop(
            client=client,
            config=AgentConfig(
                model=model,
                system_prompt=request.system_prompt,
                max_iterations=request.max_iterations or max_iterations,
                enforce_baseline_guardrail=request.enforce_baseline_guardrail,
                current_dependency=request.current_dependency,
                allowed_files=request.allowed_files,
                response_format=request.response_format,
            ),
            tools=read_only_tools() if request.read_only else default_tools(),
            workdir=workdir,
            callbacks=ui,
        )
        return loop.run(request.task)

    return run_loop


def _dirty_worktree_status(workdir: str) -> str | None:
    return dirty_worktree_status(workdir)


def _changed_worktree_paths(workdir: str) -> list[str] | None:
    status = _dirty_worktree_status(workdir)
    if status is None:
        return [] if _is_git_worktree(workdir) else None
    return sorted({_status_path(line) for line in status.splitlines() if line.strip()})


def _is_git_worktree(workdir: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _status_path(line: str) -> str:
    parts = line.strip().split(maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def _write_report_json(result: UpgradeBackboneResult, path: Path, *, workdir: str) -> None:
    report = _report_json_payload(result, workdir=workdir)
    if report is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _report_json_payload(
    result: UpgradeBackboneResult,
    *,
    workdir: str,
) -> dict[str, object] | None:
    if result.report is None:
        return None
    report = result.report.model_dump(mode="json")
    changed_files = _changed_worktree_paths(workdir)
    if changed_files is not None:
        report["changed_files"] = changed_files
    return report


def _print_report_json(result: UpgradeBackboneResult, *, workdir: str) -> None:
    report = _report_json_payload(result, workdir=workdir)
    if report is None:
        report = {
            "ok": result.ok,
            "summary": "workflow completed without a structured report",
            "changed_files": _changed_worktree_paths(workdir) or [],
            "remaining_risks": [],
        }
    typer.echo(json.dumps(report, ensure_ascii=False))


def _explicit_upgrade_targets(target: str) -> list[str]:
    return [part.strip() for part in target.split(",") if part.strip()]


def _combine_explicit_upgrade_results(
    targets: list[str],
    results: list[UpgradeBackboneResult],
    *,
    dry_run: bool,
) -> UpgradeBackboneResult:
    ok = len(results) == len(targets) and all(result.ok for result in results)
    reports = [
        result.report.model_dump(mode="json") for result in results if result.report is not None
    ]
    changed_files = sorted(
        {changed_file for report in reports for changed_file in report.get("changed_files", [])}
    )
    remaining_risks = [
        f"{target}: {risk}"
        for target, report in zip(targets, reports, strict=False)
        for risk in report.get("remaining_risks", [])
    ]
    recovery_suggestions = [
        suggestion for report in reports for suggestion in report.get("recovery_suggestions", [])
    ]
    completed_targets = targets[: len(results)]
    action = "Planned" if dry_run else "Upgraded"
    summary = (
        f"{action} {len(completed_targets)} explicit dependencies: {', '.join(completed_targets)}"
    )
    report = AgentReport(
        ok=ok,
        summary=summary,
        changed_files=changed_files,
        remaining_risks=remaining_risks,
        failure_reason=None if ok else "explicit_dependency_failed",
        recovery_suggestions=recovery_suggestions,
    )
    history = tuple(
        history_item for result in results for history_item in getattr(result, "history", ())
    )
    return UpgradeBackboneResult(
        ok=ok,
        state={
            "task": summary,
            "phase": "done",
            "report": report,
            "changed_files": changed_files,
            "history": list(history),
        },
        report=report,
        heal_attempts=sum(getattr(result, "heal_attempts", 0) for result in results),
        history=history,
    )


@app.command()
def upgrade(
    project: Path = typer.Argument(..., help="Path to the target project."),
    target: str = typer.Argument(..., help='Upgrade target, e.g. "mocha 4 -> 11".'),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(40, "--max-iters"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    report_json: Path | None = typer.Option(None, "--report-json"),
    output_json: bool = typer.Option(False, "--json", help="Print AgentReport JSON to stdout."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Research and plan only; do not execute mutating upgrade stages.",
    ),
) -> None:
    """Upgrade ONE dependency: baseline → change → verify (with self-heal)."""
    workdir = _resolve_workdir(project)
    if not output_json:
        action = "planning upgrade" if dry_run else "upgrading"
        console.rule(f"[bold]{action}[/bold] {target} in {workdir}")

    ui = object() if output_json else RichUI(verbose=verbose)
    explicit_targets = _explicit_upgrade_targets(target)
    if len(explicit_targets) > 1:
        result = _run_explicit_upgrade_targets_cli(
            targets=explicit_targets,
            model=model,
            max_iterations=max_iterations,
            max_heal_attempts=1,
            workdir=workdir,
            ui=ui,
            dry_run=dry_run,
        )
    elif dry_run:
        result = _run_upgrade_dry_run_cli(
            target=target,
            model=model,
            max_iterations=max_iterations,
            workdir=workdir,
            ui=ui,
        )
    else:
        result = _run_upgrade_backbone_cli(
            target=target,
            model=model,
            max_iterations=max_iterations,
            max_heal_attempts=1,
            workdir=workdir,
            ui=ui,
        )
    if report_json is not None:
        _write_report_json(result, report_json, workdir=workdir)
    if output_json:
        _print_report_json(result, workdir=workdir)
    raise typer.Exit(code=0 if result.ok else 1)


@app.command("upgrade-all")
def upgrade_all(
    project: Path = typer.Argument(..., help="Path to the target project."),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(80, "--max-iters"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    report_json: Path | None = typer.Option(None, "--report-json"),
    output_json: bool = typer.Option(False, "--json", help="Print AgentReport JSON to stdout."),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Build the upgrade queue and plan only; do not execute mutating upgrade stages.",
    ),
) -> None:
    """Upgrade all direct dependencies to latest, one package at a time."""
    workdir = _resolve_workdir(project)
    if not output_json:
        action = "planning all dependency upgrades" if dry_run else "upgrading all dependencies"
        console.rule(f"[bold]{action}[/bold] in {workdir}")

    ui = object() if output_json else RichUI(verbose=verbose)
    if dry_run:
        result = _run_upgrade_all_dry_run_cli(
            model=model,
            max_iterations=max_iterations,
            workdir=workdir,
            ui=ui,
        )
    else:
        result = _run_upgrade_all_backbone_cli(
            model=model,
            max_iterations=max_iterations,
            max_heal_attempts=1,
            workdir=workdir,
            ui=ui,
        )
    if report_json is not None:
        _write_report_json(result, report_json, workdir=workdir)
    if output_json:
        _print_report_json(result, workdir=workdir)
    raise typer.Exit(code=0 if result.ok else 1)


@app.command()
def ask(
    project: Path = typer.Argument(..., help="Path to the target project."),
    task: str = typer.Argument(..., help="The task for the agent."),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(30, "--max-iters"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    read_only: bool = typer.Option(False, "--read-only", help="Disable mutation/shell tools."),
) -> None:
    """Run the agent with full tools on an arbitrary task."""
    workdir = _resolve_workdir(project)
    console.rule(f"[bold]agent task[/bold] on {workdir}")

    client = create_client()
    tools = read_only_tools() if read_only else default_tools()
    loop = ReActLoop(
        client=client,
        config=AgentConfig(model=model, system_prompt=BASE_AGENT, max_iterations=max_iterations),
        tools=tools,
        workdir=workdir,
        callbacks=RichUI(verbose=verbose),
    )
    result = loop.run(task)
    raise typer.Exit(code=0 if result.ok else 1)


if __name__ == "__main__":
    app()
