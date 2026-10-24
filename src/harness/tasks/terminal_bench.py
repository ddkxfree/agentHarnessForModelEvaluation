"""Terminal-Bench 2.0: real terminal tasks in the Harbor format, run like Harbor runs them.

Task files come from https://github.com/laude-institute/terminal-bench-2 at a pinned commit.
They are downloaded on first use into a local cache and must match pinned content
fingerprints; each task's prebuilt image is pinned by registry digest. The benchmark asks
that its data not be redistributed into training corpora, so the files are not vendored.

The flow follows Harbor (``verifier/verifier.py`` and ``agents/oracle.py``):

* the agent starts in the image's WORKDIR with ``instruction.md`` as its task;
* afterwards ``tests/`` is uploaded to ``/tests`` and ``/tests/test.sh`` runs with its output
  in ``/logs/verifier/test-stdout.txt``;
* the score is the finite number in ``/logs/verifier/reward.json`` (key ``reward``) or else
  ``reward.txt``; no reward file is a verifier failure, not a wrong answer;
* the oracle uploads ``solution/`` to ``/solution`` and runs ``solve.sh``.

Known deviations are listed in ``DEVIATIONS`` and recorded in the scorer's fingerprint.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harness.core.fingerprint import fingerprint, fingerprint_dir
from harness.core.solver import Generate, Solver
from harness.core.task import DatasetSpec, Task, register, select
from harness.core.trajectory import current_trajectory
from harness.core.types import ModelOutput, Sample, Score, SolveResult
from harness.sandbox.base import Sandbox, SandboxError, SandboxSpec
from harness.solvers.basic_agent import BasicAgent, agent_only, observation_limit
from harness.solvers.oracle import REFERENCE_PROTOCOL

REPO = "laude-institute/terminal-bench-2"
COMMIT = "2fd12b88aafdd04a52c298e3940bcb189f9766d6"


@dataclass(frozen=True)
class Pin:
    image: str
    fingerprint: str


# Chosen to be quick to verify (small images, light verifier) while covering different
# categories: git, log analysis, regex, security tooling and binary data recovery.
PINS: dict[str, Pin] = {
    "fix-git": Pin(
        "alexgshaw/fix-git@sha256:61e431c00c58df652287aadce5457634d9f9330cfdd153ebdf2802df0d540119",
        "1b12832fdb57503e",
    ),
    "log-summary-date-ranges": Pin(
        "alexgshaw/log-summary-date-ranges@sha256:"
        "cbeb6ba905c2fec294f16cd5e16e3ea7f2e04d38ac2484d51a11de262aa7dc51",
        "ba620079eb149d4c",
    ),
    "regex-log": Pin(
        "alexgshaw/regex-log@sha256:"
        "90101b2e815323a8da20528a1439bebc407eb9761c9c68a3d557730856c878e9",
        "5726420883fa170b",
    ),
    "openssl-selfsigned-cert": Pin(
        "alexgshaw/openssl-selfsigned-cert@sha256:"
        "4c948a4e630af2435ae0a19108fc0814a946ac2fa29a512469e0fc77b38c8c12",
        "c9ef93da6532aa9b",
    ),
    "sqlite-db-truncate": Pin(
        "alexgshaw/sqlite-db-truncate@sha256:"
        "aabac93c93bd1f310e6a6fb893911d7735026ed18491c72133c9196a09092ca4",
        "953079359d19534e",
    ),
}

DEVIATIONS = [
    "commands run with `bash -lc` instead of Harbor's `bash -c`",
    "/logs/agent and /logs/verifier are created in the container before verifying, "
    "not mounted from the host for the whole trial",
    "the agent runs as a harness Solver (basic_agent or mini-swe-agent), not a Harbor agent",
    "storage_mb and gpus from task.toml are not enforced",
    "a verifier timeout scores 0 instead of failing the trial",
]


def cache_root() -> Path:
    base = os.environ.get("HARNESS_CACHE_DIR") or Path.home() / ".cache" / "harness"
    return Path(base) / "terminal-bench-2" / COMMIT


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


def download_task(name: str, dest: Path) -> None:
    """Fetch one task directory at the pinned commit into ``dest``."""
    tree = json.loads(_get(f"https://api.github.com/repos/{REPO}/git/trees/{COMMIT}?recursive=1"))
    files = [e["path"] for e in tree["tree"] if e["type"] == "blob"]
    files = [p for p in files if p.startswith(f"{name}/")]
    if not files:
        raise KeyError(f"{name!r} not found in {REPO}@{COMMIT}")
    for path in files:
        target = dest / Path(path).relative_to(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_get(f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{path}"))


def ensure_task(name: str, root: Path | None = None) -> Path:
    """Return the verified local copy of a pinned task, downloading it if needed."""
    pin = PINS[name]
    task_dir = (root or cache_root()) / name
    if not task_dir.exists():
        task_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=task_dir.parent) as tmp:
            download_task(name, Path(tmp) / name)
            if not task_dir.exists():
                (Path(tmp) / name).rename(task_dir)
    actual = fingerprint_dir(task_dir)
    if actual != pin.fingerprint:
        raise ValueError(
            f"{task_dir} has fingerprint {actual}, expected {pin.fingerprint}; "
            f"delete it to download {REPO}@{COMMIT} again"
        )
    return task_dir


def _workdir(dockerfile: Path) -> str:
    workdir = "/"
    for line in dockerfile.read_text().splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[0].upper() == "WORKDIR":
            workdir = parts[1] if parts[1].startswith("/") else f"{workdir.rstrip('/')}/{parts[1]}"
    return workdir


def load_task(task_dir: Path, image: str) -> Sample:
    meta = tomllib.loads((task_dir / "task.toml").read_text())
    env = meta.get("environment", {})
    return Sample(
        id=f"terminal-bench/{task_dir.name}",
        input=(task_dir / "instruction.md").read_text(),
        target="reward 1",
        metadata={
            "task_dir": str(task_dir),
            "image": image,
            "workdir": _workdir(task_dir / "environment" / "Dockerfile"),
            "difficulty": meta.get("metadata", {}).get("difficulty"),
            "category": meta.get("metadata", {}).get("category"),
            "agent_timeout": meta.get("agent", {}).get("timeout_sec"),
            "verifier_timeout": meta.get("verifier", {}).get("timeout_sec", 900.0),
            "cpus": env.get("cpus", 1),
            "memory_mb": env.get("memory_mb", 2048),
            "allow_internet": env.get("allow_internet", True),
            "task_fingerprint": fingerprint_dir(task_dir),
        },
    )


def sandbox_spec(sample: Sample) -> SandboxSpec:
    m = sample.metadata
    return SandboxSpec(
        image=m["image"],
        workdir=m["workdir"],
        network=bool(m["allow_internet"]),
        memory=f"{int(m['memory_mb'])}m",
        cpus=float(m["cpus"]),
    )


async def _run(sandbox: Sandbox, cmd: str, timeout: float = 60) -> None:
    res = await sandbox.exec(cmd, timeout=timeout)
    if res.exit_code != 0:
        raise SandboxError(f"`{cmd}` failed: {res.combined(1000)}")


async def _read(sandbox: Sandbox, path: str) -> str | None:
    try:
        return (await sandbox.read_file(path)).decode(errors="replace")
    except FileNotFoundError:
        return None


def parse_reward(reward_json: str | None, reward_txt: str | None) -> float:
    """Harbor's rule: reward.json wins over reward.txt; the value must be a finite number."""
    if reward_json is not None:
        data = json.loads(reward_json)
        value = data.get("reward") if isinstance(data, dict) else data
    elif reward_txt is not None:
        value = float(reward_txt)
    else:
        raise ValueError("the verifier wrote no reward file")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"invalid reward {value!r}")
    return float(value)


