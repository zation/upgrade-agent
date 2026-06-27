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


def test_read_file_cache_avoids_repeating_same_slice(ctx):
    from upgrade_dependencies_agent.tools.fs import ReadFile

    tool = ReadFile()
    first = tool.run({"path": "src/a.js"}, ctx)
    second = tool.run({"path": "src/a.js"}, ctx)

    assert not first.is_error
    assert not second.is_error
    assert "require('chai')" in first.output
    assert "require('chai')" not in second.output
    assert second.metadata["cache_hit"] is True


def test_read_file_summarizes_package_lock_by_default(ctx):
    from upgrade_dependencies_agent.tools.fs import ReadFile

    lock = {
        "name": "fixture",
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"left-pad": "^1.3.0"}},
            "node_modules/left-pad": {"version": "1.3.0"},
            "node_modules/mocha": {"version": "11.0.0"},
        },
    }
    from pathlib import Path

    Path(ctx.workdir, "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")

    res = ReadFile().run({"path": "package-lock.json"}, ctx)

    assert not res.is_error
    assert "package-lock.json summary" in res.output
    assert "lockfileVersion: 3" in res.output
    assert "packages: 3" in res.output
    assert "node_modules/left-pad" not in res.output


def test_read_file_summarizes_lcov_by_default(ctx):
    from pathlib import Path

    from upgrade_dependencies_agent.tools.fs import ReadFile

    coverage = "\n".join(
        [
            "TN:",
            "SF:src/a.js",
            "DA:1,1",
            "DA:2,0",
            "LF:2",
            "LH:1",
            "end_of_record",
            "SF:src/b.js",
            "DA:1,1",
            "LF:1",
            "LH:1",
            "end_of_record",
        ]
    )
    Path(ctx.workdir, "coverage").mkdir()
    Path(ctx.workdir, "coverage/lcov.info").write_text(coverage, encoding="utf-8")

    res = ReadFile().run({"path": "coverage/lcov.info"}, ctx)

    assert not res.is_error
    assert "lcov.info summary" in res.output
    assert "files: 2" in res.output
    assert "line coverage: 2/3" in res.output
    assert "src/a.js: 1/2" in res.output


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


def test_run_command_summarizes_noisy_npm_test_output(monkeypatch, ctx):
    from upgrade_dependencies_agent.tools.shell import RunCommand

    class FakeProcess:
        returncode = 1
        stdout = "noise\n" * 3000 + "  28 passing\n  1 failing\n"
        stderr = "AssertionError: expected true\n" + "stack tail\n" * 50

    def fake_run(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr("upgrade_dependencies_agent.tools.shell.subprocess.run", fake_run)

    res = RunCommand().run({"command": "npm test"}, ctx)

    assert not res.is_error
    assert "[exit 1]" in res.output
    assert "28 passing" in res.output
    assert "1 failing" in res.output
    assert "AssertionError" in res.output
    assert len(res.output) < 2500


def test_fetch_url_summarizes_long_changelog(monkeypatch, ctx):
    from upgrade_dependencies_agent.tools.changelog import FetchUrl

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "text/markdown"}
            self.content = b"x"
            self.text = (
                "# Changelog\n\n"
                + "\n".join(f"## {i}.0.0\nRegular notes" for i in range(80))
                + "\n## 5.0.0\nBreaking: removed CommonJS support\n"
            )

    def fake_get(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("upgrade_dependencies_agent.tools.changelog.httpx.get", fake_get)

    res = FetchUrl().run({"url": "https://example.test/CHANGELOG.md"}, ctx)

    assert not res.is_error
    assert "long changelog summary" in res.output
    assert "Breaking: removed CommonJS support" in res.output
    assert len(res.output) < 4000


def test_retrieve_source_chunks_returns_keyword_ranked_chunks(monkeypatch, ctx):
    from upgrade_dependencies_agent.tools.changelog import _FETCH_CACHE, RetrieveSourceChunks

    _FETCH_CACHE.clear()

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "text/markdown"}
            self.content = b"x"
            self.text = """# Changelog

## 6.0.0
Breaking: removed CommonJS support.
Node minimum is now 18.

## 5.1.0
Fix install output.

## 5.0.0
Deprecated legacy CLI config.
"""
            self.url = "https://example.test/CHANGELOG.md"

    def fake_get(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("upgrade_dependencies_agent.tools.changelog.httpx.get", fake_get)

    res = RetrieveSourceChunks().run(
        {
            "url": "https://example.test/CHANGELOG.md",
            "keywords": ["breaking", "node minimum", "deprecated"],
        },
        ctx,
    )
    data = json.loads(res.output)

    assert not res.is_error
    assert data["source"] == "https://example.test/CHANGELOG.md"
    assert data["chunks"][0]["heading"] == "6.0.0"
    assert "breaking" in data["chunks"][0]["matched_keywords"]
    assert "node minimum" in data["chunks"][0]["matched_keywords"]
    assert data["chunks"][1]["heading"] == "5.0.0"


def test_fetch_url_and_retrieve_source_chunks_share_success_cache(monkeypatch, ctx):
    from upgrade_dependencies_agent.tools.changelog import (
        _FETCH_CACHE,
        FetchUrl,
        RetrieveSourceChunks,
    )

    _FETCH_CACHE.clear()

    calls = 0

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-type": "text/markdown"}
            self.content = b"x"
            self.text = "# Changelog\n\n## 2.0.0\nBreaking: removed old API.\n"
            self.url = "https://example.test/CHANGELOG.md"

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    monkeypatch.setattr("upgrade_dependencies_agent.tools.changelog.httpx.get", fake_get)

    fetched = FetchUrl().run({"url": "https://example.test/CHANGELOG.md"}, ctx)
    retrieved = RetrieveSourceChunks().run({"url": "https://example.test/CHANGELOG.md"}, ctx)

    assert not fetched.is_error
    assert not retrieved.is_error
    assert calls == 1
    assert retrieved.metadata["cache_hit"] is True


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
