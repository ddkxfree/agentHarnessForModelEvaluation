"""SWE-bench: fix a real GitHub issue in a real repository, judged by the repository's tests.

Each instance runs in the official prebuilt image (``swebench/sweb.eval.x86_64.*``), where
the repository is checked out at ``/testbed``. After the solver stops, the scorer

1. takes the model patch (the solver's output if it is a diff, else ``git diff`` of /testbed),
2. starts a fresh container, resets /testbed to the base commit and applies that patch,
3. runs the official eval script (checks out test files, applies the hidden test patch,
   runs FAIL_TO_PASS and PASS_TO_PASS tests), and
4. grades the log with ``swebench.harness.grading.get_eval_report``.

Steps 2-4 run in a fresh container from the same image, as in the official harness, so
changes the agent made outside /testbed (e.g. installed packages) do not leak into grading.
Needs the optional dependency: ``uv sync --extra swebench``.
"""

from __future__ import annotations

import functools
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from harness.core.fingerprint import fingerprint
from harness.core.solver import Generate, Solver
from harness.core.task import DatasetSpec, Task, register, select
from harness.core.trajectory import current_trajectory
from harness.core.types import ModelOutput, Sample, Score, SolveResult
from harness.sandbox.base import Sandbox, SandboxError, SandboxSpec
from harness.solvers.basic_agent import BasicAgent, agent_only, observation_limit
from harness.solvers.oracle import REFERENCE_PROTOCOL

DATASET = DatasetSpec(
    path="SWE-bench/SWE-bench_Lite",
    split="test",
    revision="b0dde1093fe417d83b7184254edf8199c1f0dff5",
)
WORKDIR = "/testbed"

INSTRUCTIONS = """\
<issue>
{problem_statement}
</issue>

The repository is checked out at /testbed. Change the non-test source files so that the issue
above is resolved. Do not modify or add tests; hidden tests will be run against your changes.
When you are done, call submit. Your uncommitted changes in /testbed are collected as a git diff."""


def require_swebench() -> None:
    try:
        import swebench  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "the swebench task needs the optional dependency: uv sync --extra swebench"
        ) from exc


@functools.cache
def _rows() -> dict[str, dict[str, Any]]:
    from harness.tasks._hf import load_rows

    return {row["instance_id"]: row for row in load_rows(DATASET)}


def instance(sample: Sample) -> dict[str, Any]:
    return _rows()[sample.metadata["instance_id"]]


def to_sample(row: dict[str, Any]) -> Sample:
    return Sample(
        id=f"swebench/{row['instance_id']}",
        input=INSTRUCTIONS.format(problem_statement=row["problem_statement"].strip()),
        target="resolved",
        metadata={
            "instance_id": row["instance_id"],
            "repo": row["repo"],
            "version": row["version"],
            "base_commit": row["base_commit"],
            "difficulty": row.get("difficulty"),
            "image": row["image"],
            "n_fail_to_pass": len(row["FAIL_TO_PASS"]),
            "n_pass_to_pass": len(row["PASS_TO_PASS"]),
        },
    )


async def _git(sandbox: Sandbox, cmd: str, timeout: float = 120) -> str:
    res = await sandbox.exec(f"cd {WORKDIR} && {cmd}", timeout=timeout)
    if res.exit_code != 0:
        raise SandboxError(f"`{cmd}` failed: {res.combined(1000)}")
    return res.stdout


async def collect_patch(sandbox: Sandbox, text: str) -> tuple[str, str]:
    """Return ``(patch, source)``: the submitted diff if there is one, else the working tree."""
    if text.lstrip().startswith(("diff --git", "--- a/")):
        return text, "submission"
    patch = await _git(sandbox, "git add -A && git -c core.fileMode=false diff --cached HEAD")
    return patch, "git_diff"


async def apply_patch(sandbox: Sandbox, patch: str) -> tuple[bool, str]:
    """Reset /testbed to the base commit and apply ``patch``, like the official harness."""
    await _git(sandbox, "git reset -q --hard HEAD && git clean -fdq")
    await sandbox.write_file("/tmp/model.patch", patch if patch.endswith("\n") else patch + "\n")
    tried = []
    for cmd in (
        "git apply --verbose /tmp/model.patch",
        "git apply --verbose --reject /tmp/model.patch",
        "patch --batch --fuzz=5 -p1 -i /tmp/model.patch",
    ):
        res = await sandbox.exec(f"cd {WORKDIR} && {cmd}", timeout=120)
        tried.append(f"$ {cmd}\n{res.combined(1000)}")
        if res.exit_code == 0:
            return True, tried[-1]
        await _git(sandbox, "git reset -q --hard HEAD && git clean -fdq")
    return False, "\n".join(tried)


