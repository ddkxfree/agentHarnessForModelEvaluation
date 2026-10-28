"""The onboarding agent with scripted MockModel replies and real validation subprocesses.

The scripted candidates are trusted, so most tests validate them with the subprocess executor;
the container isolation tests at the end need Docker."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
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


def make_spec(tmp_path: Path, *, executor="subprocess", imports=(), **budget) -> OnboardSpec:
    source = tmp_path / "source"
    if not source.exists():
        shutil.copytree(EXAMPLE, source)
    spec = OnboardSpec(
        task="world-capitals",
        source=source,
        source_sha256=fingerprint_dir(source),
        executor=executor,
        budget=Budget(**budget),
        output_dir=tmp_path / "runs",
    )
    spec.allowed_imports += list(imports)
    return spec


def attempt(spec, script, model=None, **kwargs):
    model = model or MockModel(script, name="scripted")
    kwargs.setdefault("retry_base_delay", 0)
    run_dir = asyncio.run(OnboardAgent(spec, model, **kwargs).run())
    return run_dir, json.loads((run_dir / "attempt.json").read_text()), model


def check(spec, tmp_path, text, test=None):
    cand = tmp_path / "cand"
    cand.mkdir(exist_ok=True)
    (cand / "adapter.py").write_text(text)
    if test is not None:
        (cand / "test_adapter.py").write_text(test)
    return validate(spec, cand)


# --- 1. a valid adapter --------------------------------------------------------------------


def test_a_valid_adapter_passes_independent_validation(tmp_path):
    spec = make_spec(tmp_path)
    run_dir, record, _ = attempt(spec, Script(PLAN, adapter(GOOD)))
    assert record["status"] == "passed" and record["stop_reason"] == "validated"
    assert record["category"] is None and len(record["rounds"]) == 1
    report = record["rounds"][0]["validation"]
    assert [c["name"] for c in report["checks"]] == [
        "static",
        "import",
        "protocol",
        "fields",
        "prompts",
        "cases",
    ]
    assert report["executor"]["isolation"] == "none"
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
        "expected.jsonl",
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


class SlowModel(MockModel):
    """Waits ``delays[n]`` seconds (really, in the event loop) before its n-th reply."""

    def __init__(self, script, *delays: float) -> None:
        super().__init__(script, name="slow")
        self.delays = list(delays)

    async def generate(self, messages, config, tools=None):
        await asyncio.sleep(self.delays[self.calls] if self.calls < len(self.delays) else 0)
        return await super().generate(messages, config, tools)


def test_the_time_limit_cuts_a_model_call_short(tmp_path):
    spec = make_spec(tmp_path, time_limit_s=0.3)
    model = SlowModel(Script(PLAN, adapter(GOOD)), 0, 5.0)
    started = time.monotonic()
    run_dir, record, _ = attempt(spec, None, model=model)
    assert time.monotonic() - started < 2.0
    assert record["status"] == "failed" and record["stop_reason"] == "time_limit"
    assert record["category"] == "budget_exhausted" and "model step 2" in record["stop_detail"]
    assert "TimeoutError" in record["steps"][-1]["error"]
    assert record["rounds"] == [] and (run_dir / "conversation.json").exists()


def test_the_time_limit_bounds_retry_backoff(tmp_path):
    spec = make_spec(tmp_path, time_limit_s=0.3)
    script = Script(PLAN, TimeoutError("slow server"), adapter(GOOD))
    started = time.monotonic()
    _, record, _ = attempt(spec, script, retry_base_delay=10.0)
    assert time.monotonic() - started < 2.0  # the first backoff alone would be 5-10s
    assert record["stop_reason"] == "time_limit" and record["steps"][-1]["attempts"] == 1


SLEEPS = """\
import os
import time
from pathlib import Path

