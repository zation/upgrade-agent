"""Tests for the deterministic eval runner."""

from __future__ import annotations

import json
from pathlib import Path

from evals.runner import load_case, run_case


def _write_package(path: Path, mocha: str = "^4.0.0") -> None:
    path.write_text(
        json.dumps(
            {
                "scripts": {"test": "python -c 'print(28)'"},
                "devDependencies": {"mocha": mocha},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_run_case_copies_target_and_checks_success(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "fixture mocha upgrade",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "import json, pathlib; "
                        "p=pathlib.Path('package.json'); "
                        "d=json.loads(p.read_text()); "
                        "d['devDependencies']['mocha']='^11.0.0'; "
                        "p.write_text(json.dumps(d, indent=2)+'\\n')"
                    ),
                ],
                "checks": [
                    {
                        "type": "package_json_version",
                        "dependency": "mocha",
                        "section": "devDependencies",
                        "specifier": ">=11.0.0",
                    },
                    {"type": "command", "command": "python -c \"print('28 passing')\""},
                    {"type": "git_diff", "allowed_paths": ["package.json"]},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert result.ok
    assert result.case_name == "fixture mocha upgrade"
    assert result.workdir != target
    assert [check.ok for check in result.checks] == [True, True, True]
    assert json.loads((target / "package.json").read_text())["devDependencies"]["mocha"] == "^4.0.0"


def test_run_case_reports_failing_check(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json", mocha="^10.0.0")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "failed version check",
                "target": str(target),
                "command": ["python", "-c", "print('no change')"],
                "checks": [
                    {
                        "type": "package_json_version",
                        "dependency": "mocha",
                        "section": "devDependencies",
                        "specifier": ">=11.0.0",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert not result.ok
    assert result.checks[0].name == "package_json_version:mocha"
    assert "expected >=11.0.0" in result.checks[0].message
