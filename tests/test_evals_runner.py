"""Tests for the deterministic eval runner."""

from __future__ import annotations

import json
from pathlib import Path

from evals.runner import load_case, main, run_case, run_cases


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
    assert result.failure_reason == "postcondition_failed"
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
    assert result.failure_reason == "timeout"
    assert result.command_exit_code == 124
    assert result.checks[0].name == "case_command"
    assert "timed out after 1s" in result.checks[0].message


def test_run_cases_summarizes_multiple_results(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json", mocha="^10.0.0")

    passing_case = tmp_path / "pass.json"
    passing_case.write_text(
        json.dumps(
            {
                "name": "pass",
                "target": str(target),
                "command": (
                    'python -c "import json, pathlib; '
                    "p=pathlib.Path('package.json'); "
                    "d=json.loads(p.read_text()); "
                    "d['devDependencies']['mocha']='^11.0.0'; "
                    "p.write_text(json.dumps(d, indent=2)+'\\\\n')\""
                ),
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
    failing_case = tmp_path / "fail.json"
    failing_case.write_text(
        json.dumps(
            {
                "name": "fail",
                "target": str(target),
                "command": "python -c \"print('unchanged')\"",
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

    result = run_cases(
        [load_case(passing_case), load_case(failing_case)],
        workspace=tmp_path / "work",
    )

    assert not result.ok
    assert result.count == 2
    assert result.passed == 1
    assert result.failed == 1
    assert [case.case_name for case in result.results] == ["pass", "fail"]


def test_main_accepts_case_directory_and_prints_json_summary(tmp_path: Path, capsys) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    for name in ("a", "b"):
        (case_dir / f"{name}.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "target": str(target),
                    "command": "python -c \"print('ok')\"",
                    "checks": [],
                }
            ),
            encoding="utf-8",
        )

    exit_code = main([str(case_dir), "--workspace", str(tmp_path / "work")])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["ok"] is True
    assert output["count"] == 2
    assert [result["case_name"] for result in output["results"]] == ["a", "b"]


def test_trace_sequence_check_verifies_ordered_events(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "trace sequence",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('trace.jsonl').write_text("
                        '\'{"type":"tool_call",'
                        '"data":{"name":"run_command",'
                        '"input":{"command":"npm test"}}}\\n'
                        '{"type":"tool_call",'
                        '"data":{"name":"run_command",'
                        '"input":{"command":"npm install mocha@11"}}}\\n\')'
                    ),
                ],
                "checks": [
                    {
                        "type": "trace_sequence",
                        "path": "trace.jsonl",
                        "sequence": [
                            {
                                "event_type": "tool_call",
                                "tool": "run_command",
                                "command_contains": "npm test",
                            },
                            {
                                "event_type": "tool_call",
                                "tool": "run_command",
                                "command_contains": "npm install",
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert result.ok
    assert result.checks[0].name == "trace_sequence:trace.jsonl"


def test_trace_sequence_check_reports_missing_event(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "trace sequence failure",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('trace.jsonl').write_text("
                        '\'{"type":"tool_call",'
                        '"data":{"name":"run_command",'
                        '"input":{"command":"npm install mocha@11"}}}\\n\')'
                    ),
                ],
                "checks": [
                    {
                        "type": "trace_sequence",
                        "path": "trace.jsonl",
                        "sequence": [
                            {
                                "event_type": "tool_call",
                                "tool": "run_command",
                                "command_contains": "npm test",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert not result.ok
    assert result.failure_reason == "trajectory_violation"
    assert result.checks[0].name == "trace_sequence:trace.jsonl"
    assert "missing sequence item 1" in result.checks[0].message


def test_failure_reason_classifies_wrong_diff(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "wrong diff",
                "target": str(target),
                "command": (
                    "python -c \"from pathlib import Path; Path('extra.txt').write_text('x')\""
                ),
                "checks": [{"type": "git_diff", "allowed_paths": ["package.json"]}],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert not result.ok
    assert result.failure_reason == "wrong_diff"


def test_baseline_before_mutation_policy_passes_when_tests_run_first(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "baseline before mutation",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('trace.jsonl').write_text("
                        '\'{"type":"tool_call",'
                        '"data":{"name":"run_command",'
                        '"input":{"command":"npm test"}}}\\n'
                        '{"type":"tool_call",'
                        '"data":{"name":"run_command",'
                        '"input":{"command":"npm install mocha@11"}}}\\n\')'
                    ),
                ],
                "checks": [
                    {
                        "type": "trajectory_policy",
                        "policy": "baseline_before_mutation",
                        "path": "trace.jsonl",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert result.ok
    assert result.checks[0].name == "trajectory_policy:baseline_before_mutation"


def test_baseline_before_mutation_policy_reports_baseline_missing(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "baseline missing",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('trace.jsonl').write_text("
                        '\'{"type":"tool_call",'
                        '"data":{"name":"run_command",'
                        '"input":{"command":"npm install mocha@11"}}}\\n'
                        '{"type":"tool_call",'
                        '"data":{"name":"run_command",'
                        '"input":{"command":"npm test"}}}\\n\')'
                    ),
                ],
                "checks": [
                    {
                        "type": "trajectory_policy",
                        "policy": "baseline_before_mutation",
                        "path": "trace.jsonl",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert not result.ok
    assert result.failure_reason == "baseline_missing"
    assert result.checks[0].name == "trajectory_policy:baseline_before_mutation"
    assert "mutation before baseline" in result.checks[0].message


def test_single_dependency_policy_passes_for_one_install_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "single dependency",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('trace.jsonl').write_text("
                        '\'{"type":"tool_call",'
                        '"data":{"name":"run_command",'
                        '"input":{"command":"npm install mocha@11"}}}\\n\')'
                    ),
                ],
                "checks": [
                    {
                        "type": "trajectory_policy",
                        "policy": "single_dependency_at_a_time",
                        "path": "trace.jsonl",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert result.ok
    assert result.checks[0].name == "trajectory_policy:single_dependency_at_a_time"


def test_single_dependency_policy_fails_for_multiple_install_targets(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "multiple dependencies",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('trace.jsonl').write_text("
                        '\'{"type":"tool_call",'
                        '"data":{"name":"run_command",'
                        '"input":{"command":"npm install mocha@11 nyc@15"}}}\\n\')'
                    ),
                ],
                "checks": [
                    {
                        "type": "trajectory_policy",
                        "policy": "single_dependency_at_a_time",
                        "path": "trace.jsonl",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert not result.ok
    assert result.failure_reason == "multi_dependency_upgrade"
    assert result.checks[0].name == "trajectory_policy:single_dependency_at_a_time"
    assert "multiple install targets" in result.checks[0].message
