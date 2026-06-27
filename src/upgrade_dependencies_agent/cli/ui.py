"""Rich-powered live UI that observes the ReAct loop via callbacks.

This implements :class:`LoopCallbacks`. The loop knows nothing about rich or
the console — it just calls our methods. That separation is the whole point of
the callback design: the loop is pure logic, the UI is a spectator.

Rendering strategy: we print as we go (one logical event per block) rather than
a full-screen live dashboard. Streaming-print is simpler, works in CI/logs, and
still looks great in a terminal.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ..core.react_loop import LoopResult
from ..core.types import ToolResult

__all__ = ["RichUI"]


class RichUI:
    """A :class:`LoopCallbacks` implementation that pretty-prints the run.

    Pass an instance as ``ReActLoop(..., callbacks=RichUI())``.
    """

    def __init__(
        self,
        console: Console | None = None,
        verbose: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.console = console or Console()
        self.verbose = verbose
        self._clock = clock
        self._iter = 0
        self._started_at: float | None = None
        self._tool_calls = 0
        self._tool_ok = 0
        self._tool_errors = 0

    # ---- LoopCallbacks implementation ---- #

    def on_assistant_text(self, text: str) -> None:
        """The model's reasoning. Shown subtly so it doesn't drown out actions."""
        if not text.strip():
            return
        body = text if self.verbose else _truncate(text, 600)
        self.console.print(
            Panel(
                Text(body, style="dim"),
                border_style="cyan",
                title="assistant",
                title_align="left",
            )
        )

    def on_tool_call(self, name: str, args: dict[str, Any]) -> None:
        self._mark_started()
        self._tool_calls += 1
        self.console.print(
            f"  [bold yellow]→[/bold yellow] [bold]{name}[/bold] [dim]{_compact_args(args)}[/dim]"
        )

    def on_tool_result(self, name: str, result: ToolResult) -> None:
        self._mark_started()
        style = "red" if result.is_error else "green"
        mark = "✗" if result.is_error else "✓"
        if result.is_error:
            self._tool_errors += 1
        else:
            self._tool_ok += 1
        preview = _truncate(result.output, 400)
        self.console.print(f"  [{style}]{mark} {name}[/{style}] [dim]{preview}[/dim]")

    def on_iteration(self, n: int, response: Any) -> None:
        now = self._ensure_started()
        self._iter = n
        in_tok = response.input_tokens
        out_tok = response.output_tokens
        self.console.print(
            f"\n[bold blue]── iteration {n}[/bold blue] "
            f"[dim](in {in_tok} / out {out_tok} tokens · elapsed {self._elapsed(now):.1f}s)[/dim]"
        )

    def on_finish(self, result: LoopResult) -> None:
        now = self._ensure_started()
        status = "[bold green]success[/bold green]" if result.ok else "[bold red]failed[/bold red]"
        self.console.print()
        self.console.rule(f"[bold]{status}[/bold] · {result.iterations} iterations")
        self.console.print(f"  elapsed: [bold]{self._elapsed(now):.1f}s[/bold]")
        self.console.print(
            f"  tools:  [bold]{self._tool_calls}[/bold] calls / "
            f"[bold]{self._tool_ok}[/bold] ok / [bold]{self._tool_errors}[/bold] error"
        )
        self.console.print(
            f"  tokens: [bold]{result.total_input_tokens}[/bold] in / "
            f"[bold]{result.total_output_tokens}[/bold] out"
        )
        if result.trace_path:
            self.console.print(f"  trace:  [dim]{result.trace_path}[/dim]")
        if result.error:
            self.console.print(f"  error:  [red]{result.error}[/red]")
        self.console.print()
        if result.final_text.strip():
            self.console.print(
                Panel(
                    result.final_text,
                    border_style="bold green",
                    title="UPGRADE REPORT",
                    title_align="center",
                    padding=(1, 2),
                )
            )
        else:
            self.console.print("[dim](no report produced)[/dim]")

    def _ensure_started(self) -> float:
        now = self._clock()
        if self._started_at is None:
            self._started_at = now
        return now

    def _mark_started(self) -> None:
        if self._started_at is None:
            self._started_at = self._clock()

    def _elapsed(self, now: float | None = None) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, (self._clock() if now is None else now) - self._started_at)


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " …"


def _compact_args(args: dict[str, Any]) -> str:
    """Render tool args compactly for the one-line call display."""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 80:
            s = s[:80] + "…"
        parts.append(f"{k}={s}")
    return ", ".join(parts)
