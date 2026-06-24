"""The agent's tool belt.

A tool is anything implementing the ``Tool`` protocol from :mod:`core.types`.
We expose the concrete implementations here plus :func:`default_tools`, which
returns the standard read/inspect set for M1. Mutating tools (write/edit) and
shell are opt-in so a caller can start in a read-only "dry-run" mode.
"""

from __future__ import annotations

from ..core.types import Tool
from .fs import EditFile, Glob, Grep, ReadFile, WriteFile
from .git import GitDiff, GitStatus
from .npm import NpmOutdated, NpmReleases, NpmView
from .shell import RunCommand

__all__ = [
    "EditFile",
    "GitDiff",
    "GitStatus",
    "Glob",
    "Grep",
    "NpmOutdated",
    "NpmReleases",
    "NpmView",
    "ReadFile",
    "RunCommand",
    "WriteFile",
    "default_tools",
    "read_only_tools",
]


def read_only_tools() -> list[Tool]:
    """Read/inspect tools only — safe for a dry-run or analysis pass."""
    return [
        ReadFile(),
        Glob(),
        Grep(),
        GitStatus(),
        GitDiff(),
        NpmOutdated(),
        NpmView(),
        NpmReleases(),
    ]


def default_tools() -> list[Tool]:
    """The full M1 toolset: inspection + mutation + shell.

    This is what an upgrade task needs: it can read code, find usages, edit
    files, run npm/test, and check git state.
    """
    return [
        ReadFile(),
        Glob(),
        Grep(),
        WriteFile(),
        EditFile(),
        RunCommand(),
        GitStatus(),
        GitDiff(),
        NpmOutdated(),
        NpmView(),
        NpmReleases(),
    ]
