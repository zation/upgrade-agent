"""Repository eval case fixture coverage."""

from __future__ import annotations

from pathlib import Path

from evals.runner import load_case


def test_core_workflow_eval_fixtures_exist_and_load() -> None:
    expected = {
        "chai-like-mocha-upgrade.json": "upgrade",
        "chai-like-upgrade-all.json": "upgrade-all",
        "chai-like-research-upgrade.json": "research-upgrade",
        "chai-like-generate-tests.json": "generate-tests",
    }

    for filename, command_name in expected.items():
        case = load_case(Path("evals/cases") / filename)
        command = case.command if isinstance(case.command, list) else [case.command]

        assert command_name in command
        assert case.checks
