"""Full upgrade LangGraph backbone.

This runner owns workflow shape, not model behavior. Callers provide stage
functions that read and update ``UpgradeGraphState``; later milestones can swap
those functions for ReAct loops or structured-output nodes without changing the
graph topology.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from langgraph.graph import END, StateGraph

from .state import AgentReport, UpgradeGraphState, make_upgrade_graph_state

StateRunner = Callable[[UpgradeGraphState], UpgradeGraphState]


@dataclass(frozen=True)
class UpgradeBackboneResult:
    """Compact result returned by the full upgrade graph backbone."""

    ok: bool
    state: UpgradeGraphState
    report: AgentReport | None
    heal_attempts: int
    history: tuple[str, ...]


@dataclass
class UpgradeBackboneRunner:
    """Coordinate baseline → research → plan → execute → verify → report."""

    baseline: StateRunner
    research: StateRunner
    plan: StateRunner
    execute: StateRunner
    verify: StateRunner
    heal: StateRunner
    report: StateRunner
    max_heal_attempts: int = 1

    def run(self, task: str) -> UpgradeBackboneResult:
        """Run the full graph and return structured final state."""
        app = self._compile()
        state = app.invoke(
            make_upgrade_graph_state(
                task,
                max_heal_attempts=self.max_heal_attempts,
            )
        )
        report = state.get("report")
        return UpgradeBackboneResult(
            ok=bool(report and report.ok),
            state=state,
            report=report,
            heal_attempts=state.get("heal_attempts", 0),
            history=tuple(state.get("history", [])),
        )

    def _compile(self):
        graph = StateGraph(UpgradeGraphState)
        graph.add_node("baseline", self._baseline_node)
        graph.add_node("research", self._research_node)
        graph.add_node("plan", self._plan_node)
        graph.add_node("execute", self._execute_node)
        graph.add_node("verify", self._verify_node)
        graph.add_node("heal", self._heal_node)
        graph.add_node("report", self._report_node)
        graph.set_entry_point("baseline")
        graph.add_edge("baseline", "research")
        graph.add_edge("research", "plan")
        graph.add_edge("plan", "execute")
        graph.add_edge("execute", "verify")
        graph.add_conditional_edges(
            "verify",
            self._route_after_verify,
            {
                "heal": "heal",
                "report": "report",
            },
        )
        graph.add_edge("heal", "verify")
        graph.add_edge("report", END)
        return graph.compile()

    def _baseline_node(self, state: UpgradeGraphState) -> UpgradeGraphState:
        return self._stage(self.baseline, state, history_item="baseline", next_phase="research")

    def _research_node(self, state: UpgradeGraphState) -> UpgradeGraphState:
        return self._stage(self.research, state, history_item="research", next_phase="plan")

    def _plan_node(self, state: UpgradeGraphState) -> UpgradeGraphState:
        return self._stage(self.plan, state, history_item="plan", next_phase="execute")

    def _execute_node(self, state: UpgradeGraphState) -> UpgradeGraphState:
        return self._stage(self.execute, state, history_item="execute", next_phase="verify")

    def _verify_node(self, state: UpgradeGraphState) -> UpgradeGraphState:
        updated = self.verify(state)
        verified = bool(updated.get("verification") and updated["verification"].ok)
        history_item = "verify:ok" if verified else "verify:fail"
        return {
            **updated,
            "needs_heal": not verified,
            "phase": "report" if verified else "heal",
            "history": [*updated.get("history", []), history_item],
        }

    def _heal_node(self, state: UpgradeGraphState) -> UpgradeGraphState:
        attempts = state.get("heal_attempts", 0) + 1
        updated = self.heal(state)
        return {
            **updated,
            "heal_attempts": attempts,
            "phase": "verify",
            "history": [*updated.get("history", []), f"heal:{attempts}"],
        }

    def _report_node(self, state: UpgradeGraphState) -> UpgradeGraphState:
        return self._stage(self.report, state, history_item="report", next_phase="done")

    def _route_after_verify(self, state: UpgradeGraphState) -> str:
        if not state.get("needs_heal"):
            return "report"
        if state.get("heal_attempts", 0) >= state.get("max_heal_attempts", 0):
            return "report"
        return "heal"

    @staticmethod
    def _stage(
        runner: StateRunner,
        state: UpgradeGraphState,
        *,
        history_item: str,
        next_phase: str,
    ) -> UpgradeGraphState:
        updated = runner(state)
        return {
            **updated,
            "phase": next_phase,
            "history": [*updated.get("history", []), history_item],
        }
