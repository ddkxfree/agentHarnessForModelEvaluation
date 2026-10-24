"""Independent re-verification: cache policy, served model identity, fresh-sandbox grading."""

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from conftest import make_task, question_number
from fakes import FakeProvider, call, scripted, tool_turn
from test_agent import agent_task, run

from harness.analysis import rescore as rescore_mod
from harness.analysis import verify as verify_mod
from harness.analysis.rescore import rescore_run
from harness.core.cache import DiskCache
from harness.core.io import read_results
from harness.core.model import MockModel, OpenAICompatibleModel
from harness.core.runner import run_eval, use_cache
from harness.core.types import GenerateConfig, ModelOutput, Outcome, Score
from harness.sandbox.base import SandboxError


@pytest.mark.parametrize(
    ("policy", "temperature", "expected"),
    [
        ("auto", 0.0, True),
        ("auto", 0.8, False),
        ("always", 0.8, True),
        ("never", 0.0, False),
    ],
)
def test_cache_policy(policy, temperature, expected):
    assert use_cache(policy, GenerateConfig(temperature=temperature)) is expected


def test_unknown_cache_policy_is_rejected():
    with pytest.raises(ValueError, match="unknown cache policy"):
        use_cache("sometimes", GenerateConfig())


@pytest.mark.parametrize(("temperature", "calls_second_run"), [(0.0, 0), (0.8, 6)])
def test_sampled_outputs_are_not_reused_by_default(tmp_path, temperature, calls_second_run):
    cache = DiskCache(tmp_path / "cache")
    config = GenerateConfig(temperature=temperature)

    def evaluate():
        model = MockModel(lambda m: f"The answer is {2 * question_number(m)}")
        run_dir = asyncio.run(
            run_eval(model, [(make_task(), config)], output_dir=tmp_path / "runs", cache=cache)
        )
        return model, json.loads((run_dir / "manifest.json").read_text())

    first, _ = evaluate()
    second, manifest = evaluate()
    assert first.calls == 6
    assert second.calls == calls_second_run
    assert manifest["tasks"][0]["cache"] == {
        "enabled": True,
        "policy": "auto",
        "used": temperature == 0.0,
    }


def test_served_model_identity_is_recorded(tmp_path):
    def respond(messages):
        build = "b1" if question_number(messages) < 4 else "b2"
        return ModelOutput(text="1", served_model="qwen-2025-01", system_fingerprint=build)

    run_dir = asyncio.run(
        run_eval(MockModel(respond), [(make_task(), GenerateConfig())], output_dir=tmp_path)
    )
    results = read_results(run_dir / "toy/samples.jsonl")
    assert results[0].served_models == ["qwen-2025-01@b1"]
    summary = json.loads((run_dir / "manifest.json").read_text())["results"]["toy"]
    assert summary["served_models"] == {"qwen-2025-01@b1": 4, "qwen-2025-01@b2": 2}


def test_openai_compatible_model_reads_the_served_model():
    model = OpenAICompatibleModel("alias-latest", provider="openai", api_key="k")
    resp = SimpleNamespace(
        model="model-2026-01-01",
        system_fingerprint="fp_123",
        usage=None,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hi", tool_calls=None), finish_reason="stop"
            )
        ],
    )

    async def create(**kwargs):
        return resp

    model._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    out = asyncio.run(model.generate([{"role": "user", "content": "x"}], GenerateConfig()))
    assert out.served_identity() == "model-2026-01-01@fp_123"


class FileScorer:
    """SubmissionScorer: the submission is /app/x.txt; grading rewrites it in a fresh sandbox."""

    name = "file"
    requires_sandbox = True
    fresh_sandbox = True

    def __init__(self, fail_on=None):
        self.graded_in = []
        self.fail_on = fail_on

    async def collect(self, sample, text, sandbox):
        res = await sandbox.exec("cat /app/x.txt")
        return res.stdout

    async def grade(self, sample, submission, sandbox):
        self.graded_in.append(sandbox)
        if self.fail_on == "env":
            raise SandboxError("docker went away")
        assert "/app/x.txt" not in sandbox.files, "grading sandbox is not fresh"
        await sandbox.write_file("/app/x.txt", submission)
        return Score(value=float(submission == "hi\n"), extracted=submission.strip())

    def describe(self):
        return {"name": self.name, "fingerprint": "file-v1"}


WRITE_HI = tool_turn(call("bash", '{"command": "echo hi > /app/x.txt"}'))
SUBMIT = tool_turn(call("submit", "{}"))


