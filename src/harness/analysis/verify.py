"""Re-grade saved submissions in fresh sandboxes, without calling the model.

For tasks whose scorer is a ``SubmissionScorer`` (e.g. SWE-bench), every run saves the exact
submission as an artifact. ``verify_run`` checks its hash, grades it again in a new sandbox
built from the task's current spec and reports whether the grade matches the recorded one.
With ``update=True`` the new grades replace the recorded ones, like ``harness rescore``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from harness.analysis import environment
from harness.analysis.rescore import apply_rescore, reached_scoring
from harness.analysis.stats import summarize
from harness.core.io import read_json, read_results, write_json, write_results
from harness.core.manifest import now
from harness.core.runner import close_sandbox, sample_dirname
from harness.core.scorer import SUBMISSION_ARTIFACT, check_score, grades_in_fresh_sandbox
from harness.core.task import Task, get_task
from harness.core.trajectory import Trajectory, recording
from harness.core.types import SampleResult, Score
from harness.sandbox.base import SandboxError, SandboxProvider


def _brief(score: Score | None) -> dict[str, Any] | None:
    return None if score is None else {"value": score.value, "extracted": score.extracted}


async def _verify_sample(
    task: Task,
    r: SampleResult,
    provider: SandboxProvider,
    task_dir: Path,
) -> tuple[dict[str, Any], Score | Exception | None]:
    row: dict[str, Any] = {"sample_id": r.sample.id, "recorded": _brief(r.score)}
    sub = next((a for a in r.artifacts if a.name == SUBMISSION_ARTIFACT), None)
    if sub is None or sub.path is None:
        row["status"] = "no_submission"
        return row, None
    path = task_dir / "artifacts" / sub.path
    if not path.exists():
        row["status"] = "submission_missing"
        return row, None
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sub.sha256:
        row["status"] = "submission_hash_mismatch"
        return row, None

    trajectory = Trajectory(task_dir / "verify", sample_dirname(r.sample.id))
    labels = {"harness.task": task.name, "harness.sample": r.sample.id, "harness.phase": "verify"}
    assert task.sandbox is not None
    new: Score | Exception
    grader = None
    try:
        with recording(trajectory):
            grader = await provider.create(task.sandbox(r.sample), labels=labels)
            row["grading_sandbox"] = grader.describe()
            # A matching grade in a different image is not a reproduction of the original.
            row["environment_changes"] = environment.changes(
                environment.identity(r.grading_sandbox or r.sandbox),
                environment.identity(row["grading_sandbox"]),
            )
            new = check_score(await task.scorer.grade(r.sample, data.decode(), grader))
    except SandboxError as exc:
        row["status"], row["error"] = "env_error", f"{type(exc).__name__}: {exc}"
        return row, None
    except Exception as exc:
        new = exc
    finally:
        if grader is not None and (error := await close_sandbox(grader, r.sample.id)):
            row["cleanup_error"] = error
    row["artifacts"] = [a.model_dump() for a in trajectory.artifacts]
    if isinstance(new, Exception):
        row["status"], row["error"] = "scorer_error", f"{type(new).__name__}: {new}"
    else:
        row["verified"] = _brief(new)
        if r.score is None:
            row["status"] = "newly_graded"
        else:
            row["status"] = "agree" if row["verified"] == row["recorded"] else "disagree"
    return row, new


async def verify_run(
    run_dir: Path,
    provider: SandboxProvider,
    *,
    tasks: list[str] | None = None,
    sample_ids: list[str] | None = None,
    concurrency: int = 2,
    update: bool = False,
) -> dict[str, Any]:
    """Write ``<task>/verify.jsonl`` per task and ``verify.json``; return the per-task report."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    at = now()
    report: dict[str, Any] = {}
    sem = asyncio.Semaphore(concurrency)

    for entry in manifest["tasks"]:
        name = entry["name"]
        task_dir = run_dir / name
        if (tasks and name not in tasks) or not (task_dir / "samples.jsonl").exists():
            continue
        task = get_task(name, **entry["params"])
        if not grades_in_fresh_sandbox(task.scorer):
            report[name] = {"skipped": "grading needs the original sandbox state"}
            continue
        results = read_results(task_dir / "samples.jsonl")
        chosen = [r for r in results if sample_ids is None or r.sample.id in sample_ids]

        async def one(r: SampleResult, task: Task = task, task_dir: Path = task_dir) -> Any:
            async with sem:
                return await _verify_sample(task, r, provider, task_dir)

        outcomes = await asyncio.gather(*(one(r) for r in chosen))
        rows = [row for row, _ in outcomes]
        with open(task_dir / "verify.jsonl", "w") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

        recorded_fp = entry["scorer"].get("fingerprint")
        current = task.scorer.describe()
        report[name] = {
            "n": len(rows),
            "statuses": dict(Counter(row["status"] for row in rows).most_common()),
            "disagreements": [row["sample_id"] for row in rows if row["status"] == "disagree"],
            "environment_changed": [
                row["sample_id"] for row in rows if row.get("environment_changes")
            ],
            "scorer_fingerprint": {"recorded": recorded_fp, "current": current["fingerprint"]},
        }
        if update:
            changed = sum(
                apply_rescore(r, new, at=at, from_fingerprint=recorded_fp)
                for r, (_, new) in zip(chosen, outcomes, strict=True)
                if new is not None and reached_scoring(r)
            )
            summary = summarize(results)
            write_results(task_dir / "samples.jsonl", results)
            write_json(task_dir / "summary.json", summary)
            manifest["results"][name] = summary
            entry["scorer"] = current
            entry.setdefault("rescore_history", []).append(
                {
                    "at": at,
                    "kind": "verify",
                    "from_fingerprint": recorded_fp,
                    "to_fingerprint": current["fingerprint"],
                }
            )
            report[name]["samples_changed"] = changed
            write_json(manifest_path, manifest)

    write_json(
        run_dir / "verify.json",
        {"at": at, "sandbox_provider": provider.describe(), "update": update, "tasks": report},
    )
    return report
