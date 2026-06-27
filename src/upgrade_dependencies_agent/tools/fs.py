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
import json
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
            stat = full.stat()
        except OSError as e:
            return ToolResult(output=f"Failed to stat {path}: {e}", is_error=True)
        read_cache = ctx.scratch.setdefault("read_file_cache", {})
        cache_key = (str(full), int(offset), int(limit), stat.st_mtime_ns, stat.st_size)
        if cache_key in read_cache:
            cached = read_cache[cache_key]
            return ToolResult(
                output=(
                    f"Read cache hit for {path} lines {cached['start']}-{cached['end']}. "
                    "This exact slice was already returned earlier in this run; use a "
                    "different offset or limit if you need other content."
                ),
                metadata={
                    "path": str(full),
                    "total_lines": cached["total_lines"],
                    "shown": 0,
                    "cache_hit": True,
                },
            )
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult(output=f"Failed to read {path}: {e}", is_error=True)

        if _should_summarize_large_file(path, args):
            summary = _summarize_large_file(path, text)
            if summary is not None:
                read_cache[cache_key] = {"start": 1, "end": 0, "total_lines": text.count("\n") + 1}
                return ToolResult(
                    output=summary,
                    metadata={
                        "path": str(full),
                        "total_lines": text.count("\n") + 1,
                        "shown": 0,
                        "summarized": True,
                    },
                )

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
        read_cache[cache_key] = {
            "start": start + 1,
            "end": end,
            "total_lines": len(lines),
        }
        return ToolResult(output=f"{body}{note}", metadata=meta)


def _should_summarize_large_file(path: str, args: dict[str, Any]) -> bool:
    if "offset" in args or "limit" in args:
        return False
    return Path(path).name in {"package-lock.json", "lcov.info"}


def _summarize_large_file(path: str, text: str) -> str | None:
    if Path(path).name != "package-lock.json":
        if Path(path).name == "lcov.info":
            return _summarize_lcov(text)
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    packages = data.get("packages")
    dependencies = data.get("dependencies")
    package_count = len(packages) if isinstance(packages, dict) else 0
    dependency_count = len(dependencies) if isinstance(dependencies, dict) else 0
    root_deps: dict[str, Any] = {}
    if isinstance(packages, dict) and isinstance(packages.get(""), dict):
        root = packages[""]
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            values = root.get(section)
            if isinstance(values, dict):
                root_deps.update(values)
    root_names = ", ".join(sorted(root_deps)[:20]) or "(none found)"
    return (
        "package-lock.json summary\n"
        f"- name: {data.get('name', '(unknown)')}\n"
        f"- lockfileVersion: {data.get('lockfileVersion', '(unknown)')}\n"
        f"- packages: {package_count}\n"
        f"- top-level dependencies: {len(root_deps) or dependency_count}\n"
        f"- top-level names: {root_names}\n"
        "Use read_file with explicit offset and limit to inspect raw lockfile lines."
    )


def _summarize_lcov(text: str) -> str:
    files: list[tuple[str, int, int]] = []
    current_file: str | None = None
    current_found = 0
    current_hit = 0
    for line in text.splitlines():
        if line.startswith("SF:"):
            current_file = line[3:]
            current_found = 0
            current_hit = 0
        elif line.startswith("LF:"):
            current_found = _int_or_zero(line[3:])
        elif line.startswith("LH:"):
            current_hit = _int_or_zero(line[3:])
        elif line == "end_of_record" and current_file is not None:
            files.append((current_file, current_hit, current_found))
            current_file = None
    total_hit = sum(hit for _, hit, _ in files)
    total_found = sum(found for _, _, found in files)
    weakest = sorted(files, key=lambda item: (item[1] / item[2]) if item[2] else 1)[:10]
    weak_lines = "\n".join(f"- {name}: {hit}/{found}" for name, hit, found in weakest)
    percent = (total_hit / total_found * 100) if total_found else 0.0
    return (
        "lcov.info summary\n"
        f"- files: {len(files)}\n"
        f"- line coverage: {total_hit}/{total_found} ({percent:.1f}%)\n"
        "Lowest covered files:\n"
        f"{weak_lines or '- (none)'}\n"
        "Use read_file with explicit offset and limit to inspect raw coverage lines."
    )


def _int_or_zero(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


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
