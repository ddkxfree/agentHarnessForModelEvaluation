"""The onboarding agent with scripted MockModel replies and real validation subprocesses."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
import yaml

from harness.core.cache import DiskCache
from harness.core.fingerprint import fingerprint_dir
from harness.core.model import MockModel
from harness.core.types import ModelOutput, Usage
from harness.onboard.agent import OnboardAgent, parse_files, verify_attempt
from harness.onboard.spec import Budget, OnboardSpec, load_spec
from harness.onboard.validator import ExecResult, SubprocessExecutor, validate
from harness.sandbox.base import SandboxError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "onboard" / "world-capitals"
GOOD = (Path(__file__).parent / "onboard_fixtures" / "good_adapter.py").read_text()

PLAN = json.dumps(
    {
        "field_mapping": {
            "id": "world-capitals/<qid>",
            "input": "question template with country",
            "target": "capital",
            "metadata": "alt_names split on |",
        },
        "plan": "Read the TSV with csv, prompt per country, normalized exact match.",
        "questions": [],
    }
)

# the first round's typical mistakes
WRONG_FIELD = GOOD.replace("r['qid']", "r['id']")
ALWAYS_CORRECT = GOOD.replace(
    "ok = bool(got) and got in {normalize(a) for a in answers}", "ok = True"
)
DISALLOWED_IMPORT = "import pandas\n" + GOOD
TOUCHES_DATA = GOOD.replace(
    "    root = Path(data_dir) if data_dir else DEFAULT_DATA\n",
    "    root = Path(data_dir) if data_dir else DEFAULT_DATA\n"
    '    (root / "capitals.tsv").write_text("qid\\tcountry\\tcapital\\talt_names\\n")\n',
)
SELF_TEST = """\
import os

from adapter import world_capitals


def test_the_gold_answer_is_correct():
    task = world_capitals(data_dir=os.environ["ONBOARD_DATA_DIR"])
    sample = task.load()[0]
    assert task.scorer(sample, sample.target).value == 1.0
