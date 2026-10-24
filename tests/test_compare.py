"""Controlled comparisons: spec validation, repeated runs and the offline report."""

import asyncio
import json

import pytest
from conftest import make_samples, question_number
from pydantic import ValidationError

from harness.analysis.compare import CompareSpec, build_report, run_comparison
from harness.core.io import read_json, read_results, write_json, write_results
from harness.core.model import MockModel
from harness.core.scorer import NumberMatchScorer
from harness.core.solver import PromptSolver
from harness.core.task import DatasetSpec, Task
from harness.core.types import ModelOutput, SampleResult, Step, Usage


def toy_factory(name, solver="plain", limit=None):
    """Two 'solvers' that differ only in their prompt; the model answers well when asked to
    be careful."""
    template = {"plain": "{input}", "careful": "Be careful. {input}"}[solver]
    samples = make_samples()[:limit]
    return Task(
        name=name,
        dataset=DatasetSpec(path="local/toy", split="test"),
        load=lambda: samples,
        solver=PromptSolver(lambda s: template.format(input=s.input), template_text=template),
        scorer=NumberMatchScorer(),
        params={"solver": solver, "limit": limit},
    )


def respond(messages):
    n = question_number([{"content": messages[-1]["content"].removeprefix("Be careful. ")}])
    careful = messages[-1]["content"].startswith("Be careful.")
    # the plain prompt gets only the even questions right
    right = careful or n % 2 == 0
    return ModelOutput(
        text=f"The answer is {2 * n if right else 2 * n + 1}",
        finish_reason="stop",
        usage=Usage(input_tokens=10, output_tokens=3 if careful else 2),
    )


def spec(tmp_path, **overrides):
    data = {
        "kind": "solver_comparison",
        "model": "mock/mock",
        "task": "toy",
        "params": {"limit": 4},
        "conditions": [
            {"label": "plain", "params": {"solver": "plain"}},
            {"label": "careful", "params": {"solver": "careful"}},
        ],
        "repeats": 2,
        "cache_dir": str(tmp_path / "cache"),
        "output_dir": str(tmp_path / "runs"),
    }
    data.update(overrides)
    return CompareSpec.model_validate(data)


def run(tmp_path, s, model=None):
    model = model or MockModel(respond)
    return asyncio.run(run_comparison(s, model, make_task=toy_factory)), model


def test_conditions_are_repeated_and_reported_side_by_side(tmp_path):
    compare_dir, model = run(tmp_path, spec(tmp_path))

    # 2 conditions x 2 repeats x 4 samples, nothing replayed from the cache
    assert model.calls == 16
    index = read_json(compare_dir / "index.json")
    assert [(r["label"], r["repeat"]) for r in index["runs"]] == [
        ("plain", 1),
        ("careful", 1),
        ("plain", 2),
        ("careful", 2),
    ]
    report = read_json(compare_dir / "compare.json")
    assert report["kind"] == "solver_comparison" and "whole agent" in report["note"]
    assert report["complete"] and report["same_samples_in_every_run"]
    assert report["cache_policy"] == "never"

    plain, careful = report["conditions"]["plain"], report["conditions"]["careful"]
    assert plain["accuracy"] == {"mean": 0.5, "min": 0.5, "max": 0.5}
    assert careful["accuracy"]["mean"] == 1.0
    assert (plain["mean_input_tokens"], plain["mean_output_tokens"]) == (40, 8)
    assert careful["mean_output_tokens"] == 12
    assert plain["outcomes"]["wrong"] == 4 and careful["outcomes"]["correct"] == 8
    assert plain["mean_wall_time_s"] is not None
    assert plain["repeat_config_changes"] == []

    assert report["per_sample_solved"]["toy/0"] == {"plain": "2/2", "careful": "2/2"}
    assert report["per_sample_solved"]["toy/1"] == {"plain": "0/2", "careful": "2/2"}

    changed = {k for k, _, _ in report["config_changes_vs_baseline"]["careful"]}
    assert "run_config.params.solver" in changed
    assert "tasks.toy.solver.fingerprint" in changed
    assert not any(k.startswith(("environment", "model", "tasks.toy.generate")) for k in changed)

    manifest = read_json(compare_dir / index["runs"][1]["run_dir"] / "manifest.json")
    assert manifest["run_config"]["compare"] == {"label": "careful", "repeat": 1}


def test_report_is_rebuilt_offline_and_flags_drift(tmp_path):
    compare_dir, _ = run(tmp_path, spec(tmp_path))
    original = read_json(compare_dir / "compare.json")
    assert json.loads(json.dumps(build_report(compare_dir))) == original

    index = read_json(compare_dir / "index.json")
    second_plain = compare_dir / index["runs"][2]["run_dir"] / "manifest.json"
    manifest = read_json(second_plain)
    manifest["tasks"][0]["generate_config"]["temperature"] = 0.5
    manifest["status"] = "interrupted"
    write_json(second_plain, manifest)

    report = build_report(compare_dir)
    [drift] = report["conditions"]["plain"]["repeat_config_changes"]
    assert drift["repeat"] == 2
    assert drift["changes"] == [["tasks.toy.generate_config.temperature", 0.0, 0.5]]
    assert not report["complete"]


