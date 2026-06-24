"""The ReAct loop — the beating heart of this project.

ReAct = **Rea**son + **Act**. The pattern, in one sentence:

    The model thinks (text), optionally calls tools (acts), observes the tool
    results, and loops until it decides it's done.

This file implements that loop by hand, against the raw Anthropic SDK, with no
"agent framework" in between. ~120 lines of actual logic. Read it top to bottom
and you understand how every tool-calling agent works under the hood.

The loop is:
  1. Send the conversation + tool definitions to the model.
  2. Look at the response:
       - if it contains `tool_use` blocks  -> execute each tool, append the
         results as a `tool_result` user message, go to 1.
       - otherwise (end_turn)              -> we're done; return the final text.
  3. Always bounded by `max_iterations` so it can never spin forever.

Three engineering concerns are layered on top without obscuring that core:
  * **tracing** — every step is recorded for replay/debug (see trace.py).
  * **callbacks** — the loop emits events (assistant text, tool calls) so the
    CLI can render a live, colored view without the loop knowing about UI.
  * **context compaction** — when the conversation nears the token budget we
    shrink older history so long upgrade sessions don't 400 (see context.py).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .context import ContextBudget, compact_history, needs_compaction
from .llm_client import LLMClient, LLMResponse
from .trace import Tracer
from .types import (
    AgentConfig,
    Message,
    TextBlock,
    Tool,
    ToolContext,
    ToolResult,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = ["LoopCallbacks", "LoopResult", "ReActLoop"]

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Callbacks: how the outside world observes the loop (without coupling to it)
# --------------------------------------------------------------------------- #
@runtime_checkable
class LoopCallbacks(Protocol):
    """Optional observer the loop notifies. The CLI implements this for live UI.

    All methods are optional: the loop uses ``getattr``-with-default so a bare
    object (or nothing) works too. This decouples "what the loop does" from
    "how we show it".
    """

    def on_assistant_text(self, text: str) -> None: ...
    def on_tool_call(self, name: str, args: dict[str, Any]) -> None: ...
    def on_tool_result(self, name: str, result: ToolResult) -> None: ...
    def on_iteration(self, n: int, response: LLMResponse) -> None: ...
    def on_finish(self, result: LoopResult) -> None: ...


class _NoCallbacks:
    """Default no-op callbacks. Avoids None-checks scattered through the loop."""


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #
@dataclass
class LoopResult:
    """What the loop returns when it terminates."""

    final_text: str
    stop_reason: str
    iterations: int
    messages: list[Message]  # full transcript (already compacted if needed)
    run_id: str
    trace_path: Path | None = None
    # Cumulative token usage across all turns — handy for cost reporting.
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.stop_reason in ("end_turn", "stop_sequence")


# --------------------------------------------------------------------------- #
# The loop itself
# --------------------------------------------------------------------------- #
@dataclass
class ReActLoop:
    """Runs a ReAct conversation to completion against a set of tools.

    Construct once per task; :meth:`run` does the work. Kept as a dataclass so
    callers can tweak fields (config, budget) between runs.
    """

    client: LLMClient
    config: AgentConfig
    tools: list[Tool]
    workdir: str
    # Callbacks are optional; pass an object implementing LoopCallbacks.
    callbacks: Any = field(default_factory=_NoCallbacks)
    budget: ContextBudget = field(default_factory=ContextBudget)
    # Injected so tests can pin run_id / dir; production uses uuid + ./traces.
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    tracer: Tracer | None = None

    def run(self, task: str) -> LoopResult:
        """Run the loop to completion for ``task``. Always returns (never raises
        from model/tool errors — they're captured into ``LoopResult.error``)."""
        tracer = self.tracer or Tracer(self.run_id, enabled=self.config.trace)
        ctx = ToolContext(workdir=self.workdir, run_id=self.run_id)
        tool_map = self._tool_map()

        # Seed the transcript with the task as the first user message.
        messages: list[Message] = [
            Message(role="user", content=[TextBlock(text=task)])
        ]
        tracer.event("task", text=task, tools=[t.name for t in self.tools])

        total_in = total_out = 0
        tool_defs = [self._tool_def(t) for t in self.tools]

        for it in range(1, self.config.max_iterations + 1):
            tracer.event("turn_start", iteration=it)

            # --- (A) context guard: compact if we're crowding the window ---
            if needs_compaction(messages, self.budget):
                messages = compact_history(messages, self.budget)
                tracer.event("context_compacted", tokens_after=None)

            # --- (B) ASK the model ---
            try:
                resp = self.client.ask(
                    system=self.config.system_prompt,
                    messages=messages,
                    tools=tool_defs,
                    config=self.config,
                )
            except Exception as e:
                log.exception("LLM call failed")
                tracer.event("error", phase="llm", message=str(e))
                return self._finish(
                    final_text="",
                    stop_reason="error",
                    iterations=it,
                    messages=messages,
                    tracer=tracer,
                    in_tok=total_in,
                    out_tok=total_out,
                    error=f"llm_error: {e}",
                )

            total_in += resp.input_tokens
            total_out += resp.output_tokens
            messages.append(resp.assistant)
            tracer.message("assistant", resp.assistant)
            _emit(self.callbacks, "on_iteration", it, resp)

            # Surface any text the model produced (its "reasoning") to the UI.
            for blk in resp.assistant.content:
                if isinstance(blk, TextBlock) and blk.text.strip():
                    _emit(self.callbacks, "on_assistant_text", blk.text)

            # --- (C) ACT? collect tool_use blocks ---
            tool_uses = [b for b in resp.assistant.content if isinstance(b, ToolUseBlock)]

            if not tool_uses:
                # No tools requested -> model is done reasoning. Finish.
                tracer.event("turn_end", iteration=it, stop=resp.stop_reason, tools=0)
                return self._finish(
                    final_text=resp.assistant.text(),
                    stop_reason=resp.stop_reason,
                    iterations=it,
                    messages=messages,
                    tracer=tracer,
                    in_tok=total_in,
                    out_tok=total_out,
                )

            # --- (D) OBSERVE: execute each requested tool, gather results ---
            result_blocks: list[ToolResultBlock] = []
            for call in tool_uses:
                tool = tool_map.get(call.name)
                if tool is None:
                    res = ToolResult(
                        output=f"Error: unknown tool '{call.name}'.",
                        is_error=True,
                    )
                else:
                    res = self._exec_tool(tool, call, ctx, tracer)
                _emit(self.callbacks, "on_tool_result", call.name, res)
                result_blocks.append(
                    ToolResultBlock(
                        tool_use_id=call.id,
                        content=res.output,
                        is_error=res.is_error,
                    )
                )

            # Tool results go back as a single user turn (Anthropic convention).
            messages.append(Message(role="user", content=result_blocks))
            tracer.event("turn_end", iteration=it, stop=resp.stop_reason, tools=len(tool_uses))

        # Exhausted iterations without a natural stop.
        tracer.event("error", phase="max_iterations", iterations=self.config.max_iterations)
        return self._finish(
            final_text=messages[-1].text() if messages else "",
            stop_reason="max_tokens",
            iterations=self.config.max_iterations,
            messages=messages,
            tracer=tracer,
            in_tok=total_in,
            out_tok=total_out,
            error="max_iterations_reached",
        )

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _exec_tool(
        self,
        tool: Tool,
        call: ToolUseBlock,
        ctx: ToolContext,
        tracer: Tracer,
    ) -> ToolResult:
        """Execute one tool call with full error containment + tracing."""
        _emit(self.callbacks, "on_tool_call", call.name, call.input)
        tracer.event("tool_call", name=call.name, input=call.input)
        try:
            res = tool.run(call.input, ctx)
        except Exception as e:
            log.exception("tool %s raised", call.name)
            res = ToolResult(output=f"Tool '{call.name}' crashed: {e}", is_error=True)
        tracer.event(
            "tool_call", name=call.name, phase="result", is_error=res.is_error, output=res.output
        )
        return res

    def _finish(
        self,
        *,
        final_text: str,
        stop_reason: str,
        iterations: int,
        messages: list[Message],
        tracer: Tracer,
        in_tok: int,
        out_tok: int,
        error: str | None = None,
    ) -> LoopResult:
        result = LoopResult(
            final_text=final_text,
            stop_reason=stop_reason,
            iterations=iterations,
            messages=messages,
            run_id=self.run_id,
            trace_path=tracer.path if tracer.enabled else None,
            total_input_tokens=in_tok,
            total_output_tokens=out_tok,
            error=error,
        )
        tracer.event("finish", ok=result.ok, iterations=iterations)
        _emit(self.callbacks, "on_finish", result)
        return result

    def _tool_map(self) -> dict[str, Tool]:
        seen: set[str] = set()
        out: dict[str, Tool] = {}
        for t in self.tools:
            if t.name in seen:
                raise ValueError(f"duplicate tool name: {t.name!r}")
            seen.add(t.name)
            out[t.name] = t
        return out

    @staticmethod
    def _tool_def(tool: Tool) -> dict[str, Any]:
        """Render a Tool into Anthropic's tool definition dict."""
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }


def _emit(target: Any, method: str, *args: Any) -> None:
    """Call ``target.method(*args)`` if it exists; swallow UI errors.

    Observing the loop must never change what the loop does, so callback
    failures are logged but never propagated.
    """
    fn = getattr(target, method, None)
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:
        log.warning("callback %s raised", method, exc_info=True)