Path({pid_file!r}).write_text(str(os.getpid()))
time.sleep(60)
"""


def sleeping_adapter(pid_file: Path) -> str:
    return SLEEPS.format(pid_file=str(pid_file)) + GOOD.split("\n", 1)[1]


def gone(pid_file: Path, within: float = 5.0) -> bool:
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_the_time_limit_bounds_validation_and_kills_it(tmp_path):
    spec = make_spec(tmp_path, imports=["os", "time"], time_limit_s=1.5, validation_timeout_s=60)
    pid_file = tmp_path / "check.pid"
    started = time.monotonic()
    _, record, _ = attempt(spec, Script(PLAN, adapter(sleeping_adapter(pid_file))))
    assert time.monotonic() - started < 10
    assert record["stop_reason"] == "time_limit" and record["status"] == "failed"
    assert record["rounds"][0]["category"] == "validation_timeout"
    assert gone(pid_file)


def test_cancelling_an_attempt_kills_the_validation(tmp_path):
    spec = make_spec(tmp_path, imports=["os", "time"], validation_timeout_s=60)
    pid_file = tmp_path / "check.pid"
    agent = OnboardAgent(spec, MockModel(Script(PLAN, adapter(sleeping_adapter(pid_file)))))

    async def main():
        task = asyncio.create_task(agent.run())
        while not pid_file.exists():
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    started = time.monotonic()
    asyncio.run(main())
    assert time.monotonic() - started < 15
    assert gone(pid_file)
    record = json.loads((agent.dir / "attempt.json").read_text())
    assert record["status"] == "failed" and record["stop_reason"] == "cancelled"
    assert record["final_files"]  # the written candidate is kept


class LateExecutor(SubprocessExecutor):
    """Ignores the time limit it is given, so a passing report arrives after the deadline."""

    def run(self, argv, *, workdir, timeout):
        time.sleep(0.5)
        return super().run(argv, workdir=workdir, timeout=60)


def test_a_passing_validation_after_the_deadline_does_not_count(tmp_path):
    spec = make_spec(tmp_path, time_limit_s=0.4)
    _, record, _ = attempt(spec, Script(PLAN, adapter(GOOD)), executor=LateExecutor())
    assert record["rounds"][0]["validation"]["passed"]
    assert record["status"] == "failed" and record["stop_reason"] == "time_limit"
    assert "validation of round 1" in record["stop_detail"]


# --- 6. environment failures vs code failures ------------------------------------------------


class BrokenExecutor(SubprocessExecutor):
    def run(self, argv, *, workdir, timeout):
        raise SandboxError("docker daemon not reachable")


class TimeoutExecutor(SubprocessExecutor):
    def run(self, argv, *, workdir, timeout):
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
    report = check(spec, tmp_path, ALWAYS_CORRECT)
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
    assert spec.executor == "docker"  # model-written code is never run on the host by default
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


# --- 8. field meaning and what the model is shown --------------------------------------------

QUESTION = 'input=TEMPLATE.format(country=r["country"])'
LOADS = GOOD.replace("def load_samples", "_LOADS = []\n\n\ndef load_samples").replace(
    "    return [\n        Sample(", "    _LOADS.append(1)\n    return [\n        Sample("
)


@pytest.mark.parametrize(
    "broken, category, detail",
    [
        (GOOD.replace(QUESTION, 'input=""'), "field_error", "input is empty"),
        (GOOD.replace(QUESTION, 'input=r["capital"]'), "field_error", "input is 'Paris'"),
        (GOOD.replace(QUESTION, 'input=r["country"]'), "field_error", "input is 'France'"),
        (
            GOOD.replace("{country}?", "this country?"),
            "field_error",
            "expected 'What is the capital of France?",
        ),
        (
            GOOD.replace('target=r["capital"]', 'target=r["country"]'),
            "field_error",
            "target is 'France', expected 'Paris'",
        ),
        (
            LOADS.replace(QUESTION, QUESTION + ' + " " * len(_LOADS)'),
            "protocol_error",
            "two load() calls returned different samples",
        ),
        (
            GOOD.replace("PromptSolver(lambda s: s.input,", "PromptSolver(lambda s: s.target,"),
            "field_error",
            "the model is asked 'Paris'",
        ),
        (
            GOOD.replace(
                "PromptSolver(lambda s: s.input,",
                "PromptSolver(lambda s: s.input, fewshot=lambda s: [s], "
                "assistant_template=lambda s: s.target,",
            ),
            "field_error",
            "the answer is shown to the model",
        ),
    ],
    ids=[
        "empty-input",
        "target-as-input",
        "country-only",
        "question-without-country",
        "wrong-target",
        "content-changes-between-loads",
        "prompt-is-the-answer",
        "answer-in-a-fewshot-turn",
    ],
)
def test_frozen_expected_samples_catch_field_mistakes(tmp_path, broken, category, detail):
    assert broken != GOOD
    spec = make_spec(tmp_path)
    report = check(spec, tmp_path, broken)
    assert not report["passed"] and report["category"] == category, report.get("detail")
    assert detail in report["detail"]


def test_the_agent_never_sees_the_expected_samples_and_they_are_deleted_before_import(tmp_path):
    spec = make_spec(tmp_path)
    peek = GOOD.replace(
        "DEFAULT_DATA =",
        'assert not Path("payload.json").exists(), "payload leaked"\nDEFAULT_DATA =',
    )
    _, record, _ = attempt(spec, Script(PLAN, adapter(peek)))
    assert record["status"] == "passed"
    assert spec.path("expected.jsonl") in spec.protected()


# --- 9. isolation of candidate code (needs Docker) -------------------------------------------


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


needs_docker = pytest.mark.skipif(not _docker_available(), reason="Docker is not available")

ESCAPE = """\
from pathlib import Path

