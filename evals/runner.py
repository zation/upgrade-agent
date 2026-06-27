"""Run deterministic eval cases against copied target projects.

The eval runner intentionally avoids LLM judging in its first version. It runs a
case command in an isolated copy of the target project, then checks objective
postconditions such as package versions, test commands, and changed paths.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    name: str
    target: Path
    command: str | list[str]
    checks: list[dict[str, Any]]
    timeout: int = 1800
    env: dict[str, str] | None = None
    setup: list[str | list[str]] | None = None
    teardown: list[str | list[str]] | None = None
    budgets: dict[str, int] | None = None


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    message: str


@dataclass(frozen=True)
class EvalMetrics:
    iterations: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    wall_time_seconds: float = 0.0
    compaction_count: int = 0


@dataclass(frozen=True)
class EvalResult:
    case_name: str
    ok: bool
    workdir: Path
    command_exit_code: int
    checks: list[CheckResult]
    teardown_exit_codes: list[int]
    metrics: EvalMetrics
    failure_reason: str | None = None


@dataclass(frozen=True)
class BatchEvalResult:
    ok: bool
    count: int
    passed: int
    failed: int
    results: list[EvalResult]


def load_case(path: Path) -> EvalCase:
    """Load one JSON eval case definition."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return EvalCase(
        name=data["name"],
        target=Path(data["target"]).expanduser(),
        command=data["command"],
        checks=list(data.get("checks", [])),
        timeout=int(data.get("timeout", 1800)),
        env=data.get("env"),
        setup=data.get("setup"),
        teardown=data.get("teardown"),
        budgets=data.get("budgets"),
    )


def load_cases(paths: list[Path]) -> list[EvalCase]:
    """Load JSON cases from files or directories."""
    case_paths: list[Path] = []
    for path in paths:
        if path.is_dir():
            case_paths.extend(sorted(path.glob("*.json")))
        else:
            case_paths.append(path)
    if not case_paths:
        raise ValueError("No eval cases found.")
    return [load_case(path) for path in case_paths]


def run_case(case: EvalCase, workspace: Path | None = None) -> EvalResult:
    """Run ``case`` in an isolated target copy and return deterministic results."""
    start_time = time.perf_counter()
    if not case.target.exists() or not case.target.is_dir():
        raise ValueError(f"Target directory does not exist: {case.target}")

    root = workspace or Path(tempfile.mkdtemp(prefix="upgrade-agent-eval-"))
    root.mkdir(parents=True, exist_ok=True)
    workdir = root / _slug(case.name)
    if workdir.exists():
        shutil.rmtree(workdir)
    shutil.copytree(case.target, workdir, ignore=_copy_ignore)

    env = _merged_env(case.env)
    checks: list[CheckResult] = []
    setup_results = [
        _run_eval_command(command, workdir, timeout=case.timeout, env=env)
        for command in case.setup or []
    ]
    setup_failed = next((result for result in setup_results if result.returncode != 0), None)

    if setup_failed is None:
        _init_git_baseline(workdir)
        proc = _run_eval_command(case.command, workdir, timeout=case.timeout, env=env)
        checks = [_run_check(check, workdir, env=env) for check in case.checks]
    else:
        proc = setup_failed
        checks = [
            CheckResult(
                name="case_setup",
                ok=False,
                message=_command_message(setup_failed),
            )
        ]

    if proc.returncode != 0 and not any(
        check.name in {"case_command", "case_setup"} for check in checks
    ):
        checks = [
            CheckResult(
                name="case_command",
                ok=False,
                message=_command_message(proc),
            ),
            *checks,
        ]
    metrics = _collect_metrics(workdir, wall_time_seconds=time.perf_counter() - start_time)
    budget_checks = _check_budgets(case.budgets or {}, metrics)
    checks = [*budget_checks, *checks]
    teardown_results = [
        _run_eval_command(command, workdir, timeout=case.timeout, env=env)
        for command in case.teardown or []
    ]
    teardown_failed_checks = [
        CheckResult(
            name=f"case_teardown:{index}",
            ok=False,
            message=_command_message(result),
        )
        for index, result in enumerate(teardown_results, 1)
        if result.returncode != 0
    ]
    checks = [*checks, *teardown_failed_checks]
    if teardown_results:
        metrics = _collect_metrics(workdir, wall_time_seconds=time.perf_counter() - start_time)
    command_ok = proc.returncode == 0
    ok = command_ok and all(check.ok for check in checks)
    return EvalResult(
        case_name=case.name,
        ok=ok,
        workdir=workdir,
        command_exit_code=proc.returncode,
        checks=checks,
        teardown_exit_codes=[result.returncode for result in teardown_results],
        metrics=metrics,
        failure_reason=_classify_failure(proc.returncode, checks),
    )