class HarborVerifierScorer:
    """Runs the task's ``tests/test.sh`` in the finished container and reads the reward file."""

    name = "harbor_verifier"
    requires_sandbox = True

    async def __call__(self, sample: Sample, text: str, sandbox: Sandbox) -> Score:
        await _run(
            sandbox,
            "mkdir -p /logs/agent /logs/verifier"
            " && rm -f /logs/verifier/reward.txt /logs/verifier/reward.json",
        )
        await sandbox.copy_dir(Path(sample.metadata["task_dir"]) / "tests", "/tests")
        await _run(sandbox, "chmod +x /tests/test.sh")
        res = await sandbox.exec(
            "/tests/test.sh > /logs/verifier/test-stdout.txt 2>&1",
            timeout=float(sample.metadata["verifier_timeout"]),
        )
        stdout = await _read(sandbox, "/logs/verifier/test-stdout.txt") or ""
        artifact = current_trajectory().save_artifact("test-stdout.txt", stdout)
        details = {"test_stdout_tail": stdout[-2000:], "test_stdout_sha256": artifact.sha256}
        if res.timed_out:
            return Score(value=0.0, extracted="timeout", method=self.name, details=details)
        reward = parse_reward(
            await _read(sandbox, "/logs/verifier/reward.json"),
            await _read(sandbox, "/logs/verifier/reward.txt"),
        )
        return Score(
            value=reward, extracted=f"reward={reward:g}", method=self.name, details=details
        )

    def describe(self) -> dict[str, Any]:
        desc = {
            "name": self.name,
            "script": "/tests/test.sh",
            "reward_files": ["/logs/verifier/reward.json", "/logs/verifier/reward.txt"],
            "deviations_from_harbor": DEVIATIONS,
        }
        desc["fingerprint"] = fingerprint(desc)
        return desc


