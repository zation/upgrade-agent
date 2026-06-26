"""Run deterministic eval cases against copied target projects.

The eval runner intentionally avoids LLM judging in its first version. It runs a
case command in an isolated copy of the target project, then checks objective
postconditions such as package versions, test commands, and changed paths.
"""

from __future__ import annotations

import argparse
import json
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


def load_case(path: Path) -> EvalCase:
    """Load one JSON eval case definition."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return EvalCase(
        name=data["name"],
        target=Path(data["target"]).expanduser(),
        command=data["command"],
        checks=list(data.get("checks", [])),
    )


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
    _init_git_baseline(workdir)

    proc = _run_case_command(case.command, workdir)
    checks = [_run_check(check, workdir) for check in case.checks]
    command_ok = proc.returncode == 0
    ok = command_ok and all(check.ok for check in checks)
    if not command_ok:
        checks = [
            CheckResult(
                name="case_command",
                ok=False,
                message=f"command exited {proc.returncode}",
            ),
            *checks,
        ]
    return EvalResult(
        case_name=case.name,
        ok=ok,
        workdir=workdir,
        command_exit_code=proc.returncode,
        checks=checks,
    )


def result_to_dict(result: EvalResult) -> dict[str, Any]:
    """Serialize an eval result for CLI/CI output."""
    return {
        "case_name": result.case_name,
        "ok": result.ok,
        "workdir": str(result.workdir),
        "command_exit_code": result.command_exit_code,
        "checks": [
            {"name": check.name, "ok": check.ok, "message": check.message}
            for check in result.checks
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic agent eval cases.")
    parser.add_argument("case", type=Path, help="Path to a JSON eval case.")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Directory for isolated target copies. Defaults to a temp directory.",
    )
    args = parser.parse_args(argv)

    result = run_case(load_case(args.case), workspace=args.workspace)
    print(json.dumps(result_to_dict(result), indent=2))
    return 0 if result.ok else 1


def _copy_ignore(directory: str, names: list[str]) -> set[str]:
    ignored = {".git", "node_modules", ".venv", "venv", "__pycache__", "coverage"}
    return ignored.intersection(names)


def _init_git_baseline(workdir: Path) -> None:
    _run(["git", "init"], workdir)
    _run(["git", "config", "user.email", "eval@example.invalid"], workdir)
    _run(["git", "config", "user.name", "Eval Runner"], workdir)
    _run(["git", "add", "."], workdir)
    _run(["git", "commit", "-m", "eval baseline"], workdir)


def _run_case_command(command: str | list[str], workdir: Path) -> subprocess.CompletedProcess[str]:
    if isinstance(command, str):
        rendered = command.replace("{workdir}", str(workdir))
        return subprocess.run(
            rendered,
            cwd=workdir,
            shell=True,
            text=True,
            capture_output=True,
            timeout=1800,
        )
    rendered_args = [part.replace("{workdir}", str(workdir)) for part in command]
    return subprocess.run(
        rendered_args,
        cwd=workdir,
        text=True,
        capture_output=True,
        timeout=1800,
    )


def _run_check(check: dict[str, Any], workdir: Path) -> CheckResult:
    check_type = check.get("type")
    if check_type == "package_json_version":
        return _check_package_json_version(check, workdir)
    if check_type == "command":
        return _check_command(check, workdir)
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


def _check_command(check: dict[str, Any], workdir: Path) -> CheckResult:
    command = check["command"]
    proc = subprocess.run(
        command,
        cwd=workdir,
        shell=True,
        text=True,
        capture_output=True,
        timeout=int(check.get("timeout", 300)),
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