def run_cases(cases: list[EvalCase], workspace: Path | None = None) -> BatchEvalResult:
    """Run multiple eval cases and return an aggregate summary."""
    results = [run_case(case, workspace=workspace) for case in cases]
    passed = sum(1 for result in results if result.ok)
    failed = len(results) - passed
    return BatchEvalResult(
        ok=failed == 0,
        count=len(results),
        passed=passed,
        failed=failed,
        results=results,
    )


def result_to_dict(result: EvalResult) -> dict[str, Any]:
    """Serialize an eval result for CLI/CI output."""
    return {
        "case_name": result.case_name,
        "ok": result.ok,
        "workdir": str(result.workdir),
        "command_exit_code": result.command_exit_code,
        "teardown_exit_codes": result.teardown_exit_codes,
        "failure_reason": result.failure_reason,
        "metrics": {
            "iterations": result.metrics.iterations,
            "tool_calls": result.metrics.tool_calls,
            "input_tokens": result.metrics.input_tokens,
            "output_tokens": result.metrics.output_tokens,
            "wall_time_seconds": result.metrics.wall_time_seconds,
            "compaction_count": result.metrics.compaction_count,
        },
        "checks": [
            {"name": check.name, "ok": check.ok, "message": check.message}
            for check in result.checks
        ],
    }


