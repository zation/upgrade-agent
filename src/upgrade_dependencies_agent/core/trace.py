"""Structured JSONL trace writer.

Each agent run gets one ``traces/<run_id>.jsonl`` file. We append one JSON
object per line per event (turn start, tool call, tool result, turn end). This
is intentionally dependency-free and replayable — you can `cat` it, feed it to
`jq`, or later ship it to LangSmith.

Keeping trace concerns out of the ReAct loop itself: the loop calls
``trace.event(...)``; whether tracing is on or off is the only branching.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .types import Message, ToolResultBlock, ToolUseBlock

__all__ = ["TraceEvent", "Tracer"]

TRACES_DIR = Path("traces")


class TraceEvent:
    """One line in the trace file. Rendered as a single JSON object."""

    __slots__ = ("data", "ts", "type")

    def __init__(self, type_: str, data: dict[str, Any]) -> None:
        self.ts = time.time()
        self.type = type_
        self.data = data

    def to_line(self) -> str:
        return json.dumps(
            {"ts": self.ts, "type": self.type, "data": self.data},
            ensure_ascii=False,
            default=str,
        )


class Tracer:
    """Append-only tracer for a single run.

    Call :meth:`event` from anywhere; it no-ops cleanly when ``enabled`` is
    False so call sites never need to guard.
    """

    def __init__(self, run_id: str, enabled: bool = True, traces_dir: Path | None = None) -> None:
        self.run_id = run_id
        self.enabled = enabled
        self._dir = traces_dir or TRACES_DIR
        self._path = self._dir / f"{run_id}.jsonl"
        self._events: list[TraceEvent] = []
        if enabled:
            self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def event(self, type_: str, **data: Any) -> None:
        if not self.enabled:
            return
        ev = TraceEvent(type_, data)
        self._events.append(ev)
        # Append immediately so a crash mid-run still leaves a usable trace.
        with self._path.open("a", encoding="utf-8") as f:
            f.write(ev.to_line() + "\n")

    # ---- convenience helpers that serialize our typed messages ---- #

    def message(self, type_: str, msg: Message) -> None:
        """Serialize a :class:`Message` into a trace event."""
        blocks = []
        for blk in msg.content:
            if isinstance(blk, ToolUseBlock):
                blocks.append(
                    {"type": "tool_use", "id": blk.id, "name": blk.name, "input": blk.input}
                )
            elif isinstance(blk, ToolResultBlock):
                blocks.append(
                    {"type": "tool_result", "tool_use_id": blk.tool_use_id, "content": blk.content}
                )
            else:
                blocks.append(blk.model_dump())
        self.event(type_, role=msg.role, blocks=blocks)

    def flush(self) -> None:
        """No-op now (we append eagerly); kept for API symmetry / future batching."""
        if not self.enabled:
            return

    def summary(self) -> dict[str, Any]:
        """Return a compact dict summarising the run for the CLI/report."""
        tool_calls = sum(
            1 for e in self._events if e.type == "tool_call" and e.data.get("phase") != "result"
        )
        turns = sum(1 for e in self._events if e.type == "turn_end")
        input_tokens = sum(
            int(e.data.get("input_tokens") or 0) for e in self._events if e.type == "llm_usage"
        )
        output_tokens = sum(
            int(e.data.get("output_tokens") or 0) for e in self._events if e.type == "llm_usage"
        )
        compaction_count = sum(1 for e in self._events if e.type == "context_compacted")
        return {
            "run_id": self.run_id,
            "events": len(self._events),
            "tool_calls": tool_calls,
            "turns": turns,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "compaction_count": compaction_count,
        }
