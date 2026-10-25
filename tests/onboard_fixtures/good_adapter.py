from __future__ import annotations

import csv
import unicodedata
from pathlib import Path

from harness.core.fingerprint import fingerprint, fingerprint_dir
from harness.core.solver import PromptSolver
from harness.core.task import DatasetSpec, Task, register, select
from harness.core.types import Sample, Score

TEMPLATE = "What is the capital of {country}? Answer with the city name only."
DEFAULT_DATA = Path(__file__).parent / "world_capitals_data"


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower().strip()
    if text.endswith("."):
        text = text[:-1]
    return text.strip()


class CapitalScorer:
    name = "capital_exact_match"

    def __call__(self, sample: Sample, text: str) -> Score:
        answers = [sample.target, *sample.metadata["alt_names"]]
        got = normalize(text)
        ok = bool(got) and got in {normalize(a) for a in answers}
        return Score(value=1.0 if ok else 0.0, extracted=got, method="normalized_exact")

    def describe(self) -> dict:
        desc = {"name": self.name, "normalization": "nfkd_strip_marks_lower_strip_period_v1"}
        desc["fingerprint"] = fingerprint(desc)
        return desc


def load_samples(data_dir: Path) -> list[Sample]:
    with open(data_dir / "capitals.tsv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    return [
        Sample(
            id=f"world-capitals/{r['qid']}",
            input=TEMPLATE.format(country=r["country"]),
            target=r["capital"],
            metadata={"alt_names": [a for a in (r["alt_names"] or "").split("|") if a]},
        )
        for r in rows
    ]


@register("world-capitals")
def world_capitals(
    data_dir: str | None = None, limit: int | None = None, seed: int | None = None
) -> Task:
    root = Path(data_dir) if data_dir else DEFAULT_DATA
    return Task(
        name="world-capitals",
        dataset=DatasetSpec(path="world-capitals", split="test", revision=fingerprint_dir(root)),
        load=lambda: select(load_samples(root), limit, seed),
        solver=PromptSolver(lambda s: s.input, template_text=TEMPLATE),
        scorer=CapitalScorer(),
        params={"limit": limit, "seed": seed},
    )
