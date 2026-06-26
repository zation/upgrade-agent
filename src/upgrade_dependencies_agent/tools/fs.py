"""Filesystem tools: read, write, edit, glob, grep.

These give the agent the same primitives a human refactorer uses. All paths are
confined to ``workdir`` via :func:`upgrade_dependencies_agent.tools._common.safe_resolve`.

Design notes:
- ``read_file`` truncates very large files so a single read can't blow the
  context budget; the model learns the file is partial and can ask for an
  offset if needed.
- ``edit_file`` does an exact unique-string replace (like a human editor's
  "find & replace, fail if ambiguous"). This is more reliable than asking the
  model to rewrite whole files and less error-prone than regex.
- ``grep`` wraps Python's :mod:`re` for content search (no ripgrep dependency).
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from ..core.types import ToolImpl, ToolResult
from ._common import PathEscapeError, safe_resolve

__all__ = ["EditFile", "Glob", "Grep", "ReadFile", "WriteFile"]

# Cap a single read at ~2000 lines so one tool call can't eat the context.
_READ_MAX_LINES = 2000


class ReadFile(ToolImpl):
    name = "read_file"
    description = (
        "Read a text file from the project. Returns the file contents (up to "
        f"{_READ_MAX_LINES} lines). Use `offset`/`limit` to page through long files. "
        "Paths are relative to the project root."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to project root."},
            "offset": {
                "type": "integer",
                "description": "1-based line number to start reading from. Default 1.",
            },
            "limit": {
                "type": "integer",
                "description": f"Max lines to return. Default {_READ_MAX_LINES}.",
            },
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        path = args["path"]
        offset = args.get("offset", 1)
        limit = args.get("limit", _READ_MAX_LINES)
        try:
            full = safe_resolve(ctx.workdir, path)
        except PathEscapeError as e:
            return ToolResult(output=str(e), is_error=True)
        if not full.exists():
            return ToolResult(output=f"File not found: {path}", is_error=True)
        if full.is_dir():
            return ToolResult(output=f"Path is a directory, not a file: {path}", is_error=True)
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult(output=f"Failed to read {path}: {e}", is_error=True)

        lines = text.splitlines()
        start = max(1, offset) - 1
        end = min(len(lines), start + limit)
        slice_lines = lines[start:end]
        # Prefix with line numbers so the model can cite exact locations.
        numbered = [f"{i + start + 1:>6}\t{ln}" for i, ln in enumerate(slice_lines)]
        body = "\n".join(numbered)
        note = ""
        if end < len(lines):
            note = f"\n\n({len(lines) - end} more lines; pass a larger offset to continue)"
        meta = {"path": str(full), "total_lines": len(lines), "shown": end - start}
        return ToolResult(output=f"{body}{note}", metadata=meta)


class WriteFile(ToolImpl):
    name = "write_file"
    description = (
        "Create or overwrite a file in the project with the given text content. "
        "Creates parent directories as needed. Use for new files or full rewrites."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "The full new contents of the file."},
        },
        "required": ["path", "content"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        path = args["path"]
        content = args["content"]
        try:
            full = safe_resolve(ctx.workdir, path)
        except PathEscapeError as e:
            return ToolResult(output=str(e), is_error=True)
        full.parent.mkdir(parents=True, exist_ok=True)
        existed = full.exists()
        full.write_text(content, encoding="utf-8")
        action = "overwritten" if existed else "created"
        return ToolResult(
            output=f"Wrote {len(content)} bytes; file {action}: {path}",
            metadata={"path": str(full), "bytes": len(content), "existed": existed},
        )


class EditFile(ToolImpl):
    name = "edit_file"
    description = (
        "Replace exactly one occurrence of `old_text` with `new_text` in a file. "
        "Fails if `old_text` is not found or appears more than once — this prevents "
        "ambiguous edits. Use read_file first to get the exact string."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string", "description": "Exact text to find (must be unique)."},
            "new_text": {"type": "string", "description": "Text to replace it with."},
        },
        "required": ["path", "old_text", "new_text"],
    }

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        path = args["path"]
        old_text = args["old_text"]
        new_text = args["new_text"]
        try:
            full = safe_resolve(ctx.workdir, path)
        except PathEscapeError as e:
            return ToolResult(output=str(e), is_error=True)
        if not full.exists():
            return ToolResult(output=f"File not found: {path}", is_error=True)

        text = full.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_text)
        if count == 0:
            return ToolResult(output=f"old_text not found in {path}. No edit made.", is_error=True)
        if count > 1:
            return ToolResult(
                output=(
                    f"old_text appears {count} times in {path}; it must be unique. "
                    "Include more surrounding context to disambiguate."
                ),
                is_error=True,
            )
        new_full = text.replace(old_text, new_text, 1)
        full.write_text(new_full, encoding="utf-8")
        return ToolResult(
            output=f"Edited {path}: 1 replacement applied.",
            metadata={"path": str(full)},
        )


class Glob(ToolImpl):
    name = "glob"
    description = (
        "List files in the project matching a glob pattern (e.g. '**/*.js', "
        "'src/**/*.ts'). Returns matching paths relative to the project root. "
        "Respects a built-in ignore list (node_modules, .git, etc.)."
    )
    input_schema = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"],
    }

    # Common noise we never want surfaced to the model.
    _IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        pattern = args["pattern"]
        root = Path(ctx.workdir).resolve()
        matches: list[str] = []
        for p in root.rglob("*"):
            if any(part in self._IGNORE_DIRS for part in p.parts):
                continue
            rel = p.relative_to(root).as_posix()
            if p.is_file() and fnmatch.fnmatch(rel, pattern):
                matches.append(rel)
        matches.sort()
        if not matches:
            return ToolResult(output=f"No files matched pattern '{pattern}'.")
        cap = 200
        body = "\n".join(matches[:cap])
        note = f"\n({len(matches) - cap} more truncated)" if len(matches) > cap else ""
        return ToolResult(output=f"{body}{note}", metadata={"count": len(matches)})


class Grep(ToolImpl):
    name = "grep"
    description = (
        "Search file contents in the project for a regex. Returns matching lines "
        "with file:line prefixes. Use this to find usages (e.g. where `require('chai')` "
        "appears) before editing."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regex."},
            "glob": {
                "type": "string",
                "description": "Optional file-pattern filter, e.g. '*.js'. Default all files.",
            },
        },
        "required": ["pattern"],
    }

    _IGNORE_DIRS = Glob._IGNORE_DIRS

    def run(self, args: dict[str, Any], ctx) -> ToolResult:  # type: ignore[override]
        pattern = args["pattern"]
        glob_pat = args.get("glob")
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(output=f"Invalid regex: {e}", is_error=True)
        root = Path(ctx.workdir).resolve()
        out: list[str] = []
        for p in root.rglob("*"):
            if any(part in self._IGNORE_DIRS for part in p.parts):
                continue
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if glob_pat and not fnmatch.fnmatch(rel, glob_pat):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    out.append(f"{rel}:{i}: {line}")
            if len(out) >= 200:
                break
        if not out:
            return ToolResult(output=f"No matches for /{pattern}/.")
        truncated = " (truncated at 200 matches)" if len(out) >= 200 else ""
        return ToolResult(output="\n".join(out) + truncated, metadata={"count": len(out)})