Path({outside!r}).write_text("escaped")
"""


def escaping_adapter(outside: Path) -> str:
    return ESCAPE.format(outside=str(outside)) + GOOD.split("\n", 1)[1]


def test_the_subprocess_executor_does_not_isolate_the_host(tmp_path):
    # why it is only for trusted candidates: a plain write lands on the host
    spec = make_spec(tmp_path)
    outside = tmp_path / "outside.txt"
    report = check(spec, tmp_path, escaping_adapter(outside))
    assert outside.exists() and report["passed"]


@needs_docker
def test_docker_blocks_adapter_writes_outside_its_scratch_directory(tmp_path):
    spec = make_spec(tmp_path, executor="docker")
    outside = tmp_path / "outside.txt"
    report = check(spec, tmp_path, escaping_adapter(outside))
    assert not report["passed"] and report["category"] == "import_error"
    assert not outside.exists()
    assert report["executor"]["isolation"] == "container"
    assert report["executor"]["network"] == "none"


@needs_docker
def test_docker_blocks_writes_to_the_harness_package(tmp_path):
    import harness

    spec = make_spec(tmp_path, executor="docker")
    # the package as the candidate sees it: mounted read-only inside the container
    write = (
        "import harness\nfrom pathlib import Path\n"
        "Path(harness.__file__).with_name('onboard_escape.txt').write_text('escaped')\n"
    )
    report = check(spec, tmp_path, write + GOOD.split("\n", 1)[1])
    assert report["category"] == "import_error" and "Read-only file system" in report["detail"]
    assert not (Path(harness.__file__).parent / "onboard_escape.txt").exists()


@needs_docker
def test_docker_blocks_self_test_writes_outside_and_the_network(tmp_path):
    spec = make_spec(tmp_path, executor="docker")
    outside = tmp_path / "from-self-test.txt"
    test = SELF_TEST + (
        "\n\ndef test_escape():\n"
        "    from pathlib import Path\n"
        f"    Path({str(outside)!r}).write_text('escaped')\n"
        "\n\ndef test_network():\n"
        "    import socket\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
    )
    report = check(spec, tmp_path, GOOD, test)
    assert report["passed"]  # the adapter itself is fine
    self_test = report["self_test"]
    assert self_test["status"] == "failed" and "2 failed, 1 passed" in self_test["output"]
    assert not outside.exists()


@needs_docker
def test_docker_validation_matches_the_subprocess_one(tmp_path):
    spec = make_spec(tmp_path, executor="docker")
    good = check(spec, tmp_path, GOOD)
    assert good["passed"] and len(good["cases"]) == 17
    wrong = check(spec, tmp_path, WRONG_FIELD)
    assert wrong["category"] == "protocol_error" and "<sandbox>" in wrong["detail"]
    assert "/work" not in wrong["detail"]


@needs_docker
def test_docker_kills_and_removes_containers_on_timeout_and_cancel(tmp_path):
    import threading

    import docker

    from harness.onboard.validator import DockerExecutor

    spec = make_spec(tmp_path, executor="docker", imports=["os", "time"])
    (tmp_path / "cand").mkdir()
    (tmp_path / "cand" / "adapter.py").write_text(sleeping_adapter(Path("/tmp/check.pid")))
    started = time.monotonic()
    report = validate(spec, tmp_path / "cand", timeout=3)
    assert report["category"] == "validation_timeout"
    assert time.monotonic() - started < 30

    executor = DockerExecutor()
    threading.Timer(3, executor.cancel).start()
    report = validate(spec, tmp_path / "cand", executor)
    assert report["category"] == "cancelled"
    leftover = docker.from_env().containers.list(all=True, filters={"label": "harness.onboard"})
    assert leftover == []


# --- 10. executor preparation and container start stay within their time ----------------------


class FakeContainer:
    id = "fake-container"

    def __init__(self, start_delay: float) -> None:
        self.start_delay, self.waited, self.on_start = start_delay, None, None
        self.killed = self.removed = self.started = False

    def start(self):
        self.started = True
        if self.on_start is not None:
            self.on_start()
        time.sleep(self.start_delay)

    def wait(self, timeout):
        self.waited = timeout
        return {"StatusCode": 1}

    def logs(self, stdout, stderr):
        return b""

    def kill(self):
        self.killed = True

    def remove(self, force):
        self.removed = True


class FakeDocker:
    """Images missing until ``present_after`` lookups; containers slow to create or start."""

    def __init__(self, present_after=0, create_delay=0.0, start_delay=0.0) -> None:
        self.lookups, self.present_after, self.create_delay = 0, present_after, create_delay
        self.container = FakeContainer(start_delay)
        self.images = self.containers = self
        self.created, self.on_create = 0, None

    def ping(self):
        return True

    def get(self, tag):
        import docker

        self.lookups += 1
        if self.lookups <= self.present_after:
            raise docker.errors.ImageNotFound(tag)
        return type("Image", (), {"id": "sha256:fake"})()

    def create(self, image, argv, **kwargs):
        self.created += 1
        if self.on_create is not None:
            self.on_create()
        time.sleep(self.create_delay)
        return self.container


def fake_executor(fake: FakeDocker, build: str = "pass"):
    from harness.onboard.validator import DockerExecutor

    class Executor(DockerExecutor):
        def build_argv(self):
            return [os.sys.executable, "-c", build]

    executor = Executor()
    executor._client = fake
    return executor


def slow_build(pid_file: Path) -> str:
    return f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"


def test_a_cold_image_build_has_its_own_limit_and_is_killed(tmp_path):
    spec = make_spec(tmp_path, executor="docker", time_limit_s=0.1, prepare_timeout_s=1.0)
    pid_file = tmp_path / "build.pid"
    executor = fake_executor(FakeDocker(present_after=1), slow_build(pid_file))
    started = time.monotonic()
    _, record, model = attempt(spec, Script(PLAN, adapter(GOOD)), executor=executor)
    assert time.monotonic() - started < 5
    assert record["stop_reason"] == "prepare_timeout" and record["category"] == "env_error"
    assert "out of time" in record["preparation"]["error"] and model.calls == 0
    assert gone(pid_file)


def test_a_ready_image_starts_the_attempt_clock_afterwards(tmp_path):
    # a 1s build does not use up a 0.8s attempt
    spec = make_spec(tmp_path, executor="docker", time_limit_s=0.8, max_rounds=1)
    executor = fake_executor(FakeDocker(present_after=1), "import time; time.sleep(1)")
    _, record, model = attempt(spec, Script(PLAN, adapter(GOOD)), executor=executor)
    assert record["preparation"]["built"] and record["preparation"]["tag"] == executor.tag()
    assert record["preparation"]["prepare_s"] >= 1.0
    assert model.calls == 2 and record["stop_reason"] == "max_rounds"
    assert record["rounds"][0]["category"] == "validation_crashed"  # the fake runs nothing


def test_cancelling_during_preparation_kills_the_build(tmp_path):
    spec = make_spec(tmp_path, executor="docker")
    pid_file = tmp_path / "build.pid"
    executor = fake_executor(FakeDocker(present_after=1), slow_build(pid_file))
    agent = OnboardAgent(spec, MockModel(Script(PLAN)), executor=executor)

    async def main():
        task = asyncio.create_task(agent.run())
        while not pid_file.exists():
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert gone(pid_file)
    record = json.loads((agent.dir / "attempt.json").read_text())
    assert record["stop_reason"] == "cancelled" and agent.model.calls == 0


def test_validation_charges_image_preparation_to_its_timeout(tmp_path):
    spec = make_spec(tmp_path, executor="docker")
    (tmp_path / "cand").mkdir()
    (tmp_path / "cand" / "adapter.py").write_text(GOOD)
    pid_file = tmp_path / "build.pid"
    executor = fake_executor(FakeDocker(present_after=1), slow_build(pid_file))
    started = time.monotonic()
    report = validate(spec, tmp_path / "cand", executor, timeout=0.5)
    assert time.monotonic() - started < 3
    assert report["category"] == "validation_timeout" and "preparing" in report["detail"]
    assert gone(pid_file)


def test_a_slow_container_create_is_bounded_and_the_late_container_removed(tmp_path):
    fake = FakeDocker(create_delay=1.0)
    executor = fake_executor(fake)
    started = time.monotonic()
    res = executor.run(["python", "-V"], workdir=tmp_path, timeout=0.3)
    assert res.timed_out and time.monotonic() - started < 0.8
    assert not fake.container.removed  # still being created
    time.sleep(1.0)
    assert fake.container.removed and fake.container.waited is None


def test_a_slow_container_start_is_bounded_killed_and_removed(tmp_path):
    fake = FakeDocker(start_delay=1.0)
    started = time.monotonic()
    res = fake_executor(fake).run(["python", "-V"], workdir=tmp_path, timeout=0.3)
    assert res.timed_out and time.monotonic() - started < 0.8
    assert fake.container.killed and fake.container.removed


def test_waiting_for_a_container_gets_only_the_time_left(tmp_path):
    fake = FakeDocker(create_delay=0.3, start_delay=0.2)
    fake_executor(fake).run(["python", "-V"], workdir=tmp_path, timeout=1.0)
    assert fake.container.waited <= 0.55 and fake.container.removed


@needs_docker
def test_docker_aborts_a_slow_image_build(tmp_path):
    import uuid

    import docker

    from harness.onboard.validator import DockerExecutor, PrepareTimeout

    marker = f"sleep 60 # {uuid.uuid4().hex}"

    class SlowBuild(DockerExecutor):
        def dockerfile(self):
            return f"FROM python:3.12-slim\nRUN {marker}\n"

    started = time.monotonic()
    with pytest.raises(PrepareTimeout):
        SlowBuild().prepare(timeout=4)
    assert time.monotonic() - started < 10
    client = docker.from_env()

    def building():
        return [c for c in client.containers.list(all=True) if marker in str(c.attrs["Args"])]

    deadline = time.monotonic() + 10
    while building() and time.monotonic() < deadline:
        time.sleep(0.2)
    assert building() == []


# --- 11. a cancelled executor starts nothing ------------------------------------------------


def test_a_cancelled_executor_creates_no_container(tmp_path):
    fake = FakeDocker()
    executor = fake_executor(fake)
    executor.prepare(10)
    executor.cancel()
    res = executor.run(["python", "-V"], workdir=tmp_path, timeout=5)
    assert res.cancelled and not res.timed_out
    assert fake.created == 0 and not fake.container.started and fake.container.waited is None


def test_a_cancel_while_the_container_is_created_removes_it_unstarted(tmp_path):
    fake = FakeDocker()
    executor = fake_executor(fake)
    executor.prepare(10)
    fake.on_create = executor.cancel  # the result comes back right after the cancel
    res = executor.run(["python", "-V"], workdir=tmp_path, timeout=5)
    assert res.cancelled
    assert not fake.container.started and fake.container.waited is None
    assert fake.container.removed


def test_a_cancel_while_the_container_starts_kills_it_before_waiting(tmp_path):
    fake = FakeDocker()
    executor = fake_executor(fake)
    executor.prepare(10)
    fake.container.on_start = executor.cancel
    res = executor.run(["python", "-V"], workdir=tmp_path, timeout=5)
    assert res.cancelled and fake.container.waited is None
    assert fake.container.killed and fake.container.removed


def test_a_cancelled_executor_starts_no_image_build(tmp_path):
    from harness.onboard.validator import PrepareTimeout

    pid_file = tmp_path / "build.pid"
    executor = fake_executor(FakeDocker(present_after=1), slow_build(pid_file))
    executor.cancel()
    with pytest.raises(PrepareTimeout) as err:
        executor.prepare(10)
    assert err.value.cancelled
    time.sleep(0.3)
    assert not pid_file.exists()


def test_a_cancelled_subprocess_executor_starts_no_process(tmp_path):
    executor = SubprocessExecutor()
    executor.cancel()
    marker = tmp_path / "ran"
    res = executor.run(["python", "-c", f"open({str(marker)!r}, 'w')"], workdir=tmp_path, timeout=5)
    assert res.cancelled and not marker.exists()


@pytest.mark.parametrize("kind", ["docker", "subprocess"])
def test_validating_with_a_cancelled_executor_is_reported_as_cancelled(tmp_path, kind):
    spec = make_spec(tmp_path, executor=kind)
    (tmp_path / "cand").mkdir()
    (tmp_path / "cand" / "adapter.py").write_text(GOOD)
    executor = fake_executor(FakeDocker()) if kind == "docker" else SubprocessExecutor()
    executor.prepare(10)
    executor.cancel()
    report = validate(spec, tmp_path / "cand", executor)
    assert not report["passed"] and report["category"] == "cancelled"
