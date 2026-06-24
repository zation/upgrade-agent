"""Skills: domain-specific configurations of the agent.

A skill bundles a system prompt + a tool selection + the task framing, so the
generic ReActLoop can be pointed at a specific job ("analyze", "upgrade", …).
"""

from __future__ import annotations

from .prompts import ANALYZE, BASE_AGENT

__all__ = ["ANALYZE", "BASE_AGENT"]
