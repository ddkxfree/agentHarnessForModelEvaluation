"""Independent validation of a candidate adapter, run by the controller, not by the agent.

The candidate runs in a separate process in a scratch directory holding a copy of its files and
a copy of the data; the originals, the scoring rules and the acceptance file are never placed
there. Acceptance cases are passed on stdin. The source package is hashed before and after.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from harness.onboard.check import CHECKER_VERSION, PROTECTED_VIOLATION
from harness.onboard.spec import OnboardSpec, source_fingerprint
from harness.onboard.workspace import sha256
from harness.sandbox.base import SandboxError

VALIDATOR_VERSION = f"onboard_validator_v1+{CHECKER_VERSION}"

ENV_ERROR = "env_error"
VALIDATION_TIMEOUT = "validation_timeout"
VALIDATION_CRASHED = "validation_crashed"
SOURCE_CHANGED = "source_changed"


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Executor(Protocol):
    """Runs a command for the validator; raises SandboxError when it cannot run at all."""

    def run(self, argv: list[str], *, cwd: Path, stdin: str, timeout: float) -> ExecResult: ...

    def describe(self) -> dict[str, Any]: ...


def _clean_env(home: Path) -> dict[str, str]:
    """No credentials reach candidate code."""
    keep = ("PATH", "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV", "SYSTEMROOT", "TMPDIR")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
    return env


class SubprocessExecutor:
    """A fresh Python process per check, with a scrubbed environment and a time limit."""

    def run(self, argv: list[str], *, cwd: Path, stdin: str, timeout: float) -> ExecResult:
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_clean_env(cwd),
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout or ""
            err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr or ""
            return ExecResult(-1, out, err, timed_out=True)
        except OSError as exc:
            raise SandboxError(f"cannot start {argv[0]}: {exc}") from exc
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)

    def describe(self) -> dict[str, Any]:
        return {"type": "subprocess", "python": sys.executable, "version": sys.version.split()[0]}


def read_cases(spec: OnboardSpec) -> list[dict[str, Any]]:
    """The frozen acceptance cases, plus an empty output for every sample they name, which must
    never score as correct."""
    cases = []
    for n, line in enumerate(spec.path(spec.acceptance).read_text().splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        if case.get("expect") not in ("correct", "wrong") or not isinstance(
            case.get("output"), str
        ):
            raise ValueError(f"{spec.acceptance}:{n}: needs 'output' and expect correct/wrong")
        cases.append(case)
    if not any(c["expect"] == "correct" for c in cases) or not any(
        c["expect"] == "wrong" for c in cases
    ):
        raise ValueError("acceptance needs both correct and wrong outputs")
    for sample_id in sorted({c["sample_id"] for c in cases}):
        cases.append({"sample_id": sample_id, "output": "", "expect": "wrong", "note": "empty"})
    return cases


def _tail(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else "..." + text[-limit:]


def validate(
    spec: OnboardSpec,
    candidate: Path,
    executor: Executor | None = None,
    *,
    expected_source: str | None = None,
) -> dict[str, Any]:
    """Validate the candidate files in ``candidate``; returns a structured report.

    ``category`` is None when every check passed; otherwise one of the check categories
    (import, dependency, protocol, scoring, protected) or ``validation_timeout``,
    ``validation_crashed``, ``env_error`` or ``source_changed``.
    """
    executor = executor or SubprocessExecutor()
    expected_source = expected_source or spec.source_sha256
    report: dict[str, Any] = {
        "validator_version": VALIDATOR_VERSION,
        "executor": executor.describe(),
        "candidate_files": {},
        "source_sha256_expected": expected_source,
        "source_sha256_before": source_fingerprint(spec),
        "self_test": None,
    }

    def finish(category: str | None, detail: str | None = None) -> dict[str, Any]:
        report["source_sha256_after"] = source_fingerprint(spec)
        if category is None and report["source_sha256_after"] != report["source_sha256_before"]:
            category, detail = PROTECTED_VIOLATION, "the source package changed during validation"
        report["passed"] = category is None
        report["category"] = category
        if detail is not None:
            report["detail"] = detail
        return report

    if report["source_sha256_before"] != expected_source:
        return finish(
            SOURCE_CHANGED,
            f"source package fingerprint is {report['source_sha256_before']}, expected "
            f"{expected_source}; an earlier validation does not carry over",
        )
    cases = read_cases(spec)
    nonce = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="onboard-validate-") as tmp:
        scratch = Path(tmp)
        cand, data = scratch / "candidate", scratch / "data"
        shutil.copytree(candidate, cand)
        shutil.copytree(spec.path(spec.data), data)
        report["candidate_files"] = {
            p.name: sha256(p.read_bytes()) for p in sorted(cand.iterdir()) if p.is_file()
        }
        out = scratch / "report.json"
        argv = [
            sys.executable,
            "-m",
            "harness.onboard.check",
            "--candidate",
            str(cand),
            "--data",
            str(data),
            "--task",
            spec.task,
            "--allowed-imports",
            json.dumps(spec.allowed_imports),
            "--report",
            str(out),
        ]
        try:
            res = executor.run(
                argv,
                cwd=scratch,
                stdin=json.dumps({"nonce": nonce, "cases": cases}),
                timeout=spec.budget.validation_timeout_s,
            )
        except SandboxError as exc:
            return finish(ENV_ERROR, str(exc))
        res.stderr = res.stderr.replace(str(scratch), "<sandbox>")
        if res.timed_out:
            limit = spec.budget.validation_timeout_s
            return finish(
                VALIDATION_TIMEOUT, f"checks did not finish in {limit}s\n{_tail(res.stderr)}"
            )
        try:
            # The scratch path differs every time; without it the diagnosis is reproducible.
            checked = json.loads(out.read_text().replace(str(scratch), "<sandbox>"))
        except (OSError, ValueError):
            checked = None
        if checked is None or checked.get("nonce") != nonce or res.returncode != 0:
            return finish(
                VALIDATION_CRASHED,
                f"the check process exited with {res.returncode} without a valid report\n"
                f"{_tail(res.stderr)}",
            )
        checked.pop("nonce")
        report.update(checks=checked["checks"], cases=checked["cases"])
        report["data_sha256_before"] = checked["data_sha256_before"]
        report["data_sha256_after"] = checked["data_sha256_after"]
        report["self_test"] = _self_test(executor, scratch, cand, data, spec)
        if checked["passed"]:
            return finish(None)
        failed = [c for c in checked["checks"] if c["status"] == "failed"]
        return finish(checked["category"], failed[-1]["detail"] if failed else None)


def _self_test(
    executor: Executor, scratch: Path, cand: Path, data: Path, spec: OnboardSpec
) -> dict[str, Any]:
    """The agent's own tests, run for information only: passing them is never enough."""
    if not (cand / "test_adapter.py").is_file():
        return {"status": "missing"}
    argv = [
        sys.executable,
        "-c",
        "import os, sys, pytest; os.environ['ONBOARD_DATA_DIR'] = sys.argv[1]; "
        "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', sys.argv[2]]))",
        str(data),
        str(cand / "test_adapter.py"),
    ]
    try:
        res = executor.run(argv, cwd=scratch, stdin="", timeout=spec.budget.validation_timeout_s)
    except SandboxError as exc:
        return {"status": "env_error", "output": str(exc)}
    if res.timed_out:
        return {"status": "timeout", "output": _tail(res.stdout)}
    return {
        "status": "passed" if res.returncode == 0 else "failed",
        "output": _tail((res.stdout + res.stderr).replace(str(scratch), "<sandbox>")),
    }
