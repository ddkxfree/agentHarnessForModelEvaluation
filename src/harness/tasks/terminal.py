"""Terminal tasks: an agent works in a container, then a test script checks the container state.

Each task is a directory, following the layout used by Terminal-Bench / Harbor:

    <task-id>/
      instruction.md          what the agent is asked to do
      task.toml               metadata and verifier timeout
      environment/Dockerfile  the container the agent starts in
      tests/test.sh           exit code 0 means solved; copied in only after the agent stops
      solution/solve.sh       reference solution, used by the oracle solver
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from harness.core.fingerprint import fingerprint, fingerprint_dir
from harness.core.solver import Solver
from harness.core.task import DatasetSpec, Task, register, select
from harness.core.types import Sample, Score
from harness.sandbox.base import Sandbox, SandboxSpec
from harness.solvers.basic_agent import BasicAgent, agent_only, observation_limit
from harness.solvers.oracle import OracleSolver

DATA_DIR = Path(__file__).parent / "terminal_data"


def load_task_dir(task_dir: Path) -> Sample:
    meta = tomllib.loads((task_dir / "task.toml").read_text())
    return Sample(
        id=f"terminal/{task_dir.name}",
        input=(task_dir / "instruction.md").read_text().strip(),
        target="tests pass",
        metadata={
            "task_dir": str(task_dir),
            "difficulty": meta.get("metadata", {}).get("difficulty"),
            "category": meta.get("metadata", {}).get("category"),
            "verifier_timeout": meta.get("verifier", {}).get("timeout_sec", 60),
            "environment_fingerprint": fingerprint_dir(task_dir / "environment"),
            "task_fingerprint": fingerprint_dir(task_dir),
        },
    )


def discover(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if (p / "task.toml").exists())


class TestScriptScorer:
    """Copies the task's tests into the finished container and runs ``tests/test.sh``."""

    name = "test_script"
    requires_sandbox = True

    async def __call__(self, sample: Sample, text: str, sandbox: Sandbox) -> Score:
        tests = Path(sample.metadata["task_dir"]) / "tests"
        await sandbox.copy_dir(tests, "/tests")
        timeout = float(sample.metadata.get("verifier_timeout", 60))
        res = await sandbox.exec("bash /tests/test.sh", timeout=timeout)
        return Score(
            value=float(res.exit_code == 0 and not res.timed_out),
            extracted="timeout" if res.timed_out else f"exit_code={res.exit_code}",
            method=self.name,
            details={"test_output": res.combined(2000)},
        )

    def describe(self) -> dict[str, Any]:
        desc = {"name": self.name, "script": "tests/test.sh"}
        desc["fingerprint"] = fingerprint(desc)
        return desc


def sandbox_spec(sample: Sample) -> SandboxSpec:
    return SandboxSpec(build_context=Path(sample.metadata["task_dir"]) / "environment")


@register("terminal")
def terminal(
    limit: int | None = None,
    seed: int | None = None,
    ids: list[str] | None = None,
    solver: str = "agent",
    max_steps: int = 20,
    time_limit: float | None = 900.0,
    command_timeout: float = 60.0,
    root: str | None = None,
    token_limit: int | None = None,
    max_observation_chars: int | None = None,
) -> Task:
    data_dir = Path(root) if root else DATA_DIR
    dataset = DatasetSpec(
        path=root or "builtin:terminal_data",
        split="all",
        revision=fingerprint_dir(data_dir),
    )

    def load() -> list[Sample]:
        samples = [load_task_dir(d) for d in discover(data_dir)]
        if ids:
            samples = [s for s in samples if s.id.removeprefix("terminal/") in ids]
        return select(samples, limit, seed)

    agent_only(solver, max_observation_chars=max_observation_chars)
    chosen: Solver
    if solver == "agent":
        chosen = BasicAgent(
            max_steps=max_steps,
            command_timeout=command_timeout,
            **observation_limit(max_observation_chars),
        )
    elif solver == "mini":
        from harness.solvers.mini_swe_agent import MiniSweAgent

        chosen = MiniSweAgent(
            config="mini", max_steps=max_steps, command_timeout=command_timeout, cwd="/app"
        )
    elif solver == "oracle":
        chosen = OracleSolver()
    else:
        raise ValueError(f"unknown solver {solver!r}; use 'agent', 'mini' or 'oracle'")

    return Task(
        name="terminal",
        dataset=dataset,
        load=load,
        solver=chosen,
        scorer=TestScriptScorer(),
        params={
            "limit": limit,
            "seed": seed,
            "ids": ids,
            "solver": solver,
            "max_steps": max_steps,
            "command_timeout": command_timeout,
            "time_limit": time_limit,
            "root": root,
            "token_limit": token_limit,
            "max_observation_chars": max_observation_chars,
        },
        sandbox=sandbox_spec,
        time_limit=time_limit,
        token_limit=token_limit,
    )