def test_submission_is_saved_and_graded_in_a_fresh_sandbox(tmp_path):
    scorer = FileScorer()
    (r,), provider = run(
        agent_task(scorer=scorer), scripted(WRITE_HI, SUBMIT), artifact_dir=tmp_path
    )
    assert r.outcome == Outcome.CORRECT, r.error
    agent_sb, grader = provider.created
    assert scorer.graded_in == [grader]
    assert agent_sb.closed and grader.closed
    assert r.grading_sandbox == {"type": "FakeSandbox"}
    (sub,) = [a for a in r.artifacts if a.name == "submission"]
    assert (tmp_path / sub.path).read_text() == "hi\n"
    assert sub.sha256 == hashlib.sha256(b"hi\n").hexdigest()


def test_environment_failure_while_grading_is_env_error():
    (r,), _ = run(agent_task(scorer=FileScorer(fail_on="env")), scripted(WRITE_HI, SUBMIT))
    assert r.outcome == Outcome.ENV_ERROR
    assert any(a.name == "submission" for a in r.artifacts)


def file_task(scorer):
    task = agent_task(scorer=scorer, n=2)
    task.name = "files"
    return task


def evaluate_files(tmp_path, scorer, model):
    return asyncio.run(
        run_eval(
            model,
            [(file_task(scorer), GenerateConfig())],
            output_dir=tmp_path / "runs",
            sandbox_provider=FakeProvider(),
        )
    )


def verify(run_dir, monkeypatch, scorer, **kwargs):
    monkeypatch.setattr(verify_mod, "get_task", lambda name, **params: file_task(scorer))
    provider = FakeProvider()
    report = asyncio.run(verify_mod.verify_run(run_dir, provider, **kwargs))
    return report["files"], provider


def test_verify_regrades_saved_submissions_without_the_model(tmp_path, monkeypatch):
    run_dir = evaluate_files(tmp_path, FileScorer(), scripted(WRITE_HI, SUBMIT))

    report, provider = verify(run_dir, monkeypatch, FileScorer())
    assert report["statuses"] == {"agree": 2}
    assert len(provider.created) == 2 and all(sb.closed for sb in provider.created)
    rows = [json.loads(line) for line in (run_dir / "files/verify.jsonl").read_text().splitlines()]
    assert rows[0]["verified"] == rows[0]["recorded"] == {"value": 1.0, "extracted": "hi"}
    saved = json.loads((run_dir / "verify.json").read_text())
    assert saved["tasks"]["files"]["n"] == 2 and saved["sandbox_provider"] == {"name": "fake"}
    monkeypatch.setattr(rescore_mod, "get_task", lambda name, **params: file_task(FileScorer()))
    assert rescore_run(run_dir)["files"]["skipped"].endswith("use `harness verify`")


def test_verify_detects_tampering_and_disagreement(tmp_path, monkeypatch):
    run_dir = evaluate_files(tmp_path, FileScorer(), scripted(WRITE_HI, SUBMIT))
    results = read_results(run_dir / "files/samples.jsonl")
    sub = next(a for a in results[0].artifacts if a.name == "submission")
    (run_dir / "files/artifacts" / sub.path).write_text("bye\n")

    class Stricter(FileScorer):
        async def grade(self, sample, submission, sandbox):
            score = await super().grade(sample, submission, sandbox)
            return score.model_copy(update={"value": 0.0})

    report, _ = verify(run_dir, monkeypatch, Stricter())
    assert report["statuses"] == {"submission_hash_mismatch": 1, "disagree": 1}
    assert report["disagreements"] == ["t/1"]


def test_verify_update_replaces_grades_and_keeps_history(tmp_path, monkeypatch):
    run_dir = evaluate_files(tmp_path, FileScorer(), scripted(WRITE_HI, SUBMIT))

    class Stricter(FileScorer):
        async def grade(self, sample, submission, sandbox):
            return Score(value=0.0, extracted="strict")

        def describe(self):
            return {"name": self.name, "fingerprint": "file-v2"}

    report, _ = verify(run_dir, monkeypatch, Stricter(), update=True, sample_ids=["t/0"])
    assert report["samples_changed"] == 1
    results = read_results(run_dir / "files/samples.jsonl")
    assert results[0].outcome == Outcome.WRONG
    assert results[0].rescore_history[0]["score"]["value"] == 1.0
    assert results[1].outcome == Outcome.CORRECT
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["results"]["files"]["accuracy"] == 0.5
    assert manifest["tasks"][0]["rescore_history"][-1]["kind"] == "verify"
    assert manifest["tasks"][0]["scorer"]["fingerprint"] == "file-v2"


def test_verify_reports_environment_failures_without_touching_results(tmp_path, monkeypatch):
    run_dir = evaluate_files(tmp_path, FileScorer(), scripted(WRITE_HI, SUBMIT))
    report, _ = verify(run_dir, monkeypatch, FileScorer(fail_on="env"), update=True)
    assert report["statuses"] == {"env_error": 2}
    assert report["samples_changed"] == 0
    assert all(r.outcome == Outcome.CORRECT for r in read_results(run_dir / "files/samples.jsonl"))