"""


def files(**contents: str) -> str:
    return "\n".join(f'<file path="{p}">\n{c}</file>' for p, c in contents.items())


def adapter(text: str) -> str:
    return files(**{"adapter.py": text})


class Script:
    """Replies in order; remembers what it was asked."""

    def __init__(self, *replies: str | ModelOutput | Exception) -> None:
        self.replies = list(replies)
        self.requests: list[list[dict]] = []

    def __call__(self, messages):
        self.requests.append(messages)
        reply = self.replies[len(self.requests) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_spec(tmp_path: Path, **budget) -> OnboardSpec:
    source = tmp_path / "source"
    if not source.exists():
        shutil.copytree(EXAMPLE, source)
    return OnboardSpec(
        task="world-capitals",
        source=source,
        source_sha256=fingerprint_dir(source),
        budget=Budget(**budget),
        output_dir=tmp_path / "runs",
    )


def attempt(spec, script, **kwargs):
    model = MockModel(script, name="scripted")
    run_dir = asyncio.run(OnboardAgent(spec, model, retry_base_delay=0, **kwargs).run())
    return run_dir, json.loads((run_dir / "attempt.json").read_text()), model


# --- 1. a valid adapter --------------------------------------------------------------------


def test_a_valid_adapter_passes_independent_validation(tmp_path):
    spec = make_spec(tmp_path)
    run_dir, record, _ = attempt(spec, Script(PLAN, adapter(GOOD)))
    assert record["status"] == "passed" and record["stop_reason"] == "validated"
    assert record["category"] is None and len(record["rounds"]) == 1
    report = record["rounds"][0]["validation"]
    assert [c["name"] for c in report["checks"]] == ["static", "import", "protocol", "cases"]
    # every frozen case plus an empty output per named sample, which must fail
    assert len(report["cases"]) == 11 + 6
    assert all(c["passed"] for c in report["cases"])
    assert {c["outcome"] for c in report["cases"] if c["output"] == ""} == {"wrong"}
    # archive: identities, versions, budget, steps, plan, files, diff, reproduction
    assert record["model"] == {"type": "mock", "name": "scripted"}
    assert record["source_sha256"] == spec.source_sha256
    assert record["prompt_version"] and record["validator_version"]
    assert record["plan"]["field_mapping"]["target"] == "capital"
    assert [s["phase"] for s in record["steps"]] == ["plan", "write"]
    assert (run_dir / "candidate" / "adapter.py").read_text() == GOOD
    diff = (run_dir / "final.diff").read_text()
    assert "+++ b/src/harness/tasks/world_capitals.py" in diff
    assert any("onboard-verify" in c for c in record["reproduce"])
    conversation = json.loads((run_dir / "conversation.json").read_text())
    assert [m["role"] for m in conversation] == ["system", "user", "assistant", "user", "assistant"]
    # the agent saw the description, rules and data, but never the acceptance cases
    seen = json.dumps(conversation)
    assert "Official scoring rule" in seen and "Yamoussoukro" in seen
    assert "common mistake" not in seen


# --- 2. feedback and repair ----------------------------------------------------------------


def test_a_failed_round_is_fed_back_and_repaired(tmp_path):
    spec = make_spec(tmp_path)
    script = Script(PLAN, adapter(WRONG_FIELD), adapter(GOOD))
    _, record, _ = attempt(spec, script)
    assert record["status"] == "passed"
    assert [r["category"] for r in record["rounds"]] == ["protocol_error", None]
    feedback = script.requests[2][-1]["content"]
    assert "Independent validation failed (protocol_error)" in feedback
    assert "KeyError" in feedback and "'id'" in feedback
    # the repair was generated after the feedback, not before
    assert script.requests[2][-2]["content"] == adapter(WRONG_FIELD)


def test_a_reply_without_files_is_a_generation_failure_that_can_be_repaired(tmp_path):
    spec = make_spec(tmp_path)
    script = Script(PLAN, "Sure, here is my plan again.", adapter(GOOD))
    _, record, _ = attempt(spec, script)
    assert record["status"] == "passed"
    assert [r["category"] for r in record["rounds"]] == ["generation_failed", None]
    assert "No <file" in script.requests[2][-1]["content"]


# --- 3. self tests are not enough ----------------------------------------------------------


def test_self_tests_pass_but_independent_validation_rejects(tmp_path):
    spec = make_spec(tmp_path, max_rounds=1)
    reply = files(**{"adapter.py": ALWAYS_CORRECT, "test_adapter.py": SELF_TEST})
    _, record, _ = attempt(spec, Script(PLAN, reply))
    (rnd,) = record["rounds"]
    assert rnd["validation"]["self_test"]["status"] == "passed"
    assert rnd["category"] == "scoring_error"
    assert "should be wrong, got correct" in rnd["validation"]["detail"]
    assert record["status"] == "failed" and record["stop_reason"] == "max_rounds"


def test_self_test_failures_are_reported_but_do_not_decide(tmp_path):
    spec = make_spec(tmp_path)
    failing = SELF_TEST.replace("== 1.0", "== 0.5")
    reply = files(**{"adapter.py": GOOD, "test_adapter.py": failing})
    _, record, _ = attempt(spec, Script(PLAN, reply))
    assert record["status"] == "passed"
    assert record["rounds"][0]["validation"]["self_test"]["status"] == "failed"


# --- 4. protected inputs and the write boundary --------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "data/capitals.tsv",
        "SCORING.md",
        "acceptance.jsonl",
        "README.md",
        "../adapter.py",
        "/tmp/adapter.py",
        "src/harness/tasks/world_capitals.py",
        "candidate/../../x.py",
    ],
)
def test_writes_outside_the_candidate_files_stop_the_attempt(tmp_path, path):
    spec = make_spec(tmp_path)
    reply = files(**{"adapter.py": GOOD, path: "q1\tFrance\tLyon\t\n"})
    run_dir, record, _ = attempt(spec, Script(PLAN, reply))
    assert record["status"] == "failed" and record["category"] == "protected_violation"
    assert record["rounds"][0]["rejected"][0]["path"] == path
    assert path in record["stop_detail"]
    # nothing from that reply was written, and the source is unchanged
    assert not (run_dir / "candidate" / "adapter.py").exists()
    assert fingerprint_dir(spec.source) == spec.source_sha256


def test_a_candidate_that_changes_its_data_is_rejected(tmp_path):
    spec = make_spec(tmp_path)
    _, record, _ = attempt(spec, Script(PLAN, adapter(TOUCHES_DATA)))
    assert record["category"] == "protected_violation"
    assert record["rounds"][0]["validation"]["category"] == "protected_violation"
    assert fingerprint_dir(spec.source) == spec.source_sha256  # only the copy was touched


def test_disallowed_imports_are_dependency_errors(tmp_path):
    spec = make_spec(tmp_path, max_rounds=1)
    _, record, _ = attempt(spec, Script(PLAN, adapter(DISALLOWED_IMPORT)))
    assert record["rounds"][0]["category"] == "dependency_error"
    assert "pandas" in record["rounds"][0]["validation"]["detail"]


def test_an_unpinned_or_changed_source_is_refused(tmp_path):
    spec = make_spec(tmp_path)
    spec = spec.model_copy(update={"source_sha256": "0" * 16})
    _, record, model = attempt(spec, Script(PLAN, adapter(GOOD)))
    assert record["category"] == "source_changed" and model.calls == 0


# --- 5. budgets ----------------------------------------------------------------------------


def test_the_round_limit_stops_and_keeps_the_work(tmp_path):
    spec = make_spec(tmp_path, max_rounds=2)
    run_dir, record, _ = attempt(spec, Script(PLAN, adapter(WRONG_FIELD), adapter(WRONG_FIELD)))
    assert record["category"] == "budget_exhausted" and record["stop_reason"] == "max_rounds"
    assert "last round: protocol_error" in record["stop_detail"]
    assert len(record["steps"]) == 3 and len(record["rounds"]) == 2
    assert (run_dir / "candidate" / "adapter.py").read_text() == WRONG_FIELD
    assert (run_dir / "final.diff").exists()  # the failed candidate is still reviewable


def test_the_step_limit_stops_endless_invalid_plans(tmp_path):
    spec = make_spec(tmp_path, max_model_steps=3)
    _, record, _ = attempt(spec, Script("no plan", "still no plan", "{}", PLAN))
    assert record["stop_reason"] == "max_model_steps" and len(record["steps"]) == 3
    assert record["plan"] is None


def test_the_token_limit_stops_before_the_next_call(tmp_path):
    def costly(text):
        return ModelOutput(text=text, usage=Usage(input_tokens=900, output_tokens=100))

    spec = make_spec(tmp_path, token_limit=1500)
    script = Script(costly(PLAN), costly(adapter(WRONG_FIELD)), costly(adapter(GOOD)))
    _, record, _ = attempt(spec, script)
    assert record["stop_reason"] == "token_limit" and len(record["steps"]) == 2
    assert record["usage"] == {"input_tokens": 1800, "output_tokens": 200}


def test_the_time_limit_stops_the_attempt(tmp_path):
    ticks = iter([0.0, 0.0, 10.0, 10.0, 10.0])
    spec = make_spec(tmp_path, time_limit_s=5)
    _, record, _ = attempt(spec, Script(PLAN, adapter(GOOD)), clock=lambda: next(ticks, 10.0))
    assert record["stop_reason"] == "time_limit" and len(record["steps"]) == 1


# --- 6. environment failures vs code failures ------------------------------------------------


class BrokenExecutor:
    def run(self, argv, *, cwd, stdin, timeout):
        raise SandboxError("docker daemon not reachable")

    def describe(self):
        return {"type": "broken"}


class TimeoutExecutor(SubprocessExecutor):
    def run(self, argv, *, cwd, stdin, timeout):
        return ExecResult(-1, "", "still running", timed_out=True)


def test_environment_failures_are_not_fed_back_as_code_errors(tmp_path):
    spec = make_spec(tmp_path)
    script = Script(PLAN, adapter(GOOD), adapter(GOOD))
    _, record, _ = attempt(spec, script, executor=BrokenExecutor())
    assert record["category"] == "env_error" and record["stop_reason"] == "env_error"
    assert len(script.requests) == 2  # the agent was not asked to "fix" the environment
    assert "docker daemon" in record["stop_detail"]


def test_a_validation_timeout_is_its_own_category(tmp_path):
    spec = make_spec(tmp_path, max_rounds=1)
    _, record, _ = attempt(spec, Script(PLAN, adapter(GOOD)), executor=TimeoutExecutor())
    assert record["rounds"][0]["category"] == "validation_timeout"


def test_a_failing_model_call_is_a_generation_failure(tmp_path):
    spec = make_spec(tmp_path)
    _, record, _ = attempt(spec, Script(PLAN, RuntimeError("server said no")))
    assert record["category"] == "generation_failed"
    assert "server said no" in record["steps"][-1]["error"]


def test_questions_stop_for_a_person(tmp_path):
    spec = make_spec(tmp_path)
    plan = json.loads(PLAN) | {"questions": ["Is 'La Paz' accepted for Bolivia?"]}
    _, record, model = attempt(spec, Script(json.dumps(plan)))
    assert record["category"] == "needs_human" and model.calls == 1
    assert record["questions"] == ["Is 'La Paz' accepted for Bolivia?"]


# --- 7. replay and re-verification -----------------------------------------------------------


def test_saved_replies_reproduce_the_attempt(tmp_path):
    spec = make_spec(tmp_path)
    cache = DiskCache(tmp_path / "cache")
    _, first, _ = attempt(spec, Script(PLAN, adapter(WRONG_FIELD), adapter(GOOD)), cache=cache)

    def no_calls(messages):
        raise AssertionError("the replay called the model")

    _, second, model = attempt(spec, no_calls, cache=cache)
    assert model.calls == 0 and all(s["cached"] for s in second["steps"])
    assert second["fingerprint"] == first["fingerprint"]
    assert [r["category"] for r in second["rounds"]] == [r["category"] for r in first["rounds"]]
    assert second["final_files"] == first["final_files"]


def test_a_changed_source_changes_the_fingerprint_and_is_not_reverified(tmp_path):
    spec = make_spec(tmp_path)
    run_dir, record, _ = attempt(spec, Script(PLAN, adapter(GOOD)))
    assert verify_attempt(run_dir)["passed"]

    rules = spec.source / "SCORING.md"
    rules.write_text(rules.read_text() + "\nAnswers must also name the country.\n")
    report = verify_attempt(run_dir)
    assert not report["passed"] and report["category"] == "source_changed"
    assert "does not carry over" in report["detail"]

    pinned = spec.model_copy(update={"source_sha256": fingerprint_dir(spec.source)})
    _, again, _ = attempt(pinned, Script(PLAN, adapter(GOOD)))
    assert again["fingerprint"] != record["fingerprint"]


def test_reverification_notices_edited_candidate_files(tmp_path):
    spec = make_spec(tmp_path)
    run_dir, _, _ = attempt(spec, Script(PLAN, adapter(GOOD)))
    (run_dir / "candidate" / "adapter.py").write_text(ALWAYS_CORRECT)
    report = verify_attempt(run_dir)
    assert report["category"] == "candidate_changed"
    assert len(list(run_dir.glob("verify-*.json"))) == 1


def test_validation_alone_rejects_an_always_correct_scorer(tmp_path):
    spec = make_spec(tmp_path)
    cand = tmp_path / "cand"
    cand.mkdir()
    (cand / "adapter.py").write_text(ALWAYS_CORRECT)
    report = validate(spec, cand)
    wrongly_correct = [c for c in report["cases"] if c["expect"] == "wrong" and not c["passed"]]
    assert report["category"] == "scoring_error" and len(wrongly_correct) == 5 + 6


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_exported_diff_applies_and_registers_the_task(tmp_path):
    import subprocess
    import sys

    spec = make_spec(tmp_path)
    run_dir, _, _ = attempt(spec, Script(PLAN, adapter(GOOD)))
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "src", repo / "src", ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run(["git", "apply", str(run_dir / "final.diff")], cwd=repo, check=True)
    shutil.copytree(spec.source / "data", repo / "src/harness/tasks/world_capitals_data")
    script = (
        "from harness.core.task import get_task\n"
        "t = get_task('world-capitals')\n"
        "s = t.load()\n"
        "print(len(s), t.scorer(s[2], 'Brasilia').value, t.dataset.revision)\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo,
        env={"PYTHONPATH": str(repo / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    n, value, revision = out.stdout.split()
    assert (n, value) == ("6", "1.0") and revision == fingerprint_dir(spec.source / "data")
    # the repo itself was not touched
    assert "world_capitals" not in (ROOT / "src/harness/tasks/__init__.py").read_text()


# --- parsing, spec and command line ------------------------------------------------------------


def test_file_blocks_may_be_fenced():
    text = '<file path="adapter.py">\n```python\nx = 1\n```\n</file>\n<question> why? </question>'
    assert parse_files(text) == ({"adapter.py": "x = 1\n"}, ["why?"])


def test_the_shipped_spec_pins_the_example_source():
    spec = load_spec(ROOT / "configs" / "onboard-world-capitals.yaml")
    assert spec.source_sha256 == fingerprint_dir(ROOT / spec.source)
    assert spec.model is None  # the model is chosen on the command line


def test_cli_onboard_and_verify(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from harness.cli import app

    spec = make_spec(tmp_path)
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(spec.model_dump(mode="json")))
    # none/none cannot answer: the attempt ends as a generation failure, not a crash
    result = CliRunner().invoke(app, ["onboard", "-c", str(path), "-m", "none/none", "--no-cache"])
    assert result.exit_code == 0, result.output
    assert "generation_failed" in result.output

    run_dir, _, _ = attempt(spec, Script(PLAN, adapter(GOOD)))
    ok = CliRunner().invoke(app, ["onboard-verify", str(run_dir)])
    assert ok.exit_code == 0 and "passed" in ok.output
