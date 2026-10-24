import asyncio
import json

import pytest
from conftest import make_task, question_number

from harness.analysis import rescore as rescore_mod
from harness.analysis.diff import diff_manifests, diff_runs, flatten
from harness.analysis.rescore import rescore_run
from harness.analysis.stats import bootstrap_ci, summarize
from harness.core.io import read_results, write_results
from harness.core.model import MockModel
from harness.core.runner import run_eval
from harness.core.scorer import NumberMatchScorer
from harness.core.types import ErrorKind, GenerateConfig, Outcome, Score


def evaluate(tmp_path, respond, config=None):
    return asyncio.run(
        run_eval(
            MockModel(respond, name="mock-model"),
            [(make_task(), config or GenerateConfig())],
            output_dir=tmp_path / "runs",
            run_config={"note": "test"},
        )
    )


def good(m):
    return f"The answer is {2 * question_number(m)}"


def test_run_writes_manifest_summary_and_samples(tmp_path):
    run_dir = evaluate(tmp_path, good)
    manifest = json.loads((run_dir / "manifest.json").read_text())

    assert manifest["status"] == "completed"
    assert manifest["model"] == {"type": "mock", "name": "mock-model"}
    env = manifest["environment"]
    assert {"git_commit", "python", "packages", "uv_lock_sha256"} <= env.keys()
    (task,) = manifest["tasks"]
    assert task["n_samples"] == 6
    assert task["solver"]["fingerprint"] and task["scorer"]["fingerprint"]
    assert task["sample_ids_sha256"]
    assert task["generate_config"]["temperature"] == 0.0
    assert manifest["results"]["toy"]["accuracy"] == 1.0

    results = read_results(run_dir / "toy" / "samples.jsonl")
    assert len(results) == 6
    assert results[0].messages[-1]["content"] == "What is 0 + 0?"
    assert json.loads((run_dir / "toy" / "summary.json").read_text())["n"] == 6


def test_manifest_contains_no_api_key(tmp_path, monkeypatch):
    from harness.core.model import get_model

    model = get_model("dashscope/qwen-plus", api_key="sk-secret-value")
    assert "sk-secret-value" not in json.dumps(model.spec())


def test_rescore_uses_saved_outputs(tmp_path, monkeypatch):
    run_dir = evaluate(tmp_path, lambda m: f"I got {2 * question_number(m)} in total")
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["results"]["toy"]["accuracy"] == 1.0

    strict = make_task()
    strict.scorer = NumberMatchScorer(fallback_last_number=False)
    monkeypatch.setattr(rescore_mod, "get_task", lambda name, **params: strict)

    changes = rescore_run(run_dir)
    assert changes["toy"]["accuracy_after"] == 0.0
    assert changes["toy"]["samples_changed"] == 6
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["results"]["toy"]["outcomes"]["extract_failed"] == 6
    assert len(manifest["tasks"][0]["rescore_history"]) == 1


class BrokenScorer(NumberMatchScorer):
    def __call__(self, sample, text):
        if sample.id == "toy/0":
            raise KeyError("broken scorer")
        return super().__call__(sample, text)


class NaNScorer(NumberMatchScorer):
    def __call__(self, sample, text):
        return Score.model_construct(value=float("nan"))


def use_scorer(monkeypatch, scorer):
    task = make_task()
    task.scorer = scorer
    monkeypatch.setattr(rescore_mod, "get_task", lambda name, **params: task)


def test_rescore_with_fixed_scorer_clears_old_scorer_error(tmp_path, monkeypatch):
    task = make_task()
    task.scorer = BrokenScorer()
    run_dir = asyncio.run(
        run_eval(MockModel(good), [(task, GenerateConfig())], output_dir=tmp_path / "runs")
    )
    (broken,) = [r for r in read_results(run_dir / "toy/samples.jsonl") if r.sample.id == "toy/0"]
    assert broken.outcome == "scorer_error" and broken.output is not None

    use_scorer(monkeypatch, NumberMatchScorer())
    changes = rescore_run(run_dir)
    assert changes["toy"] == {
        "accuracy_before": pytest.approx(5 / 6),
        "accuracy_after": 1.0,
        "samples_changed": 1,
        "scorer_errors": 0,
    }
    results = {r.sample.id: r for r in read_results(run_dir / "toy/samples.jsonl")}
    fixed = results["toy/0"]
    assert (fixed.outcome, fixed.error, fixed.error_kind) == ("correct", None, None)
    assert fixed.score.value == 1.0
    (old,) = fixed.rescore_history
    assert old["outcome"] == "scorer_error" and old["error_kind"] == "scorer"
    assert "broken scorer" in old["error"]
    assert results["toy/1"].rescore_history == []
    summary = json.loads((run_dir / "manifest.json").read_text())["results"]["toy"]
    assert summary["outcomes"]["correct"] == 6 and summary["outcomes"]["scorer_error"] == 0


