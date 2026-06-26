"""Run deterministic eval cases against copied target projects.

The eval runner intentionally avoids LLM judging in its first version. It runs a
case command in an isolated copy of the target project, then checks objective
postconditions such as package versions, test commands, and changed paths.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
class EvalResult:
    case_name: str
    ok: bool
    workdir: Path
    command_exit_code: int
    checks: list[CheckResult]
    teardown_exit_codes: list[int]


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
    command_ok = proc.returncode == 0
    ok = command_ok and all(check.ok for check in checks)
    return EvalResult(
        case_name=case.name,
        ok=ok,
        workdir=workdir,
        command_exit_code=proc.returncode,
        checks=checks,
        teardown_exit_codes=[result.returncode for result in teardown_results],
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
    return CheckResult(name=str(check_type or "unknown"), ok=False, message="unknown check type")


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
    proc = _run(["git", "diff", "--name-only"], workdir)
    changed = {line for line in proc.stdout.splitlines() if line.strip()}
    unexpected = sorted(changed - allowed)
    ok = not unexpected
    message = f"changed={sorted(changed)}"
    if unexpected:
        message = f"unexpected changed paths={unexpected}; {message}"
    return CheckResult(name="git_diff", ok=ok, message=message)


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
