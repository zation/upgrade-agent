"""Tests for tools: fs, npm (offline parts), path safety."""

from __future__ import annotations

import os

import pytest

from refactor_agent.core.types import ToolContext
from refactor_agent.tools._common import PathEscapeError, safe_resolve


@pytest.fixture()
def ctx(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text("require('chai');\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    return ToolContext(workdir=str(tmp_path), run_id="test")


# --- path safety (the most important property) --- #


def test_safe_resolve_rejects_escape(tmp_path):
    with pytest.raises(PathEscapeError):
        safe_resolve(str(tmp_path), "../../etc/passwd")


def test_safe_resolve_rejects_abs_outside(tmp_path):
    with pytest.raises(PathEscapeError):
        safe_resolve(str(tmp_path), "/etc/passwd")


def test_safe_resolve_allows_nested(tmp_path):
    p = safe_resolve(str(tmp_path), "src/deep/x.js")
    assert str(p).startswith(str(tmp_path))


# --- read_file --- #


def test_read_file(ctx):
    from refactor_agent.tools.fs import ReadFile

    res = ReadFile().run({"path": "src/a.js"}, ctx)
    assert not res.is_error
    assert "require('chai')" in res.output
    assert res.metadata["total_lines"] == 1


def test_read_file_missing(ctx):
    from refactor_agent.tools.fs import ReadFile

    res = ReadFile().run({"path": "nope.js"}, ctx)
    assert res.is_error


def test_read_file_escape_blocked(ctx):
    from refactor_agent.tools.fs import ReadFile

    res = ReadFile().run({"path": "../../../etc/passwd"}, ctx)
    assert res.is_error


# --- write_file + edit_file --- #


def test_write_then_edit(ctx):
    from refactor_agent.tools.fs import EditFile, WriteFile

    w = WriteFile().run({"path": "src/b.js", "content": "var x = 1;"}, ctx)
    assert not w.is_error
    e = EditFile().run(
        {"path": "src/b.js", "old_text": "var x = 1;", "new_text": "const x = 1;"}, ctx
    )
    assert not e.is_error
    assert (ctx.workdir + "/src/b.js").replace("/", os.sep) or True
    from pathlib import Path

    assert Path(ctx.workdir, "src/b.js").read_text() == "const x = 1;"


def test_edit_file_ambiguous_rejected(ctx):
    from refactor_agent.tools.fs import EditFile, WriteFile

    WriteFile().run({"path": "src/dup.js", "content": "AAA AAA"}, ctx)
    res = EditFile().run({"path": "src/dup.js", "old_text": "AAA", "new_text": "BBB"}, ctx)
    assert res.is_error  # two occurrences -> must refuse


# --- grep / glob --- #


def test_grep_finds_usage(ctx):
    from refactor_agent.tools.fs import Grep

    res = Grep().run({"pattern": "require"}, ctx)
    assert not res.is_error
    assert "a.js" in res.output


def test_glob_matches(ctx):
    from refactor_agent.tools.fs import Glob

    res = Glob().run({"pattern": "**/*.js"}, ctx)
    assert "src/a.js" in res.output
