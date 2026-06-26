"""CLI entrypoint.

    uv run upgrade-dependencies-agent analyze ../some-project
    uv run upgrade-dependencies-agent analyze-coverage ../some-project
    uv run upgrade-dependencies-agent generate-tests ../some-project "cover src/foo edge cases"
    uv run upgrade-dependencies-agent research-upgrade ../some-project "mocha 4 -> 11"
    uv run upgrade-dependencies-agent upgrade ../some-project "mocha 4 -> 11"
    uv run upgrade-dependencies-agent upgrade-graph ../some-project "mocha 4 -> 11"
    uv run upgrade-dependencies-agent upgrade-all ../some-project
    uv run upgrade-dependencies-agent ask ../some-project "any free-form task"

Commands:
- ``analyze`` — read-only ReAct run that profiles a project.
- ``analyze-coverage`` — read-only test-gap and coverage analysis.
- ``generate-tests`` — add focused tests and verify them.
- ``research-upgrade`` — read-only breaking-change research for one upgrade.
- ``upgrade`` — full-tool upgrade of ONE dependency (baseline → change → verify).
- ``upgrade-graph`` — LangGraph-orchestrated upgrade with verify → self-heal.
- ``upgrade-all`` — full-tool upgrade of all direct dependencies, one at a time.
- ``ask``     — give the agent any task against a project, with full tools.

All wire the same primitives: create_client() → ReActLoop → tools, observed by
the RichUI. This is the user-facing surface; everything substantive is in core/.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console

from ..core import AgentConfig, LoopResult, ReActLoop, create_client
from ..orchestrator import StageLoopRequest, run_upgrade_backbone_workflow
from ..skills import (
    ADD_TESTS_ANALYZE,
    ADD_TESTS_GENERATE,
    ANALYZE,
    BASE_AGENT,
    BREAKING_CHANGE_RESEARCHER,
    UPGRADE,
    UPGRADE_ALL,
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


@app.command("generate-tests")
def generate_tests(
    project: Path = typer.Argument(..., help="Path to the target project."),
    focus: str = typer.Argument(
        "highest-priority uncovered behavior",
        help="Test gap, source area, or behavior to cover.",
    ),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(45, "--max-iters"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Generate focused tests, then verify npm test and coverage if available."""
    workdir = _resolve_workdir(project)
    console.rule(f"[bold]generating tests[/bold] in {workdir}")

    client = create_client()
    loop = ReActLoop(
        client=client,
        config=AgentConfig(
            model=model,
            system_prompt=ADD_TESTS_GENERATE,
            max_iterations=max_iterations,
        ),
        tools=default_tools(),
        workdir=workdir,
        callbacks=RichUI(verbose=verbose),
    )
    task = (
        f"Generate tests for this focus area: {focus}.\n\n"
        "Start by establishing the existing npm test baseline, inspect current "
        "test style and coverage signals, add a focused reviewable batch of "
        "tests, verify with npm test, check coverage if available, then report "
        "the final result and remaining gaps."
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


@app.command()
def upgrade(
    project: Path = typer.Argument(..., help="Path to the target project."),
    target: str = typer.Argument(..., help='Upgrade target, e.g. "mocha 4 -> 11".'),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(40, "--max-iters"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Upgrade ONE dependency: baseline → change → verify (with self-heal)."""
    workdir = _resolve_workdir(project)
    console.rule(f"[bold]upgrading[/bold] {target} in {workdir}")

    client = create_client()
    loop = ReActLoop(
        client=client,
        config=AgentConfig(
            model=model,
            system_prompt=UPGRADE,
            max_iterations=max_iterations,
            enforce_baseline_guardrail=True,
        ),
        tools=default_tools(),
        workdir=workdir,
        callbacks=RichUI(verbose=verbose),
    )
    task = (
        f"Upgrade the dependency: {target}.\n\n"
        "Follow the upgrade workflow strictly: first establish a green test "
        "baseline (run the tests, record the passing count), then research "
        "breaking changes, then make the minimal version change, adapt the code "
        "if needed, and verify tests still pass with the same count. Report "
        "what broke and what you fixed."
    )
    result = loop.run(task)
    raise typer.Exit(code=0 if result.ok else 1)


@app.command("upgrade-graph")
def upgrade_graph(
    project: Path = typer.Argument(..., help="Path to the target project."),
    target: str = typer.Argument(..., help='Upgrade target, e.g. "mocha 4 -> 11".'),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(40, "--max-iters"),
    max_heal_attempts: int = typer.Option(1, "--max-heal-attempts"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Upgrade one dependency through the full LangGraph backbone."""
    workdir = _resolve_workdir(project)
    console.rule(f"[bold]graph upgrade[/bold] {target} in {workdir}")

    client = create_client()
    ui = RichUI(verbose=verbose)

    def run_loop(request: StageLoopRequest) -> LoopResult:
        loop = ReActLoop(
            client=client,
            config=AgentConfig(
                model=model,
                system_prompt=request.system_prompt,
                max_iterations=max_iterations,
                enforce_baseline_guardrail=request.enforce_baseline_guardrail,
            ),
            tools=read_only_tools() if request.read_only else default_tools(),
            workdir=workdir,
            callbacks=ui,
        )
        return loop.run(request.task)

    result = run_upgrade_backbone_workflow(
        target,
        max_heal_attempts=max_heal_attempts,
        run_loop=run_loop,
    )
    raise typer.Exit(code=0 if result.ok else 1)


@app.command("upgrade-all")
def upgrade_all(
    project: Path = typer.Argument(..., help="Path to the target project."),
    model: str = typer.Option(_default_model(), "--model", "-m"),
    max_iterations: int = typer.Option(80, "--max-iters"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Upgrade all direct dependencies to latest, one package at a time."""
    workdir = _resolve_workdir(project)
    console.rule(f"[bold]upgrading all dependencies[/bold] in {workdir}")

    client = create_client()
    loop = ReActLoop(
        client=client,
        config=AgentConfig(
            model=model,
            system_prompt=UPGRADE_ALL,
            max_iterations=max_iterations,
            enforce_baseline_guardrail=True,
        ),
        tools=default_tools(),
        workdir=workdir,
        callbacks=RichUI(verbose=verbose),
    )
    task = (
        "Upgrade all direct npm dependencies and devDependencies in this project "
        "to their latest stable versions.\n\n"
        "Follow the all-dependencies upgrade workflow strictly: first establish "
        "a green test baseline and record the passing count, then use npm_outdated "
        "to build the package queue, upgrade exactly one direct package at a time, "
        "verify tests after each package, fix only breakages caused by that package, "
        "and finish with a final test run plus git diff review. If one package "
        "cannot be fixed safely, revert that package and continue with the rest."
    )
    result = loop.run(task)
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