def batch_result_to_dict(result: BatchEvalResult) -> dict[str, Any]:
    """Serialize a batch eval result for CLI/CI output."""
    return {
        "ok": result.ok,
        "count": result.count,
        "passed": result.passed,
        "failed": result.failed,
        "results": [result_to_dict(case_result) for case_result in result.results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic agent eval cases.")
    parser.add_argument(
        "cases",
        nargs="+",
        type=Path,
        help="Path(s) to JSON eval case files or directories containing *.json cases.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Directory for isolated target copies. Defaults to a temp directory.",
    )
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if len(cases) == 1 and not args.cases[0].is_dir():
        result = run_case(cases[0], workspace=args.workspace)
        print(json.dumps(result_to_dict(result), indent=2))
        return 0 if result.ok else 1
    batch = run_cases(cases, workspace=args.workspace)
    print(json.dumps(batch_result_to_dict(batch), indent=2))
    return 0 if batch.ok else 1


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {".git", "node_modules", ".venv", "venv", "__pycache__", "coverage"}
    return ignored.intersection(names)


def _init_git_baseline(workdir: Path) -> None:
    _run(["git", "init"], workdir)
    _run(["git", "config", "user.email", "eval@example.invalid"], workdir)
    _run(["git", "config", "user.name", "Eval Runner"], workdir)
    _run(["git", "add", "."], workdir)
    _run(["git", "commit", "-m", "eval baseline"], workdir)


def _run_eval_command(
    command: str | list[str],
    workdir: Path,
    *,
    timeout: int,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    if isinstance(command, str):
        rendered = command.replace("{workdir}", str(workdir))
        try:
            return subprocess.run(
                rendered,
                cwd=workdir,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            return _timeout_result(rendered, timeout, e)
    rendered_args = [part.replace("{workdir}", str(workdir)) for part in command]
    try:
        return subprocess.run(
            rendered_args,
            cwd=workdir,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as e:
        return _timeout_result(rendered_args, timeout, e)


def _run_check(check: dict[str, Any], workdir: Path, *, env: dict[str, str]) -> CheckResult:
    check_type = check.get("type")
    if check_type == "package_json_version":
        return _check_package_json_version(check, workdir)
    if check_type == "command":
        return _check_command(check, workdir, env=env)
    if check_type == "git_diff":
        return _check_git_diff(check, workdir)
    if check_type == "trace_sequence":
        return _check_trace_sequence(check, workdir)
    if check_type == "trajectory_policy":
        return _check_trajectory_policy(check, workdir)
    if check_type == "structured_report":
        return _check_structured_report(check, workdir)
    if check_type == "research_report":
        return _check_research_report(check, workdir)
    return CheckResult(name=str(check_type or "unknown"), ok=False, message="unknown check type")


def _collect_metrics(workdir: Path, *, wall_time_seconds: float) -> EvalMetrics:
    traces_dir = workdir / "traces"
    if not traces_dir.exists():
        return EvalMetrics(wall_time_seconds=round(wall_time_seconds, 3))

    input_tokens = 0
    output_tokens = 0
    tool_calls = 0
    turn_iterations: set[int] = set()
    turn_count = 0
    compaction_count = 0
    for trace_path in sorted(traces_dir.glob("*.jsonl")):
        try:
            events = _read_trace_events(trace_path)
        except ValueError:
            continue
        for event in events:
            event_type = event.get("type")
            data = event.get("data", {})
            if event_type == "llm_usage":
                input_tokens += int(data.get("input_tokens") or 0)
                output_tokens += int(data.get("output_tokens") or 0)
            elif _is_tool_call(event):
                tool_calls += 1
            elif event_type == "turn_end":
                turn_count += 1
                iteration = data.get("iteration")
                if isinstance(iteration, int):
                    turn_iterations.add(iteration)
            elif event_type == "context_compacted":
                compaction_count += 1

    iterations = max(turn_iterations) if turn_iterations else turn_count
    return EvalMetrics(
        iterations=iterations,
        tool_calls=tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        wall_time_seconds=round(wall_time_seconds, 3),
        compaction_count=compaction_count,
    )


def _check_budgets(budgets: dict[str, int], metrics: EvalMetrics) -> list[CheckResult]:
    metric_by_budget = {
        "max_iterations": ("iterations", metrics.iterations),
        "max_tool_calls": ("tool_calls", metrics.tool_calls),
        "max_input_tokens": ("input_tokens", metrics.input_tokens),
        "max_output_tokens": ("output_tokens", metrics.output_tokens),
        "max_wall_time_seconds": ("wall_time_seconds", metrics.wall_time_seconds),
        "max_compaction_count": ("compaction_count", metrics.compaction_count),
    }
    results: list[CheckResult] = []
    for budget_name, (metric_name, actual) in metric_by_budget.items():
        if budget_name not in budgets:
            continue
        limit = budgets[budget_name]
        ok = actual <= limit
        results.append(
            CheckResult(
                name=f"budget:{metric_name}",
                ok=ok,
                message=f"{metric_name}={actual}; budget {actual} <= {limit}"
                if ok
                else f"{metric_name} budget exceeded: {actual} > {limit}",
            )
        )
    return results


def _check_package_json_version(check: dict[str, Any], workdir: Path) -> CheckResult:
    dependency = check["dependency"]
    section = check.get("section", "dependencies")
    expected = check["specifier"]
    package_json = json.loads((workdir / "package.json").read_text(encoding="utf-8"))
    actual = package_json.get(section, {}).get(dependency)
    name = f"package_json_version:{dependency}"
    if actual is None:
        return CheckResult(name=name, ok=False, message=f"{dependency} missing from {section}")
    ok = _satisfies(actual, expected)
    message = f"{dependency} is {actual}; expected {expected}"
    return CheckResult(name=name, ok=ok, message=message)


def _check_command(check: dict[str, Any], workdir: Path, *, env: dict[str, str]) -> CheckResult:
    command = check["command"]
    proc = _run_eval_command(
        command,
        workdir,
        timeout=int(check.get("timeout", 300)),
        env=env,
    )
    ok = proc.returncode == int(check.get("exit_code", 0))
    output = (proc.stdout + proc.stderr).strip()
    message = f"exit {proc.returncode}"
    if output:
        message = f"{message}; {output[-500:]}"
    return CheckResult(name=f"command:{command}", ok=ok, message=message)


def _check_git_diff(check: dict[str, Any], workdir: Path) -> CheckResult:
    allowed = set(check.get("allowed_paths", []))
    proc = _run(["git", "status", "--porcelain"], workdir)
    changed = {_status_path(line) for line in proc.stdout.splitlines() if line.strip()}
    unexpected = sorted(changed - allowed)
    ok = not unexpected
    message = f"changed={sorted(changed)}"
    if unexpected:
        message = f"unexpected changed paths={unexpected}; {message}"
    return CheckResult(name="git_diff", ok=ok, message=message)


def _status_path(line: str) -> str:
    # `git status --porcelain` uses two status columns, a space, then path.
    return line[3:].strip()


def _check_structured_report(check: dict[str, Any], workdir: Path) -> CheckResult:
    report_path = Path(check["path"])
    full = report_path if report_path.is_absolute() else workdir / report_path
    name = f"structured_report:{report_path.as_posix()}"
    if not full.exists():
        return CheckResult(name=name, ok=False, message=f"report file not found: {report_path}")
    try:
        report = json.loads(full.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return CheckResult(name=name, ok=False, message=f"invalid report JSON: {e}")
    shape_error = _agent_report_shape_error(report)
    if shape_error:
        return CheckResult(name=name, ok=False, message=shape_error)

    expected_ok = check.get("ok")
    if expected_ok is not None and report["ok"] != bool(expected_ok):
        return CheckResult(
            name=name,
            ok=False,
            message=f"ok={report['ok']}; expected {bool(expected_ok)}",
        )

    changed_files = set(report["changed_files"])
    allowed_changed = set(check.get("allowed_changed_files", []))
    if allowed_changed:
        unexpected = sorted(changed_files - allowed_changed)
        if unexpected:
            return CheckResult(
                name=name,
                ok=False,
                message=f"unexpected changed files={unexpected}; changed={sorted(changed_files)}",
            )

    if check.get("allow_remaining_risks") is False and report["remaining_risks"]:
        return CheckResult(
            name=name,
            ok=False,
            message=f"remaining risks present: {report['remaining_risks']}",
        )

    return CheckResult(
        name=name,
        ok=True,
        message=f"ok={report['ok']}; changed_files={sorted(changed_files)}",
    )


def _agent_report_shape_error(value: Any) -> str | None:
    if not isinstance(value, dict):
        return "report must be a JSON object"
    if not isinstance(value.get("ok"), bool):
        return "report.ok must be a boolean"
    if not isinstance(value.get("summary"), str):
        return "report.summary must be a string"
    if not _is_string_list(value.get("changed_files")):
        return "report.changed_files must be a list of strings"
    if not _is_string_list(value.get("remaining_risks")):
        return "report.remaining_risks must be a list of strings"
    failure_reason = value.get("failure_reason")
    if failure_reason is not None and not isinstance(failure_reason, str):
        return "report.failure_reason must be a string or null"
    recovery_suggestions = value.get("recovery_suggestions")
    if recovery_suggestions is not None and not _is_string_list(recovery_suggestions):
        return "report.recovery_suggestions must be a list of strings"
    return None


def _check_research_report(check: dict[str, Any], workdir: Path) -> CheckResult:
    report_path = Path(check["path"])
    full = report_path if report_path.is_absolute() else workdir / report_path
    name = f"research_report:{report_path.as_posix()}"
    if not full.exists():
        return CheckResult(name=name, ok=False, message=f"report file not found: {report_path}")

    text = full.read_text(encoding="utf-8")
    reported_urls = _extract_urls(text)
    min_sources = int(check.get("min_sources", 1))
    if len(reported_urls) < min_sources:
        return CheckResult(
            name=name,
            ok=False,
            message=f"reported_sources={len(reported_urls)}; expected at least {min_sources}",
        )

    trace_path = check.get("trace_path")
    if trace_path:
        trace_full = Path(trace_path)
        trace_full = trace_full if trace_full.is_absolute() else workdir / trace_full
        if not trace_full.exists():
            return CheckResult(
                name=name,
                ok=False,
                message=f"trace file not found: {Path(trace_path)}",
            )
        read_urls = _research_source_urls_from_trace(_read_trace_events(trace_full))
        unread = sorted(set(reported_urls) - read_urls)
        if unread:
            return CheckResult(
                name=name,
                ok=False,
                message=f"reported unread source(s): {unread}; read={sorted(read_urls)}",
            )

    if check.get("require_source_gap_fallback") and not _has_source_gap_fallback(text):
        return CheckResult(
            name=name,
            ok=False,
            message="source gap fallback missing; expected source gap and test-driven verification",
        )

    return CheckResult(
        name=name,
        ok=True,
        message=f"reported_sources={len(reported_urls)}",
    )


def _extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"https?://[^\s)\]>,]+", text):
        url = match.group(0).rstrip(".,;:")
        if url not in urls:
            urls.append(url)
    return urls


def _research_source_urls_from_trace(events: list[dict[str, Any]]) -> set[str]:
    urls: set[str] = set()
    for event in events:
        if not _is_tool_call(event):
            continue
        data = event.get("data", {})
        tool = data.get("name")
        tool_input = data.get("input", {})
        if tool in {"fetch_url", "retrieve_source_chunks"}:
            url = tool_input.get("url")
            if isinstance(url, str):
                urls.add(url)
        elif tool == "fetch_releases":
            owner = tool_input.get("owner")
            repo = tool_input.get("repo")
            if isinstance(owner, str) and isinstance(repo, str):
                urls.add(f"https://github.com/{owner}/{repo}/releases")
    return urls


def _has_source_gap_fallback(text: str) -> bool:
    lower = text.lower()
    return "source gap" in lower and "test" in lower and "verification" in lower


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _check_trace_sequence(check: dict[str, Any], workdir: Path) -> CheckResult:
    trace_path = Path(check["path"])
    full = trace_path if trace_path.is_absolute() else workdir / trace_path
    name = f"trace_sequence:{trace_path.as_posix()}"
    if not full.exists():
        return CheckResult(name=name, ok=False, message=f"trace file not found: {trace_path}")
    events = _read_trace_events(full)
    cursor = 0
    for index, requirement in enumerate(check.get("sequence", []), 1):
        match_at = _find_trace_match(events, requirement, start=cursor)
        if match_at is None:
            return CheckResult(
                name=name,
                ok=False,
                message=f"missing sequence item {index}: {requirement}",
            )
        cursor = match_at + 1
    return CheckResult(
        name=name,
        ok=True,
        message=f"matched {len(check.get('sequence', []))} ordered trace event(s)",
    )


def _check_trajectory_policy(check: dict[str, Any], workdir: Path) -> CheckResult:
    policy = check["policy"]
    trace_path = Path(check["path"])
    full = trace_path if trace_path.is_absolute() else workdir / trace_path
    name = f"trajectory_policy:{policy}"
    if not full.exists():
        return CheckResult(name=name, ok=False, message=f"trace file not found: {trace_path}")
    events = _read_trace_events(full)
    if policy == "baseline_before_mutation":
        return _check_baseline_before_mutation(name, events)
    if policy == "single_dependency_at_a_time":
        return _check_single_dependency_at_a_time(name, events)
    return CheckResult(name=name, ok=False, message=f"unknown trajectory policy: {policy}")


def _check_baseline_before_mutation(name: str, events: list[dict[str, Any]]) -> CheckResult:
    saw_baseline = False
    for event in events:
        if not _is_tool_call(event):
            continue
        if _is_test_command(event):
            saw_baseline = True
            continue
        if _is_mutating_event(event):
            if saw_baseline:
                return CheckResult(name=name, ok=True, message="baseline observed before mutation")
            return CheckResult(
                name=name,
                ok=False,
                message=f"mutation before baseline: {_event_description(event)}",
            )
    if saw_baseline:
        return CheckResult(name=name, ok=True, message="baseline observed; no mutation found")
    return CheckResult(name=name, ok=False, message="no baseline test command found")


def _is_tool_call(event: dict[str, Any]) -> bool:
    return event.get("type") == "tool_call" and event.get("data", {}).get("phase") != "result"


def _is_test_command(event: dict[str, Any]) -> bool:
    data = event.get("data", {})
    command = data.get("input", {}).get("command", "")
    return data.get("name") == "run_command" and _looks_like_test_command(command)


def _looks_like_test_command(command: str) -> bool:
    normalized = command.lower()
    return any(
        marker in normalized
        for marker in ("npm test", "npm run test", "yarn test", "pnpm test", "pytest")
    )


def _is_mutating_event(event: dict[str, Any]) -> bool:
    data = event.get("data", {})
    tool = data.get("name")
    if tool in {"write_file", "edit_file"}:
        return True
    if tool != "run_command":
        return False
    command = data.get("input", {}).get("command", "").lower()
    mutating_markers = (
        "npm install",
        "npm i ",
        "yarn add",
        "pnpm add",
        "git checkout",
        "git reset",
        "rm ",
    )
    return any(marker in command for marker in mutating_markers)


def _event_description(event: dict[str, Any]) -> str:
    data = event.get("data", {})
    tool = data.get("name", "?")
    command = data.get("input", {}).get("command")
    return f"{tool} {command}" if command else tool


def _check_single_dependency_at_a_time(name: str, events: list[dict[str, Any]]) -> CheckResult:
    for event in events:
        if not _is_tool_call(event):
            continue
        command = event.get("data", {}).get("input", {}).get("command", "")
        targets = _npm_install_targets(command)
        if len(targets) > 1:
            return CheckResult(
                name=name,
                ok=False,
                message=f"multiple install targets in one command: {targets}",
            )
    return CheckResult(name=name, ok=True, message="no multi-dependency install command found")


def _npm_install_targets(command: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if len(parts) < 3:
        return []
    if parts[0] != "npm" or parts[1] not in {"install", "i"}:
        return []
    targets: list[str] = []
    skip_next = False
    for part in parts[2:]:
        if skip_next:
            skip_next = False
            continue
        if part in {"--save-dev", "-D", "--save", "-S", "--global", "-g"}:
            continue
        if part in {"--workspace", "-w"}:
            skip_next = True
            continue
        if part.startswith("-"):
            continue
        targets.append(part)
    return targets


def _read_trace_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSONL trace at {path}:{line_no}: {e}") from e
        if isinstance(event, dict):
            events.append(event)
    return events


def _find_trace_match(
    events: list[dict[str, Any]], requirement: dict[str, Any], *, start: int
) -> int | None:
    for index in range(start, len(events)):
        if _trace_event_matches(events[index], requirement):
            return index
    return None


def _trace_event_matches(event: dict[str, Any], requirement: dict[str, Any]) -> bool:
    data = event.get("data", {})
    if requirement.get("event_type") and event.get("type") != requirement["event_type"]:
        return False
    if requirement.get("tool") and data.get("name") != requirement["tool"]:
        return False
    if needle := requirement.get("command_contains"):
        command = data.get("input", {}).get("command", "")
        if needle not in command:
            return False
    if needle := requirement.get("input_contains"):
        rendered = json.dumps(data.get("input", {}), ensure_ascii=False, sort_keys=True)
        if needle not in rendered:
            return False
    return True


def _run(args: list[str], workdir: Path) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, cwd=workdir, text=True, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"{args} failed in {workdir}: {proc.stderr or proc.stdout}")
    return proc


def _merged_env(extra: dict[str, str] | None) -> dict[str, str]:
    env = dict(os.environ)
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def _timeout_result(
    args: str | list[str], timeout: int, exc: subprocess.TimeoutExpired
) -> subprocess.CompletedProcess[str]:
    stdout = _decode_timeout_output(exc.stdout)
    stderr = _decode_timeout_output(exc.stderr)
    message = f"command timed out after {timeout}s"
    stderr = f"{stderr}\n{message}".strip()
    return subprocess.CompletedProcess(args=args, returncode=124, stdout=stdout, stderr=stderr)


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _command_message(proc: subprocess.CompletedProcess[str]) -> str:
    output = (proc.stdout + proc.stderr).strip()
    message = f"command exited {proc.returncode}"
    if output:
        message = f"{message}; {output[-500:]}"
    return message


def _classify_failure(exit_code: int, checks: list[CheckResult]) -> str | None:
    if exit_code == 0 and all(check.ok for check in checks):
        return None
    failing = next((check for check in checks if not check.ok), None)
    message = (failing.message if failing else "").lower()
    name = failing.name if failing else "case_command"
    if exit_code == 124 or "timed out" in message:
        return "timeout"
    if name.startswith("trace_sequence"):
        return "trajectory_violation"
    if name.startswith("trajectory_policy"):
        message = (failing.message if failing else "").lower()
        if "baseline" in message:
            return "baseline_missing"
        if "multiple install targets" in message:
            return "multi_dependency_upgrade"
        return "trajectory_violation"
    if name == "git_diff":
        return "wrong_diff"
    if name.startswith("structured_report:"):
        return "structured_report_failed"
    if name.startswith("research_report:"):
        return "research_report_failed"
    if name.startswith("budget:"):
        return "budget_exceeded"
    if name.startswith("command:"):
        return "test_failed"
    if name == "case_setup":
        return "setup_failed"
    if name == "case_command":
        if "llm" in message or "api" in message:
            return "llm_error"
        return "command_failed"
    return "postcondition_failed"


def _satisfies(actual: str, expected: str) -> bool:
    if expected.startswith(">="):
        return _version_tuple(actual) >= _version_tuple(expected[2:])
    if expected.startswith("=="):
        return _version_tuple(actual) == _version_tuple(expected[2:])
    return _version_tuple(actual) == _version_tuple(expected)


def _version_tuple(value: str) -> tuple[int, int, int]:
    numbers: list[int] = []
    current = ""
    for char in value:
        if char.isdigit():
            current += char
        elif current:
            numbers.append(int(current))
            current = ""
    if current:
        numbers.append(int(current))
    padded = [*numbers, 0, 0, 0][:3]
    return (padded[0], padded[1], padded[2])


def _slug(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "-" for char in value.strip()]
    slug = "".join(chars).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "case"


if __name__ == "__main__":
    sys.exit(main())
