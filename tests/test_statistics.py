"""Stage 7: epochs and cache namespaces, resuming, paired statistics, diff hints, reports."""

import asyncio
import json

import pytest
from conftest import make_samples, make_task, question_number
from test_compare import respond, spec, toy_factory

from harness.analysis.compare import build_report, resume_comparison, run_comparison
from harness.analysis.diff import diff_runs
from harness.analysis.report import failure_report
from harness.analysis.stats import paired_difference, sign_test, summarize
from harness.core.cache import DiskCache, cache_key
from harness.core.io import read_json, read_results, write_json, write_results
from harness.core.model import MockModel
from harness.core.runner import cache_namespace, run_eval, sample_dirname
from harness.core.types import GenerateConfig, Outcome, Sample, SampleResult, Score


def answer(m):
    return f"The answer is {2 * question_number(m)}"


def evaluate(tmp_path, model, cache=None, **kwargs):
    run_dir = asyncio.run(
        run_eval(
            model,
            [(make_task(), GenerateConfig())],
            output_dir=tmp_path / "runs",
            cache=cache,
            **kwargs,
        )
    )
    return run_dir, read_results(run_dir / "toy/samples.jsonl")


def test_epoch_one_keeps_existing_cache_keys():
    args = ({"name": "m"}, [{"role": "user", "content": "hi"}], GenerateConfig())
    assert cache_namespace(None, 1) is None
    assert cache_key(*args, None, cache_namespace(None, 1)) == cache_key(*args)
    keys = {
        cache_key(*args, None, cache_namespace(ns, e))
        for ns, e in [(None, 1), (None, 2), ("x", 1), ("x", 2)]
    }
    assert len(keys) == 4
    assert sample_dirname("a/b", 1) != sample_dirname("a/b", 2)


