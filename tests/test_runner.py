import asyncio

import pytest
from conftest import make_samples, make_task, question_number

from harness.core.cache import DiskCache
from harness.core.model import MockModel, RetryableError
from harness.core.runner import run_samples
from harness.core.types import ErrorKind, GenerateConfig, ModelOutput, Outcome, Score
from harness.sandbox.base import SandboxError


def run(task, model, **kwargs):
    kwargs.setdefault("retry_base_delay", 0.0)
    return asyncio.run(run_samples(task, task.load(), model, GenerateConfig(), **kwargs))


def test_outcomes_are_classified(toy_task):
    def respond(messages):
        n = question_number(messages)
        if n == 0:
            return "The answer is 0."
        if n == 1:
            return "The answer is 3."
        if n == 2:
            return "no idea"
        if n == 3:
            return ModelOutput(text="Let me think about 3 + 3 and", finish_reason="length")
        if n == 4:
            raise ValueError("boom")
        return "#### 10"

    results = run(toy_task, MockModel(respond))
    assert [r.outcome for r in results] == [
        Outcome.CORRECT,
        Outcome.WRONG,
        Outcome.EXTRACT_FAILED,
        Outcome.TRUNCATED,
        Outcome.API_ERROR,
        Outcome.CORRECT,
    ]
    assert "ValueError: boom" in results[4].error


class _Scorer:
    name = "fixed"

    def __init__(self, behaviour):
        self.behaviour = behaviour

    def __call__(self, sample, text):
        return self.behaviour()

    def describe(self):
        return {"name": self.name}


def _with_scorer(behaviour):
    task = make_task(make_samples(1))
    task.scorer = _Scorer(behaviour)
    return task


def _raise(exc):
    def behaviour():
        raise exc

    return behaviour


@pytest.mark.parametrize(
    ("behaviour", "error"),
    [
        (_raise(KeyError("scorer bug")), "KeyError"),
        (lambda: Score.model_construct(value=float("nan")), "invalid value"),
        (lambda: 1.0, "scorer returned float, not Score"),
        (lambda: Score(value=float("nan")), "ValidationError"),
    ],
)
def test_scorer_failures_are_not_blamed_on_the_model(behaviour, error):
    [r] = run(_with_scorer(behaviour), MockModel(lambda m: "The answer is 0"))
    assert r.outcome == Outcome.SCORER_ERROR
    assert r.error_kind == ErrorKind.SCORER
    assert error in r.error
    assert r.output is not None and r.output.text == "The answer is 0"


def test_sandbox_error_in_scorer_is_env_error():
    [r] = run(_with_scorer(_raise(SandboxError("docker died"))), MockModel(lambda m: "x"))
    assert r.outcome == Outcome.ENV_ERROR


def test_solver_bug_is_solver_error_and_model_failure_is_api_error():
    class BrokenSolver:
        name = "broken"

        async def __call__(self, sample, generate, sandbox=None):
            await generate([{"role": "user", "content": "hi"}])
            raise IndexError("agent bug")

        def describe(self):
            return {"name": self.name}

    task = make_task(make_samples(1))
    task.solver = BrokenSolver()
    [r] = run(task, MockModel(lambda m: "ok"))
    assert (r.outcome, r.error_kind) == (Outcome.SOLVER_ERROR, ErrorKind.SOLVER)
    assert r.error == "IndexError: agent bug"

    def down(messages):
        raise ValueError("bad request")

    [r] = run(task, MockModel(down))
    assert (r.outcome, r.error_kind) == (Outcome.API_ERROR, ErrorKind.MODEL)
    assert r.error == "ValueError: bad request"


def test_summary_stays_finite_and_counts_every_failure_kind():
    from harness.analysis.stats import summarize

    results = run(_with_scorer(_raise(KeyError("x"))), MockModel(lambda m: "1"))
    s = summarize(results)
    assert s["accuracy"] == 0.0
    assert s["outcomes"]["scorer_error"] == 1
    assert set(s["outcomes"]) >= {"api_error", "solver_error", "scorer_error", "env_error"}


def test_retryable_errors_are_retried(toy_task):
    failures = {"left": 2}

    def respond(messages):
        if question_number(messages) == 1 and failures["left"]:
            failures["left"] -= 1
            raise RetryableError("rate limited")
        return f"The answer is {2 * question_number(messages)}"

    results = run(toy_task, MockModel(respond), retries=3)
    assert all(r.outcome == Outcome.CORRECT for r in results)
    assert results[1].attempts == 3


def test_non_retryable_errors_fail_fast(toy_task):
    def respond(messages):
        raise ValueError("bad request")

    model = MockModel(respond)
    results = run(toy_task, model, retries=3)
    assert model.calls == len(results)
    assert all(r.outcome == Outcome.API_ERROR for r in results)


def test_retries_are_bounded(toy_task):
    def respond(messages):
        raise RetryableError("still down")

    model = MockModel(respond)
    results = run(toy_task, model, retries=2)
    assert model.calls == 3 * len(results)


def test_timeouts_are_retried(toy_task):
    class Slow(MockModel):
        async def generate(self, messages, config, tools=None):
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(1)
            return ModelOutput(text=f"The answer is {2 * question_number(messages)}")

    task = make_task(make_samples(1))
    model = Slow(lambda m: "")
    results = asyncio.run(
        run_samples(task, task.load(), model, GenerateConfig(timeout=0.05), retry_base_delay=0)
    )
    assert results[0].outcome == Outcome.CORRECT
    assert results[0].attempts == 2


def test_cache_makes_reruns_free(tmp_path, toy_task):
    model = MockModel(lambda m: f"The answer is {2 * question_number(m)}")
    cache = DiskCache(tmp_path)
    first = run(toy_task, model, cache=cache)
    assert model.calls == 6
    second = run(toy_task, model, cache=cache)
    assert model.calls == 6
    assert all(r.output.cached for r in second)
    assert [r.output.text for r in first] == [r.output.text for r in second]


def test_cache_resumes_after_partial_failure(tmp_path, toy_task):
    cache = DiskCache(tmp_path)
    flaky = MockModel(
        lambda m: (_ for _ in ()).throw(ValueError("x")) if question_number(m) >= 3 else "#### 0"
    )
    run(toy_task, flaky, cache=cache)
    healthy = MockModel(lambda m: "#### 0")
    run(toy_task, healthy, cache=cache)
    assert healthy.calls == 3


def test_cache_key_depends_on_generate_config(tmp_path, toy_task):
    model = MockModel(lambda m: "#### 0")
    cache = DiskCache(tmp_path)
    samples = toy_task.load()
    asyncio.run(run_samples(toy_task, samples, model, GenerateConfig(), cache=cache))
    asyncio.run(run_samples(toy_task, samples, model, GenerateConfig(temperature=0.7), cache=cache))
    assert model.calls == 12


def test_concurrency_limit_is_respected():
    active = {"now": 0, "max": 0}

    class Tracking(MockModel):
        async def generate(self, messages, config, tools=None):
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.01)
            active["now"] -= 1
            return ModelOutput(text="#### 0")

    task = make_task(make_samples(20))
    asyncio.run(
        run_samples(task, task.load(), Tracking(lambda m: ""), GenerateConfig(), concurrency=3)
    )
    assert active["max"] == 3
