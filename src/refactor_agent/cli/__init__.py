"""CLI entrypoint.

    uv run refactor-agent analyze ../some-project
    uv run refactor-agent ask "any free-form task"

Two commands for now:
- ``analyze``  — read-only ReAct run that profiles a project (M1 demo).
- ``ask``      — give the agent any task against a project, with full tools.

Both wire the same primitives: LLMClient → ReActLoop → tools, observed by the
RichUI. This is the user-facing surface; everything substantive is in core/.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console

from ..core import AgentConfig, ReActLoop, create_client
from ..skills import ANALYZE
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
    name="refactor-agent",
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
        config=AgentConfig(model=model, system_prompt=ANALYZE, max_iterations=max_iterations),
        tools=tools,
        workdir=workdir,
        callbacks=RichUI(verbose=verbose),
    )
    result = loop.run(task)
    raise typer.Exit(code=0 if result.ok else 1)


if __name__ == "__main__":
    app()
