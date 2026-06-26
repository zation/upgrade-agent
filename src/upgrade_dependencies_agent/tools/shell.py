"""Shell tool: run commands inside the target project.

This is the agent's most powerful and most dangerous capability — it can run
``npm install``, ``npm test``, ``npx tsc`` etc. We confine execution to the
project workdir and apply a timeout so a hanging test suite can't wedge the
whole run. Output (stdout+stderr) is captured and truncated to keep it within
the context budget.
"""

from __future__ import annotations

import subprocess
from typing import Any

from ..core.types import ToolImpl, ToolResult

__all__ = ["RunCommand"]

# A single command is capped so a runaway watch/serve process can't block forever.
_DEFAULT_TIMEOUT = 120  # seconds
# Cap captured output so one noisy command (e.g. npm install with 1000 deps)
# doesn't eat the whole context window.
_MAX_OUTPUT_CHARS = 8000


class RunCommand(ToolImpl):
    name = "run_command"
    description = (
        "Run a shell command inside the project directory. Use for builds, tests, "
        "package installs (e.g. `npm install chai@5`), etc. Returns combined "
        f"stdout+stderr (truncated to {_MAX_OUTPUT_CHARS} chars). Non-zero exit "
        "is reported but the result is NOT an error — test failures are data, "
        "use them. Prefer specific tools (read_file etc.) for simple inspection."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout": {
                "type": "integer",
                "description": f"Max seconds. Default {_DEFAULT_TIMEOUT}.",
            },
        },
        "required": ["command"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        command = args["command"]
        timeout = args.get("timeout", _DEFAULT_TIMEOUT)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=ctx.workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                output=f"Command timed out after {timeout}s: {command}", is_error=True
            )
        except OSError as e:
            return ToolResult(output=f"Failed to spawn command: {e}", is_error=True)

        out = (proc.stdout or "") + (proc.stderr or "")
        truncated = False
        if len(out) > _MAX_OUTPUT_CHARS:
            # Keep head + tail so both startup context and the final error are visible.
            keep = _MAX_OUTPUT_CHARS // 2
            omitted = len(out) - _MAX_OUTPUT_CHARS
            out = out[:keep] + f"\n\n... [truncated {omitted} chars] ...\n\n" + out[-keep:]
            truncated = True

        body = f"$ {command}\n[exit {proc.returncode}]\n{out}"
        note = "\n(output truncated)" if truncated else ""
        # Non-zero exit is reported in-band, NOT flagged as tool error: the model
        # needs to read failing test output as normal content to reason about it.
        return ToolResult(
            output=body + note,
            metadata={"exit_code": proc.returncode, "truncated": truncated},
        )