def test_actual_image_drift_is_reported_separately(tmp_path):
    compare_dir, _ = run(tmp_path, spec(tmp_path))
    index = read_json(compare_dir / "index.json")
    for n, entry in enumerate(index["runs"]):
        path = compare_dir / entry["run_dir"] / "toy" / "samples.jsonl"
        results = read_results(path)
        for r in results:
            # the second plain repeat got a new image behind the same tag
            image = "sha256:B" if (n == 2 and r.sample.id == "toy/1") else "sha256:A"
            r.sandbox = {"image": "img:latest", "image_id": image, "container_id": f"c{n}"}
        write_results(path, results)

    report = build_report(compare_dir)
    assert report["complete"] and report["same_samples_in_every_run"]
    assert report["repeat_config_changes"] is False
    [drift] = report["environment_drift"]
    assert (drift["sample_id"], drift["field"]) == ("toy/1", "sandbox.image_id")
    assert drift["values"]["plain#2"] == "sha256:B" and drift["values"]["plain#1"] == "sha256:A"
    assert not report["comparable"]


def test_clean_comparison_is_comparable(tmp_path):
    compare_dir, _ = run(tmp_path, spec(tmp_path))
    report = read_json(compare_dir / "compare.json")
    assert report["environment_drift"] == [] and report["comparable"]


def test_runs_on_different_samples_are_flagged(tmp_path):
    compare_dir, _ = run(tmp_path, spec(tmp_path))
    index = read_json(compare_dir / "index.json")
    path = compare_dir / index["runs"][3]["run_dir"] / "toy" / "samples.jsonl"
    path.write_text("\n".join(path.read_text().splitlines()[:-1]) + "\n")
    assert not build_report(compare_dir)["same_samples_in_every_run"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"conditions": [{"label": "a", "params": {"solver": "plain"}}]}, "at least two"),
        (
            {
                "conditions": [
                    {"label": "a", "params": {"solver": "plain"}},
                    {"label": "a", "params": {"solver": "careful"}},
                ]
            },
            "unique",
        ),
        (
            {
                "conditions": [
                    {"label": "a", "params": {"solver": "plain"}},
                    {"label": "b", "params": {"solver": "careful", "limit": 2}},
                ]
            },
            "may only set 'solver'",
        ),
        (
            {
                "conditions": [
                    {"label": "a", "params": {"solver": "plain"}},
                    {"label": "b", "params": {"solver": "plain"}},
                ]
            },
            "different solver",
        ),
        (
            {
                "kind": "ablation",
                "conditions": [
                    {"label": "base"},
                    {"label": "b", "params": {"solver": "careful"}},
                ],
            },
            "not a single-variable ablation",
        ),
        (
            {
                "kind": "ablation",
                "params": {"limit": 4, "max_steps": 10},
                "conditions": [
                    {"label": "base"},
                    {"label": "b", "params": {"limit": 2, "max_steps": 5}},
                ],
            },
            "exactly one parameter",
        ),
        (
            {
                "kind": "ablation",
                "conditions": [
                    {"label": "base", "params": {"limit": 2}},
                    {"label": "b", "params": {"limit": 3}},
                ],
            },
            "baseline",
        ),
        ({"cache_policy": "auto"}, "replay cached"),
        ({"cache_policy": "always", "generate": {"temperature": 0.7}}, "replay cached"),
    ],
)
def test_invalid_comparisons_are_rejected(tmp_path, overrides, message):
    with pytest.raises(ValidationError, match=message):
        spec(tmp_path, **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"cache_policy": "auto", "generate": {"temperature": 0.7}},
        {"cache_policy": "auto", "repeats": 1},
        {"cache": False, "cache_policy": "always"},
        {
            "kind": "ablation",
            "params": {"limit": 4},
            "conditions": [{"label": "base"}, {"label": "short", "params": {"limit": 2}}],
        },
    ],
)
def test_valid_comparisons(tmp_path, overrides):
    spec(tmp_path, **overrides)


def test_ablation_changes_one_parameter(tmp_path):
    s = spec(
        tmp_path,
        kind="ablation",
        repeats=1,
        conditions=[{"label": "four"}, {"label": "two", "params": {"limit": 2}}],
    )
    compare_dir, _ = run(tmp_path, s)
    report = read_json(compare_dir / "compare.json")
    assert report["kind"] == "ablation"
    assert report["conditions"]["two"]["runs"][0]["n"] == 2
    assert not report["same_samples_in_every_run"]


def test_tokens_include_steps_of_a_failed_solver(tmp_path):
    from harness.core.types import Sample

    steps = [Step(index=i, text="", usage=Usage(input_tokens=5, output_tokens=1)) for i in range(3)]
    r = SampleResult(sample=Sample(id="x", input="", target=""), steps=steps)
    assert r.usage() == Usage(input_tokens=15, output_tokens=3)
    r = SampleResult(
        sample=Sample(id="x", input="", target=""),
        output=ModelOutput(text="", usage=Usage(input_tokens=2, output_tokens=1)),
    )
    assert r.usage() == Usage(input_tokens=2, output_tokens=1)


def test_samples_record_wall_time(tmp_path):
    compare_dir, _ = run(tmp_path, spec(tmp_path, repeats=1))
    index = read_json(compare_dir / "index.json")
    results = read_results(compare_dir / index["runs"][0]["run_dir"] / "toy" / "samples.jsonl")
    assert all(r.wall_time_s is not None and r.wall_time_s >= 0 for r in results)
    summary = read_json(compare_dir / index["runs"][0]["run_dir"] / "toy" / "summary.json")
    assert summary["wall_time_s"]["mean"] is not None
