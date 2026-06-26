"""LangGraph orchestration layer.

This package composes the hand-written ReAct loop into higher-level workflows.
It must not replace or leak into ``core/``; the loop remains the learning
artifact and the graph only coordinates when to execute, verify, and self-heal.
"""

from __future__ import annotations

from .state import (
    AgentReport,
    BaselineState,
    ResearchBrief,
    UpgradeGraphState,
    UpgradePlan,
    VerificationResult,
    make_upgrade_graph_state,
)
from .upgrade_graph import GraphRunResult, UpgradeGraphRunner

__all__ = [
    "AgentReport",
    "BaselineState",
    "GraphRunResult",
    "ResearchBrief",
    "UpgradeGraphRunner",
    "UpgradeGraphState",
    "UpgradePlan",
    "VerificationResult",
    "make_upgrade_graph_state",
]