class HarborOracleSolver:
    """Harbor's oracle: upload ``solution/`` to ``/solution`` and run ``solve.sh``."""

    name = "harbor_oracle"

    async def __call__(
        self, sample: Sample, generate: Generate, sandbox: Sandbox | None = None
    ) -> SolveResult:
        if sandbox is None:
            raise SandboxError("the oracle needs a sandbox")
        await _run(sandbox, "mkdir -p /logs/agent")
        await sandbox.copy_dir(Path(sample.metadata["task_dir"]) / "solution", "/solution")
        await _run(sandbox, "chmod +x /solution/solve.sh")
        timeout = float(sample.metadata.get("agent_timeout") or 900)
        res = await sandbox.exec(
            "/solution/solve.sh > /logs/agent/oracle.txt 2>&1", timeout=timeout
        )
        log = await _read(sandbox, "/logs/agent/oracle.txt") or ""
        current_trajectory().save_artifact("oracle.txt", log)
        return SolveResult(
            messages=[],
            output=ModelOutput(text=f"exit_code={res.exit_code}", finish_reason="oracle"),
        )

    def describe(self) -> dict[str, Any]:
        desc = {"name": self.name, "script": "/solution/solve.sh"}
        desc["fingerprint"] = fingerprint(desc)
        return desc

    def protocol(self) -> dict[str, Any]:
        return REFERENCE_PROTOCOL


@register("terminal-bench")
def terminal_bench(
    limit: int | None = None,
    seed: int | None = None,
    ids: list[str] | None = None,
    solver: str = "agent",
    max_steps: int = 30,
    time_limit: float | None = 900.0,
    command_timeout: float = 120.0,
    root: str | None = None,
    token_limit: int | None = None,
    max_observation_chars: int | None = None,
) -> Task:
    names = list(ids) if ids else list(PINS)
    unknown = sorted(set(names) - set(PINS))
    if unknown:
        raise KeyError(f"tasks not pinned: {unknown}; pinned: {sorted(PINS)}")
    dataset = DatasetSpec(
        path=f"github:{REPO}",
        split="pinned:" + ",".join(sorted(PINS)),
        revision=COMMIT,
    )

    def load() -> list[Sample]:
        base = Path(root) if root else None
        samples = [load_task(ensure_task(n, base), PINS[n].image) for n in sorted(names)]
        return select(samples, limit, seed)

    agent_only(solver, max_observation_chars=max_observation_chars)
    chosen: Solver
    if solver == "agent":
        chosen = BasicAgent(
            workdir=None,
            max_steps=max_steps,
            command_timeout=command_timeout,
            **observation_limit(max_observation_chars),
        )
    elif solver == "mini":
        from harness.solvers.mini_swe_agent import MiniSweAgent

        chosen = MiniSweAgent(
            config="mini", max_steps=max_steps, command_timeout=command_timeout, cwd=""
        )
    elif solver == "oracle":
        chosen = HarborOracleSolver()
    else:
        raise ValueError(f"unknown solver {solver!r}; use 'agent', 'mini' or 'oracle'")

    return Task(
        name="terminal-bench",
        dataset=dataset,
        load=load,
        solver=chosen,
        scorer=HarborVerifierScorer(),
        params={
            "limit": limit,
            "seed": seed,
            "ids": ids,
            "solver": solver,
            "max_steps": max_steps,
            "time_limit": time_limit,
            "command_timeout": command_timeout,
            "root": root,
            "token_limit": token_limit,
            "max_observation_chars": max_observation_chars,
        },
        sandbox=sandbox_spec,
        time_limit=time_limit,
        token_limit=token_limit,
    )
