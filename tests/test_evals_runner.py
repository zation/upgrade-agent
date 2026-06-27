"""Tests for the deterministic eval runner."""

from __future__ import annotations

import json
from pathlib import Path

from evals.runner import load_case, main, result_to_dict, run_case, run_cases


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


def test_run_case_reports_cost_metrics_from_trace_and_wall_time(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")
    trace = "\n".join(
        json.dumps(event)
        for event in [
            {"type": "llm_usage", "data": {"input_tokens": 120, "output_tokens": 30}},
            {"type": "tool_call", "data": {"name": "read_file", "input": {}}},
            {
                "type": "tool_call",
                "data": {"name": "read_file", "phase": "result", "is_error": False},
            },
            {"type": "context_compacted", "data": {}},
            {"type": "turn_end", "data": {"iteration": 1}},
        ]
    )

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "cost metrics",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('traces').mkdir(); "
                        f"Path('traces/run.jsonl').write_text({trace!r})"
                    ),
                ],
                "checks": [],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert result.ok
    assert result.metrics.iterations == 1
    assert result.metrics.tool_calls == 1
    assert result.metrics.input_tokens == 120
    assert result.metrics.output_tokens == 30
    assert result.metrics.compaction_count == 1
    assert result.metrics.wall_time_seconds >= 0
    assert result_to_dict(result)["metrics"]["input_tokens"] == 120


def test_run_case_fails_when_cost_budget_is_exceeded(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")
    trace = json.dumps({"type": "llm_usage", "data": {"input_tokens": 120, "output_tokens": 30}})

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "cost budget",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('traces').mkdir(); "
                        f"Path('traces/run.jsonl').write_text({trace!r})"
                    ),
                ],
                "budgets": {"max_input_tokens": 100},
                "checks": [],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert not result.ok
    assert result.failure_reason == "budget_exceeded"
    assert result.checks[0].name == "budget:input_tokens"
    assert "120 > 100" in result.checks[0].message


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


def test_structured_report_check_validates_agent_report_shape(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "structured report",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "import json, pathlib; "
                        "pathlib.Path('report.json').write_text(json.dumps({"
                        "'ok': True, "
                        "'summary': 'upgrade passed', "
                        "'changed_files': ['package.json'], "
                        "'remaining_risks': []"
                        "}))"
                    ),
                ],
                "checks": [
                    {
                        "type": "structured_report",
                        "path": "report.json",
                        "ok": True,
                        "allowed_changed_files": ["package.json"],
                        "allow_remaining_risks": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert result.ok
    assert result.checks[0].name == "structured_report:report.json"
    assert "ok=True" in result.checks[0].message


def test_structured_report_check_reports_unexpected_changed_files(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "structured report failure",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "import json, pathlib; "
                        "pathlib.Path('report.json').write_text(json.dumps({"
                        "'ok': True, "
                        "'summary': 'upgrade passed', "
                        "'changed_files': ['src/index.js'], "
                        "'remaining_risks': []"
                        "}))"
                    ),
                ],
                "checks": [
                    {
                        "type": "structured_report",
                        "path": "report.json",
                        "ok": True,
                        "allowed_changed_files": ["package.json"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert not result.ok
    assert result.failure_reason == "structured_report_failed"
    assert result.checks[0].name == "structured_report:report.json"
    assert "unexpected changed files" in result.checks[0].message


def test_structured_report_check_validates_optional_failure_fields(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "structured report optional fields",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "import json, pathlib; "
                        "pathlib.Path('report.json').write_text(json.dumps({"
                        "'ok': False, "
                        "'summary': 'upgrade failed', "
                        "'changed_files': [], "
                        "'remaining_risks': ['tests failed'], "
                        "'failure_reason': 42, "
                        "'recovery_suggestions': ['inspect test output']"
                        "}))"
                    ),
                ],
                "checks": [
                    {
                        "type": "structured_report",
                        "path": "report.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert not result.ok
    assert result.failure_reason == "structured_report_failed"
    assert "report.failure_reason must be a string or null" in result.checks[0].message


def test_research_report_check_requires_actual_read_source_and_gap_fallback(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")
    trace = json.dumps(
        {
            "type": "tool_call",
            "data": {
                "name": "retrieve_source_chunks",
                "input": {"url": "https://example.test/mocha/CHANGELOG.md"},
            },
        }
    )
    report = (
        "Sources read:\n"
        "- https://example.test/mocha/CHANGELOG.md\n\n"
        "Source gap: migration guide was missing, so tests must drive verification.\n"
    )

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "research report",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        f"Path('trace.jsonl').write_text({trace!r}); "
                        f"Path('research.md').write_text({report!r})"
                    ),
                ],
                "checks": [
                    {
                        "type": "research_report",
                        "path": "research.md",
                        "trace_path": "trace.jsonl",
                        "min_sources": 1,
                        "require_source_gap_fallback": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert result.ok
    assert result.checks[0].name == "research_report:research.md"


def test_research_report_check_rejects_hallucinated_sources(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _write_package(target / "package.json")
    trace = json.dumps(
        {
            "type": "tool_call",
            "data": {
                "name": "retrieve_source_chunks",
                "input": {"url": "https://example.test/mocha/CHANGELOG.md"},
            },
        }
    )
    report = "Sources read:\n- https://hallucinated.example/mocha.md\n"

    case_path = tmp_path / "case.json"
    case_path.write_text(
        json.dumps(
            {
                "name": "research report hallucination",
                "target": str(target),
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        f"Path('trace.jsonl').write_text({trace!r}); "
                        f"Path('research.md').write_text({report!r})"
                    ),
                ],
                "checks": [
                    {
                        "type": "research_report",
                        "path": "research.md",
                        "trace_path": "trace.jsonl",
                        "min_sources": 1,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_case(load_case(case_path), workspace=tmp_path / "work")

    assert not result.ok
    assert result.failure_reason == "research_report_failed"
    assert "reported unread source" in result.checks[0].message
