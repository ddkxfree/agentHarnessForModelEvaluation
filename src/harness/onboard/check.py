"""Independent checks of a candidate adapter, run by the validator in a separate process.

    python -m harness.onboard.check --candidate DIR --data DIR --task NAME --report FILE
        --allowed-imports JSON < cases.json

The acceptance cases arrive on stdin, so they are never a file next to the candidate code. The
data directory is a copy; whether the candidate changed it is checked at the end. This guards
against mistakes and simple shortcuts, not against code written to attack the checker from
inside the same process.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import json
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

# Failure categories of a validation, in the order the checks run.
IMPORT_ERROR = "import_error"
DEPENDENCY_ERROR = "dependency_error"
PROTOCOL_ERROR = "protocol_error"
SCORING_ERROR = "scoring_error"
PROTECTED_VIOLATION = "protected_violation"

CHECKER_VERSION = "onboard_check_v1"


def _tail(text: str, limit: int = 1500) -> str:
    return text if len(text) <= limit else "..." + text[-limit:]


class Failed(Exception):
    def __init__(self, category: str, check: str, detail: str) -> None:
        super().__init__(detail)
        self.category, self.check, self.detail = category, check, detail


class Checker:
    def __init__(self, candidate: Path, data: Path, task: str, allowed: list[str]) -> None:
        self.candidate, self.data, self.task, self.allowed = candidate, data, task, allowed
        self.checks: list[dict[str, Any]] = []
        self.cases: list[dict[str, Any]] = []

    def ok(self, name: str, detail: str = "") -> None:
        self.checks.append({"name": name, "status": "passed", "detail": detail})

    # --- 1. static -------------------------------------------------------------------------
    def static(self) -> None:
        path = self.candidate / "adapter.py"
        if not path.is_file():
            raise Failed(IMPORT_ERROR, "static", "adapter.py was not written")
        try:
            tree = ast.parse(path.read_text(), filename="adapter.py")
        except SyntaxError as exc:
            raise Failed(IMPORT_ERROR, "static", f"syntax error: {exc}") from None
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level > 0:
                raise Failed(DEPENDENCY_ERROR, "static", "relative imports are not allowed")
        if extra := sorted(imported - set(self.allowed)):
            raise Failed(
                DEPENDENCY_ERROR, "static", f"imports {extra} not in allowed imports {self.allowed}"
            )
        self.ok("static", f"imports {sorted(imported)}")

    # --- 2. import -------------------------------------------------------------------------
    def load(self) -> Any:
        from harness.core.task import TASKS

        before = set(TASKS)
        spec = importlib.util.spec_from_file_location(
            f"candidate_{self.task}", self.candidate / "adapter.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except ModuleNotFoundError as exc:
            raise Failed(
                DEPENDENCY_ERROR, "import", f"{exc}\n{_tail(traceback.format_exc())}"
            ) from None
        except BaseException as exc:  # SystemExit from candidate code too
            raise Failed(
                IMPORT_ERROR,
                "import",
                f"{type(exc).__name__}: {exc}\n{_tail(traceback.format_exc())}",
            ) from None
        new = sorted(set(TASKS) - before)
        if new != [self.task]:
            raise Failed(
                PROTOCOL_ERROR,
                "import",
                f"adapter.py must register exactly {[self.task]}, got {new}",
            )
        self.ok("import")
        return TASKS[self.task]

    # --- 3. task protocol ------------------------------------------------------------------
    def protocol(self, factory: Any, case_ids: set[str]) -> Any:
        from harness.core.fingerprint import fingerprint_dir
        from harness.core.task import Task

        def fail(detail: str) -> Failed:
            return Failed(PROTOCOL_ERROR, "protocol", detail)

        try:
            task = factory(data_dir=str(self.data))
            if not isinstance(task, Task):
                raise fail(f"the factory returned {type(task).__name__}, not Task")
            if task.name != self.task:
                raise fail(f"Task.name is {task.name!r}, expected {self.task!r}")
            expected_rev = fingerprint_dir(self.data)
            if task.dataset.revision != expected_rev:
                raise fail(
                    f"dataset.revision is {task.dataset.revision!r}; it must be the content "
                    f"fingerprint of data_dir, fingerprint_dir(Path(data_dir)) = {expected_rev!r}"
                )
            first, second = task.load(), task.load()
        except Failed:
            raise
        except BaseException as exc:
            raise fail(f"{type(exc).__name__}: {exc}\n{_tail(traceback.format_exc())}") from None
        ids = [s.id for s in first]
        if not ids:
            raise fail("load() returned no samples")
        if len(set(ids)) != len(ids):
            raise fail("sample ids are not unique")
        if ids != [s.id for s in second]:
            raise fail("sample ids differ between two load() calls")
        if bad := [i for i in ids if not i.startswith(f"{self.task}/")]:
            raise fail(f"sample ids must start with {self.task + '/'!r}, got e.g. {bad[0]!r}")
        if missing := sorted(case_ids - set(ids)):
            raise fail(f"expected sample ids are missing, e.g. {missing[:3]}")
        if getattr(task.scorer, "requires_sandbox", False):
            raise fail("this workflow only accepts scorers that need no sandbox")
        desc = task.scorer.describe()
        if not isinstance(desc, dict) or not desc.get("fingerprint"):
            raise fail("scorer.describe() must return a dict with a 'fingerprint'")
        self.ok("protocol", f"{len(ids)} samples, revision {task.dataset.revision}")
        return task

    # --- 4. positive and negative outputs ----------------------------------------------------
    def score_cases(self, task: Any, cases: list[dict[str, Any]], out_dir: Path) -> None:
        from dataclasses import replace

        from harness.core.io import read_json, read_results
        from harness.core.model import MockModel
        from harness.core.runner import run_eval
        from harness.core.types import GenerateConfig, Outcome

        by_id = {s.id: s for s in task.load()}
        failures = []
        for case in cases:
            sample = by_id[case["sample_id"]]
            single = replace(task, load=lambda s=sample: [s])
            output = case["output"]
            try:
                run_dir = asyncio.run(
                    run_eval(
                        MockModel(lambda _m, o=output: o, name="acceptance"),
                        [(single, GenerateConfig())],
                        output_dir=out_dir,
                        retries=0,
                    )
                )
            except BaseException as exc:
                detail = f"{type(exc).__name__}: {exc}\n{_tail(traceback.format_exc())}"
                raise Failed(SCORING_ERROR, "cases", detail) from None
            (r,) = read_results(run_dir / self.task / "samples.jsonl")
            manifest = read_json(run_dir / "manifest.json")["tasks"][0]
            got = r.outcome.value
            if r.outcome == Outcome.CORRECT:
                verdict = "correct"
            elif r.error_kind is not None:
                verdict = "error"
            else:
                verdict = "wrong"
            passed = verdict == case["expect"]
            record = {
                **case,
                "outcome": got,
                "score": r.score.value if r.score else None,
                "error": r.error,
                "passed": passed,
                "scorer_fingerprint": manifest["scorer"].get("fingerprint"),
            }
            self.cases.append(record)
            if not passed:
                failures.append(record)
        if failures:
            lines = [
                f"{f['sample_id']}: output {f['output']!r} ({f.get('note', '')}) should be "
                f"{f['expect']}, got {f['outcome']}" + (f" - {f['error']}" if f["error"] else "")
                for f in failures
            ]
            raise Failed(
                SCORING_ERROR,
                "cases",
                f"{len(failures)} of {len(cases)} cases failed:\n" + "\n".join(lines),
            )
        self.ok("cases", f"{len(cases)} cases")


def run_checks(
    candidate: Path, data: Path, task: str, allowed: list[str], cases: list[dict[str, Any]]
) -> dict[str, Any]:
    from harness.core.fingerprint import fingerprint_dir

    data_before = fingerprint_dir(data)
    checker = Checker(candidate, data, task, allowed)
    failure: Failed | None = None
    try:
        checker.static()
        factory = checker.load()
        task_obj = checker.protocol(factory, {c["sample_id"] for c in cases})
        with tempfile.TemporaryDirectory(dir=candidate.parent) as tmp:
            checker.score_cases(task_obj, cases, Path(tmp))
    except Failed as exc:
        failure = exc
    data_after = fingerprint_dir(data)
    if data_after != data_before:
        # overrides any failure it may have caused, e.g. a protocol error from emptied data
        if failure is not None:
            checker.checks.append(
                {"name": failure.check, "status": "failed", "detail": failure.detail}
            )
        failure = Failed(PROTECTED_VIOLATION, "data", "the candidate changed its copy of the data")
    if failure is not None:
        checker.checks.append({"name": failure.check, "status": "failed", "detail": failure.detail})
    return {
        "checker_version": CHECKER_VERSION,
        "passed": failure is None,
        "category": failure.category if failure else None,
        "checks": checker.checks,
        "cases": checker.cases,
        "data_sha256_before": data_before,
        "data_sha256_after": data_after,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--allowed-imports", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = json.loads(sys.stdin.read())
    report = run_checks(
        args.candidate, args.data, args.task, json.loads(args.allowed_imports), payload["cases"]
    )
    # The validator only trusts a report carrying the nonce it sent on stdin.
    report["nonce"] = payload["nonce"]
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
