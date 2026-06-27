"""Tests for tools: fs, npm (offline parts), path safety."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from upgrade_dependencies_agent.core.types import ToolContext
from upgrade_dependencies_agent.tools._common import PathEscapeError, safe_resolve


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
    from upgrade_dependencies_agent.tools.fs import ReadFile

    res = ReadFile().run({"path": "src/a.js"}, ctx)
    assert not res.is_error
    assert "require('chai')" in res.output
    assert res.metadata["total_lines"] == 1


def test_read_file_missing(ctx):
    from upgrade_dependencies_agent.tools.fs import ReadFile

    res = ReadFile().run({"path": "nope.js"}, ctx)
    assert res.is_error


def test_read_file_escape_blocked(ctx):
    from upgrade_dependencies_agent.tools.fs import ReadFile

    res = ReadFile().run({"path": "../../../etc/passwd"}, ctx)
    assert res.is_error


# --- write_file + edit_file --- #


def test_write_then_edit(ctx):
    from upgrade_dependencies_agent.tools.fs import EditFile, WriteFile

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
    from upgrade_dependencies_agent.tools.fs import EditFile, WriteFile

    WriteFile().run({"path": "src/dup.js", "content": "AAA AAA"}, ctx)
    res = EditFile().run({"path": "src/dup.js", "old_text": "AAA", "new_text": "BBB"}, ctx)
    assert res.is_error  # two occurrences -> must refuse


# --- grep / glob --- #


def test_grep_finds_usage(ctx):
    from upgrade_dependencies_agent.tools.fs import Grep

    res = Grep().run({"pattern": "require"}, ctx)
    assert not res.is_error
    assert "a.js" in res.output


def test_glob_matches(ctx):
    from upgrade_dependencies_agent.tools.fs import Glob

    res = Glob().run({"pattern": "**/*.js"}, ctx)
    assert "src/a.js" in res.output


# --- dependency research --- #


def test_dependency_research_summarizes_registry_metadata(monkeypatch, ctx):
    from upgrade_dependencies_agent.tools import read_only_tools
    from upgrade_dependencies_agent.tools.npm import DependencyResearch

    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "name": "chai",
                "description": "BDD/TDD assertion library",
                "dist-tags": {"latest": "5.1.2"},
                "repository": {"url": "git+https://github.com/chaijs/chai.git"},
                "homepage": "https://www.chaijs.com",
                "versions": {
                    "4.0.0": {},
                    "4.5.0": {},
                    "5.0.0": {},
                    "5.1.2": {},
                },
            }

    def fake_get(url, timeout):
        assert url == "https://registry.npmjs.org/chai"
        assert timeout == 20
        return FakeResponse()

    monkeypatch.setattr("upgrade_dependencies_agent.tools.npm.httpx.get", fake_get)

    res = DependencyResearch().run({"name": "chai", "current": "^4.0.0"}, ctx)
    data = json.loads(res.output)

    assert not res.is_error
    assert data["latest"] == "5.1.2"
    assert data["target"] == "5.1.2"
    assert data["major_span"] == "4->5"
    assert "https://github.com/chaijs/chai/releases" in data["candidate_sources"]
    assert any("Major-version upgrade" in hint for hint in data["risk_hints"])
    assert "dependency_research" in {tool.name for tool in read_only_tools()}


# --- structured revert --- #


def test_revert_files_restores_only_requested_tracked_files(tmp_path):
    from upgrade_dependencies_agent.tools.git import RevertFiles

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "init",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "package.json").write_text('{"name":"old"}\n', encoding="utf-8")
    (tmp_path / "src.js").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "package.json", "src.js"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "files",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    (tmp_path / "package.json").write_text('{"name":"new"}\n', encoding="utf-8")
    (tmp_path / "src.js").write_text("new\n", encoding="utf-8")

    result = RevertFiles().run(
        {"paths": ["package.json"]},
        ToolContext(workdir=str(tmp_path), run_id="test"),
    )

    assert not result.is_error
    assert (tmp_path / "package.json").read_text(encoding="utf-8") == '{"name":"old"}\n'
    assert (tmp_path / "src.js").read_text(encoding="utf-8") == "new\n"


def test_revert_files_rejects_path_escape(ctx):
    from upgrade_dependencies_agent.tools.git import RevertFiles

    result = RevertFiles().run({"paths": ["../outside.js"]}, ctx)

    assert result.is_error
    assert "outside the project workdir" in result.output
