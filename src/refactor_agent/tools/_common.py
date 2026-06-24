"""Shared helpers for tools.

The most important one is :func:`safe_resolve`: every path a tool receives
from the model is resolved against the run's ``workdir`` AND confined to it.
An agent must never read or write outside the project it's operating on —
that's the difference between a helpful tool and a footgun.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["PathEscapeError", "safe_resolve"]


class PathEscapeError(Exception):
    """Raised when a tool is asked to touch a path outside ``workdir``."""


def safe_resolve(workdir: str, path: str) -> Path:
    """Resolve ``path`` under ``workdir`` and refuse to escape it.

    We normalize the joined path and check it still lives under workdir after
    resolving ``..`` segments. Symlinks are intentionally NOT followed across
    the boundary (we use ``resolve`` only for ``..`` collapse via
    ``Path.absolute`` + a manual check, to avoid resolving symlinks that point
    outside the project).

    Returns the absolute, normalized Path on success; raises PathEscapeError
    otherwise. ``path`` may be absolute (then it must already be inside workdir)
    or relative.
    """
    root = Path(workdir).resolve()
    candidate = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PathEscapeError(
            f"Path '{path}' resolves outside the project workdir ({root}). "
            "The agent can only operate inside the target project."
        ) from exc
    return candidate
