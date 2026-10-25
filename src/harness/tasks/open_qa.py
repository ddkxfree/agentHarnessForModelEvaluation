"""Open-ended technical questions graded by judge models.

A small frozen set (``open_qa_data/``, pinned by content fingerprint) for checking the judge
pipeline end to end, not a benchmark. ``mode=rubric`` grades each answer against a fixed
rubric; ``mode=pairwise`` compares it with the reference answer in both orders.

    harness run -m <model> -t open-qa -p open-qa.judges='[openai/gpt-4o-mini]'
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.core.fingerprint import fingerprint_dir
from harness.core.judge import JudgeSpec
from harness.core.solver import PromptSolver
from harness.core.task import DatasetSpec, Task, register, select
from harness.core.types import GenerateConfig, Sample
from harness.judge import Criterion, PairwiseJudge, Rubric, RubricJudge

DATA_DIR = Path(__file__).parent / "open_qa_data"

RUBRIC = Rubric(
    criteria=[
        Criterion(
            name="correctness",
            description="The technical claims are right; nothing important is wrong.",
            max_points=4,
        ),
        Criterion(
            name="completeness",
            description="Every part of the question is answered, as in the reference answer.",
            max_points=3,
        ),
        Criterion(
            name="clarity",
            description="The answer is clear and to the point; length alone earns nothing.",
            max_points=2,
        ),
    ],
    instructions="The reference answer shows what a full answer covers; other correct wording "
    "or additional correct detail is fine.",
)


def load_samples(data_dir: Path = DATA_DIR) -> list[Sample]:
    rows = [json.loads(line) for line in (data_dir / "samples.jsonl").read_text().splitlines()]
    return [
        Sample(id=f"open-qa/{r['id']}", input=r["question"], target=r["reference"]) for r in rows
    ]


def judge_specs(judges: list[str] | str, max_tokens: int) -> list[JudgeSpec]:
    models = [judges] if isinstance(judges, str) else list(judges)
    if not models:
        raise ValueError(
            "open-qa needs at least one judge, e.g. -p open-qa.judges='[provider/model]'"
        )
    config = GenerateConfig(temperature=0.0, max_tokens=max_tokens)
    return [JudgeSpec(name=f"j{i}", model=m, generate=config) for i, m in enumerate(models, 1)]


@register("open-qa")
def open_qa(
    limit: int | None = None,
    seed: int | None = None,
    ids: list[str] | None = None,
    mode: str = "rubric",
    judges: list[str] | str | None = None,
    judge_max_tokens: int = 1024,
    disagreement_threshold: float = 0.25,
) -> Task:
    specs = judge_specs(judges or [], judge_max_tokens)
    if mode == "rubric":
        scorer: RubricJudge | PairwiseJudge = RubricJudge(RUBRIC, specs, disagreement_threshold)
    elif mode == "pairwise":
        scorer = PairwiseJudge(specs)
    else:
        raise ValueError(f"unknown mode {mode!r}; use 'rubric' or 'pairwise'")

    def load() -> list[Sample]:
        samples = load_samples()
        if ids:
            samples = [s for s in samples if s.id.removeprefix("open-qa/") in ids]
        return select(samples, limit, seed)

    return Task(
        name="open-qa",
        dataset=DatasetSpec(
            path="builtin:open_qa_data", split="all", revision=fingerprint_dir(DATA_DIR)
        ),
        load=load,
        solver=PromptSolver(lambda s: s.input, template_text="{input}"),
        scorer=scorer,
        params={
            "limit": limit,
            "seed": seed,
            "ids": ids,
            "mode": mode,
            "judges": judges,
            "judge_max_tokens": judge_max_tokens,
            "disagreement_threshold": disagreement_threshold,
        },
    )
