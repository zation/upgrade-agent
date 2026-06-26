"""LangGraph workflow for upgrade → verify → self-heal.

The graph intentionally treats :class:`upgrade_dependencies_agent.core.react_loop.ReActLoop`
as a black-box executor. This keeps ``core/`` model-agnostic and framework-free
while still demonstrating state-graph orchestration at the workflow layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph

from ..core import LoopResult

GraphPhase = Literal["execute", "verify", "heal", "done"]


class UpgradeGraphState(TypedDict, total=False):
    """Mutable state carried through the upgrade graph."""

    task: str
    execute_result: LoopResult | None
    verify_result: LoopResult | None
    heal_result: LoopResult | None
    final_result: LoopResult | None
    heal_attempts: int
    max_heal_attempts: int
    needs_heal: bool
    phase: GraphPhase
    history: list[str]


@dataclass(frozen=True)
class GraphRunResult:
    """Small public result for callers that do not need raw LangGraph state."""

    ok: bool
    final_result: LoopResult | None
    verify_result: LoopResult | None
    heal_attempts: int
    history: tuple[str, ...]


TaskRunner = Callable[[str], LoopResult]
VerifyDecision = Callable[[LoopResult], bool]


@dataclass
class UpgradeGraphRunner:
    """Coordinate an upgrade with a verify → self-heal edge.

    ``execute`` should perform the requested upgrade. ``verify`` should run an
    independent verification pass. ``heal`` receives a generated repair task if
    verification fails and may edit the project before the graph verifies again.
    """

    execute: TaskRunner
    verify: TaskRunner
    heal: TaskRunner
    max_heal_attempts: int = 1
    is_verified: VerifyDecision | None = None

    def run(self, task: str) -> GraphRunResult:
        """Run the compiled graph and return a compact result."""
        app = self._compile()
        state = app.invoke(
            {
                "task": task,
                "execute_result": None,
                "verify_result": None,
                "heal_result": None,
                "final_result": None,
                "heal_attempts": 0,
                "max_heal_attempts": self.max_heal_attempts,
                "needs_heal": False,
                "phase": "execute",
                "history": [],
            }
        )
        final_result = state.get("final_result")
        verify_result = state.get("verify_result")
        return GraphRunResult(
            ok=bool(final_result and final_result.ok and not state.get("needs_heal")),
            final_result=final_result,
            verify_result=verify_result,
            heal_attempts=state.get("heal_attempts", 0),
            history=tuple(state.get("history", [])),
        )

    def _compile(self):
        graph = StateGraph(UpgradeGraphState)
        graph.add_node("execute", self._execute_node)
        graph.add_node("verify", self._verify_node)
        graph.add_node("heal", self._heal_node)
        graph.set_entry_point("execute")
        graph.add_edge("execute", "verify")
        graph.add_conditional_edges(
            "verify",
            self._route_after_verify,
            {
                "heal": "heal",
                "done": END,
            },
        )
        graph.add_edge("heal", "verify")
        return graph.compile()

    def _execute_node(self, state: UpgradeGraphState) -> UpgradeGraphState:
        task = state["task"]
        result = self.execute(task)
        history = [*state.get("history", []), "execute"]
        return {
            **state,
            "execute_result": result,
            "final_result": result,
            "phase": "verify",
            "history": history,
        }

    def _verify_node(self, state: UpgradeGraphState) -> UpgradeGraphState:
        verify_task = self._verify_task(state)
        result = self.verify(verify_task)
        verified = self._is_verified(result)
        history = [*state.get("history", []), "verify:ok" if verified else "verify:fail"]
        return {
            **state,
            "verify_result": result,
            "final_result": result if verified else state.get("final_result"),
            "needs_heal": not verified,
            "phase": "done" if verified else "heal",
            "history": history,
        }

    def _heal_node(self, state: UpgradeGraphState) -> UpgradeGraphState:
        attempts = state.get("heal_attempts", 0) + 1
        result = self.heal(self._heal_task(state, attempts))
        history = [*state.get("history", []), f"heal:{attempts}"]
        return {
            **state,
            "heal_result": result,
            "final_result": result,
            "heal_attempts": attempts,
            "phase": "verify",
            "history": history,
        }

    def _route_after_verify(self, state: UpgradeGraphState) -> str:
        if not state.get("needs_heal"):
            return "done"
        if state.get("heal_attempts", 0) >= state.get("max_heal_attempts", 0):
            return "done"
        return "heal"

    def _is_verified(self, result: LoopResult) -> bool:
        if self.is_verified is not None:
            return self.is_verified(result)
        text = result.final_text.lower()
        if "verdict: pass" in text:
            return result.ok
        if "verdict: fail" in text:
            return False
        failure_markers = ("failing", "failed", "error", "red baseline", "cannot verify")
        return result.ok and not any(marker in text for marker in failure_markers)

    @staticmethod
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

    @staticmethod
    def _heal_task(state: UpgradeGraphState, attempt: int) -> str:
        verify_result = state.get("verify_result")
        failure = verify_result.final_text if verify_result else "(no verification result)"
        return (
            f"Self-heal attempt {attempt}: fix the failed dependency upgrade.\n\n"
            "Use the verification output below as the source of truth. Make the "
            "smallest targeted edit required, then run the tests and inspect git diff. "
            "If you cannot safely fix it, revert your own attempted fix and report "
            "the blocker.\n\n"
            f"Verification failure:\n{failure}"
        )