class SweBenchScorer:
    """Resolved (1.0) iff every FAIL_TO_PASS and PASS_TO_PASS test passes with the model patch.

    A ``SubmissionScorer``: the patch is collected from the agent's container and graded in a
    fresh container from the same image, as in the official harness. The complete patch and
    logs are saved as artifacts; ``details`` keeps their hashes and short tails.
    """

    name = "swebench"
    requires_sandbox = True
    fresh_sandbox = True

    def __init__(self, eval_timeout: float = 1800.0) -> None:
        self.eval_timeout = eval_timeout

    async def collect(self, sample: Sample, text: str, sandbox: Sandbox) -> str:
        patch, _ = await collect_patch(sandbox, text)
        return patch

    async def grade(self, sample: Sample, submission: str, sandbox: Sandbox) -> Score:
        from swebench.harness.grading import get_eval_report
        from swebench.harness.utils import make_test_spec

        trajectory = current_trajectory()
        row = instance(sample)
        patch = submission
        details: dict[str, Any] = {
            "model_patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
            "model_patch_bytes": len(patch.encode()),
        }
        if not patch.strip():
            return Score(value=0.0, extracted="empty_patch", method=self.name, details=details)

        applied, apply_log = await apply_patch(sandbox, patch)
        trajectory.save_artifact("apply.log", apply_log)
        details["apply_log_tail"] = apply_log[-2000:]
        if not applied:
            return Score(value=0.0, extracted="patch_failed", method=self.name, details=details)

        spec = make_test_spec(row)
        await sandbox.write_file("/eval.sh", spec.eval_script)
        # The eval script traces with `set -x`, so the log markers land on stderr; keep one stream.
        res = await sandbox.exec(
            "bash /eval.sh > /tmp/eval.log 2>&1; echo exit=$?", timeout=self.eval_timeout
        )
        log = (await sandbox.read_file("/tmp/eval.log")).decode(errors="replace")
        details["eval_log_sha256"] = trajectory.save_artifact("eval.log", log).sha256
        details["eval_log_tail"] = log[-3000:]
        if res.timed_out:
            return Score(value=0.0, extracted="timeout", method=self.name, details=details)

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "eval.log"
            log_path.write_text(log)
            prediction = {
                "instance_id": row["instance_id"],
                "model_name_or_path": "harness",
                "model_patch": patch,
            }
            report = get_eval_report(spec, prediction, str(log_path), True)[row["instance_id"]]

        if report.get("infra_failure"):
            reason = report.get("infra_failure_reason")
            raise SandboxError(f"evaluation infrastructure failed: {reason}")
        tests = report.get("tests_status", {})
        details["tests"] = {
            group: {k: len(v) for k, v in status.items()} for group, status in tests.items()
        }
        details["failed_tests"] = {
            group: status.get("failure", [])[:20] for group, status in tests.items()
        }
        resolved = bool(report.get("resolved"))
        if resolved:
            extracted = "resolved"
        elif not report.get("patch_successfully_applied"):
            extracted = "tests_not_run"
        else:
            extracted = "unresolved"
        return Score(value=float(resolved), extracted=extracted, method=self.name, details=details)

    def describe(self) -> dict[str, Any]:
        import swebench

        desc = {
            "name": self.name,
            "swebench_version": swebench.__version__,
            "eval_timeout": self.eval_timeout,
            "grading": "fresh_container",
        }
        desc["fingerprint"] = fingerprint(desc)
        return desc


class GoldPatchSolver:
    """Applies the reference fix. Every instance should be resolved; if not, the setup is broken."""

    name = "gold_patch"

    async def __call__(
        self, sample: Sample, generate: Generate, sandbox: Sandbox | None = None
    ) -> SolveResult:
        if sandbox is None:
            raise SandboxError("gold patch solver needs a sandbox")
        await sandbox.write_file("/tmp/gold.patch", instance(sample)["patch"])
        await _git(sandbox, "git apply --verbose /tmp/gold.patch")
        return SolveResult(messages=[], output=ModelOutput(text="", finish_reason="oracle"))

    def describe(self) -> dict[str, Any]:
        desc = {"name": self.name}
        desc["fingerprint"] = fingerprint(desc)
        return desc

    def protocol(self) -> dict[str, Any]:
        return REFERENCE_PROTOCOL


def sandbox_spec(sample: Sample) -> SandboxSpec:
    # The eval script runs `pip install -e .`, which may need the network.
    return SandboxSpec(
        image=sample.metadata["image"], workdir=WORKDIR, network=True, memory="4g", cpus=2.0
    )


@register("swebench")
def swebench(
    limit: int | None = None,
    seed: int | None = None,
    ids: list[str] | None = None,
    solver: str = "agent",
    max_steps: int = 30,
    time_limit: float | None = 3600.0,
    command_timeout: float = 120.0,
    eval_timeout: float = 1800.0,
    token_limit: int | None = None,
    max_observation_chars: int | None = None,
) -> Task:
    require_swebench()
    agent_only(solver, max_observation_chars=max_observation_chars)

    def load() -> list[Sample]:
        rows = _rows()
        if ids:
            missing = sorted(set(ids) - set(rows))
            if missing:
                raise KeyError(f"unknown SWE-bench Lite instance ids: {missing}")
            samples = [to_sample(rows[i]) for i in ids]
        else:
            samples = [to_sample(r) for r in rows.values()]
        return select(samples, limit, seed)

    chosen: Solver
    if solver == "agent":
        chosen = BasicAgent(
            workdir=WORKDIR,
            max_steps=max_steps,
            command_timeout=command_timeout,
            **observation_limit(max_observation_chars or 8000),
        )
    elif solver == "mini":
        from harness.solvers.mini_swe_agent import MiniSweAgent

        chosen = MiniSweAgent(
            config="swebench", max_steps=max_steps, command_timeout=command_timeout, cwd=WORKDIR
        )
    elif solver == "oracle":
        chosen = GoldPatchSolver()
    else:
        raise ValueError(f"unknown solver {solver!r}; use 'agent', 'mini' or 'oracle'")

    return Task(
        name="swebench",
        dataset=DATASET,
        load=load,
        solver=chosen,
        scorer=SweBenchScorer(eval_timeout=eval_timeout),
        params={
            "limit": limit,
            "seed": seed,
            "ids": ids,
            "solver": solver,
            "max_steps": max_steps,
            "command_timeout": command_timeout,
            "time_limit": time_limit,
            "eval_timeout": eval_timeout,
            "token_limit": token_limit,
            "max_observation_chars": max_observation_chars,
        },
        sandbox=sandbox_spec,
        time_limit=time_limit,
        token_limit=token_limit,
    )
