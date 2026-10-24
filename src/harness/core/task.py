from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from harness.core.scorer import SandboxScorer, Scorer, SubmissionScorer
from harness.core.solver import Solver
from harness.core.types import Sample
from harness.sandbox.base import SandboxSpec


class DatasetSpec(BaseModel):
    path: str
    config: str | None = None
    split: str
    revision: str | None = None


@dataclass
class Task:
    name: str
    dataset: DatasetSpec
    load: Callable[[], list[Sample]]
    solver: Solver
    scorer: Scorer | SandboxScorer | SubmissionScorer
    params: dict[str, Any] = field(default_factory=dict)
    # When set, every sample gets its own sandbox built from this spec.
    sandbox: Callable[[Sample], SandboxSpec] | None = None
    # Wall-clock budget in seconds for the solver on one sample. When it runs out the solver is
    # cancelled, the work so far is scored and an unsolved sample is reported as time_limit.
    time_limit: float | None = None
    # Token budget (input + output, over all model calls) for the solver on one sample. A call
    # is refused once the budget is used up; the work so far is scored and an unsolved sample
    # is reported as token_limit. The last allowed call may overshoot the budget; while a budget
    # is set, a solver's concurrent calls run one at a time so only one call can overshoot. It
    # counts the tokens the server reports, so it is not a strict cost cap.
    token_limit: int | None = None


TaskFactory = Callable[..., Task]
TASKS: dict[str, TaskFactory] = {}


def register(name: str) -> Callable[[TaskFactory], TaskFactory]:
    def deco(fn: TaskFactory) -> TaskFactory:
        if name in TASKS:
            raise ValueError(f"Task {name!r} is already registered")
        TASKS[name] = fn
        return fn

    return deco


def get_task(name: str, **params: Any) -> Task:
    import harness.tasks  # noqa: F401  (populates the registry)

    if name not in TASKS:
        raise KeyError(f"Unknown task {name!r}. Known: {', '.join(sorted(TASKS))}")
    return TASKS[name](**params)


def select(samples: list[Sample], limit: int | None, seed: int | None) -> list[Sample]:
    """Pick a deterministic subset. Returned samples keep their original order."""
    if limit is None or limit >= len(samples):
        return samples
    if seed is None:
        return samples[:limit]
    idx = sorted(random.Random(seed).sample(range(len(samples)), limit))
    return [samples[i] for i in idx]