def test_rescore_failures_are_per_sample_scorer_errors(tmp_path, monkeypatch):
    run_dir = evaluate(tmp_path, good)
    use_scorer(monkeypatch, BrokenScorer())
    changes = rescore_run(run_dir)
    assert changes["toy"]["scorer_errors"] == 1
    assert changes["toy"]["accuracy_after"] == pytest.approx(5 / 6)
    results = {r.sample.id: r for r in read_results(run_dir / "toy/samples.jsonl")}
    assert results["toy/0"].outcome == "scorer_error" and results["toy/0"].score is None
    assert results["toy/0"].rescore_history[0]["score"]["value"] == 1.0
    assert results["toy/5"].outcome == "correct"


def test_rescore_rejects_invalid_scores(tmp_path, monkeypatch):
    run_dir = evaluate(tmp_path, good)
    use_scorer(monkeypatch, NaNScorer())
    assert rescore_run(run_dir)["toy"]["scorer_errors"] == 6
    for r in read_results(run_dir / "toy/samples.jsonl"):
        assert r.outcome == "scorer_error" and "invalid value nan" in r.error


def test_rescore_keeps_model_and_solver_failures(tmp_path, monkeypatch):
    run_dir = evaluate(tmp_path, good)
    path = run_dir / "toy/samples.jsonl"
    results = read_results(path)
    results[0].error, results[0].error_kind = "RuntimeError: agent crashed", ErrorKind.SOLVER
    results[0].outcome = Outcome.SOLVER_ERROR
    results[1].error, results[1].error_kind = "ModelCallError: 500", ErrorKind.MODEL
    results[1].outcome = Outcome.API_ERROR
    write_results(path, results)

    use_scorer(monkeypatch, NumberMatchScorer())
    assert rescore_run(run_dir)["toy"]["samples_changed"] == 0
    results = read_results(path)
    assert [r.outcome for r in results[:2]] == ["solver_error", "api_error"]
    assert results[0].error == "RuntimeError: agent crashed"


def test_diff_reports_config_changes_and_flips(tmp_path):
    a = evaluate(tmp_path, good)

    def worse(m):
        n = question_number(m)
        return "The answer is 99" if n in (1, 2) else good(m)

    b = evaluate(tmp_path, worse, GenerateConfig(temperature=0.7))
    d = diff_runs(a, b)

    keys = {k for k, _, _ in d["manifest_changes"]}
    assert "tasks.toy.generate_config.temperature" in keys
    t = d["tasks"]["toy"]
    assert t["regressions"] == ["toy/1", "toy/2"]
    assert t["improvements"] == []
    assert t["identical_outputs"] == 4
    assert t["outcome_transitions"] == {"correct -> wrong": 2}


def test_identical_runs_have_no_manifest_changes(tmp_path):
    a = evaluate(tmp_path, good)
    b = evaluate(tmp_path, good)
    assert a != b
    assert diff_runs(a, b)["manifest_changes"] == []


def test_flatten_indexes_named_lists():
    assert flatten({"tasks": [{"name": "x", "n": 1}]}) == {"tasks.x.name": "x", "tasks.x.n": 1}


def test_task_missing_from_one_run_is_reported_once():
    a = {"tasks": [{"name": "x", "n": 1}, {"name": "y", "n": 2, "m": 3}]}
    b = {"tasks": [{"name": "x", "n": 5}]}
    assert diff_manifests(a, b) == [("tasks.x.n", 1, 5), ("tasks.y", "<present>", None)]


def test_bootstrap_ci_brackets_mean():
    values = [1.0] * 70 + [0.0] * 30
    lo, hi = bootstrap_ci(values)
    assert lo < 0.7 < hi
    assert hi - lo < 0.25
    assert bootstrap_ci([]) == (0.0, 0.0)


def test_summary_counts_errors_as_failures(tmp_path):
    def respond(m):
        if question_number(m) == 0:
            raise ValueError("x")
        return good(m)

    run_dir = evaluate(tmp_path, respond)
    s = summarize(read_results(run_dir / "toy" / "samples.jsonl"))
    assert s["accuracy"] == pytest.approx(5 / 6)
    assert s["outcomes"]["api_error"] == 1
    assert s["rates"]["api_error"] == pytest.approx(1 / 6)
    assert s["extraction_methods"] == {"answer_is": 5}
