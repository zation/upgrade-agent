"""Shared state and structured artifacts for upgrade LangGraph workflows."""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field

from ..core import LoopResult

GraphPhase = Literal[
    "baseline",
    "research",
    "plan",
    "execute",
    "verify",
    "heal",
    "report",
    "done",
]


class BaselineState(BaseModel):
    """Facts learned from the pre-mutation baseline run."""

    ran: bool = False
    green: bool = False
    command: str | None = None
    summary: str | None = None


class ResearchBrief(BaseModel):
    """Structured dependency research that can feed an upgrade plan."""

    package: str
    current_version: str | None = None
    target_version: str | None = None
    sources: list[str] = Field(default_factory=list)
    relevant_risks: list[str] = Field(default_factory=list)


class UpgradePlan(BaseModel):
    """Minimal executable plan for one dependency upgrade."""

    dependency: str
    target_version: str | None = None
    steps: list[str] = Field(default_factory=list)
    allowed_files: list[str] = Field(default_factory=list)


class VerificationResult(BaseModel):
    """Structured verification result for a command or graph stage."""

    ok: bool
    command: str | None = None
    summary: str
    passing_count: int | None = None


class AgentReport(BaseModel):
    """Final graph report that can later back JSON CLI output."""

    ok: bool
    summary: str
    changed_files: list[str] = Field(default_factory=list)
    remaining_risks: list[str] = Field(default_factory=list)


class UpgradeGraphState(TypedDict, total=False):
    """Mutable state carried through upgrade graph workflows."""

    task: str
    phase: GraphPhase
    baseline: BaselineState
    research: ResearchBrief | None
    plan: UpgradePlan | None
    verification: VerificationResult | None
    report: AgentReport | None
    current_dependency: str | None
    changed_files: list[str]
    execute_result: LoopResult | None
    verify_result: LoopResult | None
    heal_result: LoopResult | None
    final_result: LoopResult | None
    heal_attempts: int
    max_heal_attempts: int
    needs_heal: bool
    history: list[str]


def make_upgrade_graph_state(
    task: str,
    *,
    max_heal_attempts: int,
    phase: GraphPhase = "baseline",
) -> UpgradeGraphState:
    """Create a complete initial state for upgrade graph runs."""
    return {
        "task": task,
        "phase": phase,
        "baseline": BaselineState(),
        "research": None,
        "plan": None,
        "verification": None,
        "report": None,
        "current_dependency": None,
        "changed_files": [],
        "execute_result": None,
        "verify_result": None,
        "heal_result": None,
        "final_result": None,
        "heal_attempts": 0,
        "max_heal_attempts": max_heal_attempts,
        "needs_heal": False,
        "history": [],
    }
