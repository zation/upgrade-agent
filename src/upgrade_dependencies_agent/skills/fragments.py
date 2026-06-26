"""Reusable prompt fragments shared across skills."""

from __future__ import annotations

BASELINE_RULE = (
    "Establish a green baseline before mutating files: read package.json, run the "
    "project test command, and record the exact passing/failing count. If the "
    "baseline is red, stop and report instead of changing files."
)

VERIFY_RULE = (
    "Verify with real command output: run the relevant tests after each meaningful "
    "change, read the actual output, compare it with the baseline, and do not "
    "claim success from exit code alone."
)

MINIMAL_CHANGE_RULE = (
    "Make the smallest targeted change that solves the current task; do not "
    "refactor unrelated code or batch unrelated fixes."
)

ONE_DEPENDENCY_RULE = (
    "Upgrade exactly one direct dependency at a time, verify immediately after "
    "that package, and only then move to the next package."
)

READ_ONLY_RULE = "Stay read-only: do not edit files, install packages, or run mutating commands."

SOURCE_EVIDENCE_RULE = (
    "Ground conclusions in sources actually read: package metadata, release notes, "
    "changelog or docs URLs, and project usage search."
)

TEST_STYLE_RULE = (
    "Follow the existing test style: naming, assertion library, import style, "
    "fixtures, setup helpers, and nearest appropriate test location."
)

BREAKING_CHANGE_RESEARCH_WORKFLOW = """\
1. Read package.json to confirm the current dependency version and scripts.
2. Use dependency_research for the target package to get latest version, \
major-version span, repository/homepage, and candidate changelog sources.
3. Use npm_releases to inspect recent versions and identify major boundaries.
4. Read release notes or changelog sources with fetch_releases/fetch_url. Focus \
on breaking changes, Node.js minimum version, ESM/CJS changes, peer dependency \
changes, CLI/config changes, and removed APIs.
5. Search the target project for actual usage of the dependency so the report \
distinguishes relevant project risks from generic upstream changes."""


def shared_contracts(*rules: str) -> str:
    """Render a standard shared-contract section for task prompts."""
    bullets = "\n".join(f"- {rule}" for rule in rules)
    return f"Shared contracts:\n{bullets}\n\n"
