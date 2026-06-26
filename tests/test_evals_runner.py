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


def test_run_case_supports_env_setup_teardown_and_timeout(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "schema extensions",
                "target": str(target),
                "timeout": 30,
                "env": {"TARGET_MOCHA": "^11.1.0"},
                "setup": [
                    (
                        'python -c "from pathlib import Path; '
                        "Path('setup.txt').write_text('prepared')\""
                    )
                ],
                "command": (
                    'python -c "import json, os, pathlib; '
                    "p=pathlib.Path('package.json'); "
                    "d=json.loads(p.read_text()); "
                    "d['devDependencies']['mocha']=os.environ['TARGET_MOCHA']; "
                    "p.write_text(json.dumps(d, indent=2)+'\\\\n')\""
                ),
                "teardown": [
                    (
                        'python -c "from pathlib import Path; '
                        "Path('teardown.txt').write_text('done')\""
                    )
                ],
                "budgets": {"max_iterations": 40, "max_input_tokens": 1_000_000},
                "checks": [
                    {
                        "type": "package_json_version",
                        "dependency": "mocha",
                        "section": "devDependencies",
                        "specifier": ">=11.0.0",
                    },
                    {
                        "type": "command",
                        "command": "python -c \"import os; print(os.environ['TARGET_MOCHA'])\"",
                    },
                    {"type": "git_diff", "allowed_paths": ["package.json"]},
                ],
            }
        ),
        encoding="utf-8",
    )

    case = load_case(case_path)
    result = run_case(case, workspace=tmp_path / "work")

    assert case.timeout == 30
    assert case.env == {"TARGET_MOCHA": "^11.1.0"}
    assert case.budgets == {"max_iterations": 40, "max_input_tokens": 1_000_000}
    assert result.ok
    assert result.teardown_exit_codes == [0]
    assert (result.workdir / "setup.txt").read_text() == "prepared"
    assert (result.workdir / "teardown.txt").read_text() == "done"


def test_run_case_timeout_is_reported_as_failure(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "timeout case",
                "target": str(target),
                "timeout": 1,
                "command": [
                    "python",
                    "-c",
                    "import time; time.sleep(5)",
                ],
                "checks": [],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert not result.ok
    assert result.command_exit_code == 124
    assert result.checks[0].name == "case_command"
    assert "timed out after 1s" in result.checks[0].message
