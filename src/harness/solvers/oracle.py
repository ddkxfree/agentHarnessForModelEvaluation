from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.core.fingerprint import fingerprint
from harness.core.solver import Generate
from harness.core.types import ModelOutput, Sample, SolveResult
from harness.sandbox.base import Sandbox, SandboxError


class OracleSolver:
    """Runs the task's reference solution instead of a model.

    Every task should score 1.0 under the oracle; a task that does not is broken
    (bad environment, wrong tests or wrong solution), not hard.
    """

    name = "oracle"

    def __init__(self, timeout: float = 300.0) -> None:
        self.timeout = timeout

    async def __call__(
        self, sample: Sample, generate: Generate, sandbox: Sandbox | None = None
    ) -> SolveResult:
        if sandbox is None:
            raise SandboxError("oracle solver needs a sandbox")
        solution = Path(sample.metadata["task_dir"]) / "solution"
        await sandbox.copy_dir(solution, "/solution")
        res = await sandbox.exec("bash /solution/solve.sh", timeout=self.timeout)
        text = f"exit_code={res.exit_code}\n{res.combined(4000)}"
        return SolveResult(messages=[], output=ModelOutput(text=text, finish_reason="oracle"))

    def describe(self) -> dict[str, Any]:
        desc = {"name": self.name, "timeout": self.timeout}
        desc["fingerprint"] = fingerprint(desc)
        return desc

    def protocol(self) -> dict[str, Any]:
        return REFERENCE_PROTOCOL


REFERENCE_PROTOCOL: dict[str, Any] = {
    "interface": "reference_solution",
    "tools": [],
    "finish": "the reference solution ran",
    "step": "no model calls",
    "requires": ["sandbox"],
}