def test_epochs_are_sampled_separately_and_resume_within_an_epoch(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    model = MockModel(answer)
    run_dir, results = evaluate(tmp_path, model, cache, epochs=3)
    # temperature 0 with the default policy still calls the model once per epoch
    assert model.calls == 18
    assert sorted({r.epoch for r in results}) == [1, 2, 3] and len(results) == 18
    manifest = read_json(run_dir / "manifest.json")
    assert manifest["tasks"][0]["epochs"] == [1, 2, 3]
    stats = manifest["results"]["toy"]["epochs"]
    assert stats["n_epochs"] == 3 and stats["std"] == 0.0 and stats["outcome_varies_samples"] == 0

    # re-running (e.g. after an interruption) replays every epoch from the cache
    again = MockModel(answer)
    evaluate(tmp_path, again, cache, epochs=3)
    assert again.calls == 0
    # a fourth epoch and another namespace are new draws
    more = MockModel(answer)
    evaluate(tmp_path, more, cache, epochs=1, first_epoch=4)
    other = MockModel(answer)
    evaluate(tmp_path, other, cache, epochs=1, namespace="attempt-2")
    assert (more.calls, other.calls) == (6, 6)


def test_epoch_statistics_and_ci_resample_samples_not_epochs():
    def results(epochs, flaky):
        out = []
        for e in range(1, epochs + 1):
            for i in range(6):
                value = float(i < 3 or (i == 3 and flaky and e % 2 == 0))
                out.append(
                    SampleResult(
                        sample=Sample(id=f"s{i}", input="", target=""),
                        epoch=e,
                        score=Score(value=value),
                        outcome=Outcome.CORRECT if value else Outcome.WRONG,
                    )
                )
        return out

    one, three = summarize(results(1, False)), summarize(results(3, False))
    assert one["epochs"] is None
    # identical epochs add no information, so the interval must not shrink
    assert one["ci95"] == three["ci95"]
    flaky = summarize(results(4, True))["epochs"]
    assert flaky["accuracy_per_epoch"] == {"1": 0.5, "2": 4 / 6, "3": 0.5, "4": 4 / 6}
    assert flaky["outcome_varies_samples"] == flaky["score_varies_samples"] == 1
    assert flaky["std"] == pytest.approx(0.0962, abs=1e-3)


def test_sign_test_and_paired_difference():
    assert sign_test(0, 0) == 1.0
    assert sign_test(5, 0) == pytest.approx(0.0625)
    assert sign_test(3, 3) == 1.0
    p = paired_difference({"a": 0, "b": 1, "c": 0, "z": 1}, {"a": 1, "b": 1, "c": 1, "y": 0})
    assert (p["n"], p["b_better"], p["b_worse"], p["ties"]) == (3, 2, 0, 1)
    assert p["mean_difference"] == pytest.approx(2 / 3)
    assert p["sign_test_p"] == 0.5 and not p["enough_samples_for_claims"]


def test_comparison_reports_paired_differences(tmp_path):
    compare_dir = asyncio.run(
        run_comparison(spec(tmp_path), MockModel(respond), make_task=toy_factory)
    )
    report = read_json(compare_dir / "compare.json")
    p = report["paired_vs_baseline"]["careful"]
    # plain solves the even questions of 4, careful solves all
    assert (p["n"], p["mean_difference"], p["b_better"], p["ties"]) == (4, 0.5, 2, 2)
    assert p["sign_test_p"] == 0.5
    assert report["conditions"]["careful"]["accuracy"]["ci95_over_samples"] == [1.0, 1.0]


def test_resume_reruns_only_missing_runs_and_replays_their_cache(tmp_path):
    s = spec(tmp_path)
    compare_dir = asyncio.run(run_comparison(s, MockModel(respond), make_task=toy_factory))
    index = read_json(compare_dir / "index.json")
    assert index["spec"]["cache_namespace"] == compare_dir.name

    # simulate a crash: the last run never got indexed, the one before was interrupted
    interrupted = compare_dir / index["runs"][2]["run_dir"] / "manifest.json"
    manifest = read_json(interrupted)
    manifest["status"] = "interrupted"
    write_json(interrupted, manifest)
    index["runs"] = index["runs"][:3]
    write_json(compare_dir / "index.json", index)

    model = MockModel(respond)
    asyncio.run(resume_comparison(compare_dir, model, make_task=toy_factory))
    # both runs were generated before, in the same epochs and namespace: nothing is resampled
    assert model.calls == 0
    index = read_json(compare_dir / "index.json")
    assert sorted((r["label"], r["repeat"]) for r in index["runs"]) == [
        ("careful", 1),
        ("careful", 2),
        ("plain", 1),
        ("plain", 2),
    ]
    assert [(r["label"], r["repeat"]) for r in index["superseded"]] == [("plain", 2)]
    report = build_report(compare_dir)
    assert report["complete"] and report["scores_comparable"]
    # the resumed run replayed its responses, so its wall time is not inference time
    assert not report["performance_comparable"]
    assert report["cache_states"]["plain#2"] == "cached"
    assert report["cache_states"]["careful#2"] == "cached"
    assert report["cache_states"]["plain#1"] == "fresh"
    resumed = [r for r in report["conditions"]["plain"]["runs"] if r["resumed"]]
    assert [r["repeat"] for r in resumed] == [2]
    assert resumed[0]["fresh_input_tokens"] == 0 and resumed[0]["input_tokens"] == 40

    # a new comparison from the same spec gets its own namespace and samples afresh
    fresh = MockModel(respond)
    asyncio.run(run_comparison(s, fresh, make_task=toy_factory))
    assert fresh.calls == 16


def test_diff_explains_likely_causes(tmp_path):
    run_a, _ = evaluate(tmp_path, MockModel(answer, name="model-a"))
    run_b, results = evaluate(tmp_path, MockModel(answer, name="model-b"))
    # same outputs, but one sample's score was changed after the fact
    results[0].score = Score(value=0.0, extracted="x")
    results[0].outcome = Outcome.WRONG
    write_results(run_b / "toy/samples.jsonl", results)

    d = diff_runs(run_a, run_b)
    hints = "\n".join(d["likely_causes"])
    assert "model or endpoint changed" in hints
    assert "1 samples have identical outputs but different scores" in hints
    t = d["tasks"]["toy"]
    assert t["same_output_different_score"] == ["toy/0"]
    assert t["paired"]["b_worse"] == 1 and t["paired"]["n"] == 6


def test_diff_with_no_recorded_difference_points_at_nondeterminism(tmp_path):
    run_a, _ = evaluate(tmp_path, MockModel(answer))
    run_b, _ = evaluate(tmp_path, MockModel(lambda m: "The answer is -1"))
    hints = diff_runs(run_a, run_b)["likely_causes"]
    assert len(hints) == 1 and "nondeterminism" in hints[0]


def test_diff_matches_samples_by_epoch(tmp_path):
    run_a, _ = evaluate(tmp_path, MockModel(answer), epochs=2)
    run_b, _ = evaluate(tmp_path, MockModel(answer), epochs=2)
    t = diff_runs(run_a, run_b)["tasks"]["toy"]
    assert t["common_samples"] == 12 and t["paired"]["n"] == 6


def test_failure_report_samples_each_failure_type(tmp_path):
    def flaky(m):
        n = question_number(m)
        if n == 5:
            raise RuntimeError("upstream exploded")
        return answer(m) if n % 2 == 0 else "no idea"

    run_dir, _ = evaluate(tmp_path, MockModel(flaky), retries=0)
    text = failure_report(run_dir, per_outcome=1, seed=0)
    assert text == failure_report(run_dir, per_outcome=1, seed=0)
    assert "| correct | 3 | 50.0% |" in text
    assert "### extract_failed (2)" in text and "### api_error (1)" in text
    assert "upstream exploded" in text
    assert "### correct" not in text
    assert text.count("#### `toy/") == 2


def test_report_cli_writes_markdown(tmp_path):
    from typer.testing import CliRunner

    from harness.cli import app

    run_dir, _ = evaluate(tmp_path, MockModel(lambda m: "nope"))
    result = CliRunner().invoke(app, ["report", str(run_dir)])
    assert result.exit_code == 0, result.output
    assert (run_dir / "report.md").read_text().startswith("# Failure report")


def test_samples_without_epoch_field_load_as_epoch_one(tmp_path):
    path = tmp_path / "samples.jsonl"
    path.write_text(json.dumps({"sample": {"id": "x", "input": "", "target": ""}}) + "\n")
    [r] = read_results(path)
    assert r.epoch == 1
    assert make_samples(1)[0].id == "toy/0"


def test_conditions_do_not_share_cached_responses(tmp_path):
    """An ablation whose parameter never reaches the prompt sends identical requests in both
    conditions; each condition must still get its own model calls."""

    def factory(name, solver="plain", limit=None, budget=1):
        task = toy_factory(name, solver=solver, limit=limit)
        task.params["budget"] = budget
        return task

    s = spec(
        tmp_path,
        kind="ablation",
        params={"limit": 4, "budget": 1},
        conditions=[{"label": "base"}, {"label": "more", "params": {"budget": 2}}],
    )
    model = MockModel(respond)
    compare_dir = asyncio.run(run_comparison(s, model, make_task=factory))
    assert model.calls == 2 * 2 * 4
    report = read_json(compare_dir / "compare.json")
    assert report["conditions"]["more"]["cached_calls"] == 0
    assert report["performance_comparable"]


@pytest.mark.parametrize(
    ("scores", "outcomes", "score_varies", "outcome_varies"),
    [
        ([0.5, 0.5, 0.5], ["wrong"] * 3, 0, 0),
        ([0.5, 0.25, 0.5], ["wrong"] * 3, 1, 0),
        ([1.0, 1.0, 1.0], ["correct"] * 3, 0, 0),
        ([0.0, 0.0, 0.0], ["wrong"] * 3, 0, 0),
        ([1.0, 0.0, 1.0], ["correct", "wrong", "correct"], 1, 1),
    ],
)
def test_epoch_variation_handles_partial_scores(scores, outcomes, score_varies, outcome_varies):
    results = [
        SampleResult(
            sample=Sample(id="s", input="", target=""),
            epoch=e,
            score=Score(value=v),
            outcome=Outcome(o),
        )
        for e, (v, o) in enumerate(zip(scores, outcomes, strict=True), start=1)
    ]
    stats = summarize(results)["epochs"]
    assert stats["score_varies_samples"] == score_varies
    assert stats["outcome_varies_samples"] == outcome_varies


def test_summary_separates_fresh_and_replayed_calls(tmp_path):
    cache = DiskCache(tmp_path / "cache")
    first, _ = evaluate(tmp_path, MockModel(answer), cache)
    second, results = evaluate(tmp_path, MockModel(answer), cache)
    s1 = read_json(first / "manifest.json")["results"]["toy"]
    s2 = read_json(second / "manifest.json")["results"]["toy"]
    assert s1["calls"] == {"fresh": 6, "cached": 0}
    assert s2["calls"] == {"fresh": 0, "cached": 6}
    assert s2["fresh_tokens"] == {"input": 0, "output": 0}
    assert all(r.calls.cached == 1 for r in results)
