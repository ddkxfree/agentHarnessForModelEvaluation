"""Failures keep their trajectory, results reach disk early, cleanup and time limits are tracked."""

import asyncio
import hashlib
import json
import time

import pytest
from conftest import make_task, question_number
from fakes import FakeProvider, FakeSandbox, call, scripted, tool_turn
from test_agent import agent_task, run

from harness.analysis.stats import summarize
from harness.core import runner as runner_mod
from harness.core.io import read_results
from harness.core.model import MockModel
from harness.core.runner import run_eval
from harness.core.types import ErrorKind, GenerateConfig, Outcome
from harness.sandbox.base import ExecResult
from harness.solvers.basic_agent import BasicAgent

ECHO = call("bash", '{"command": "echo hi > /app/x.txt"}')


def test_model_failure_on_second_call_keeps_the_first_step():
    def respond(messages):
        if any(m["role"] == "tool" for m in messages):
            raise ValueError("provider down")
        return tool_turn(ECHO, text="writing the file")

    (r,), provider = run(agent_task(), MockModel(respond))
    assert r.outcome == Outcome.API_ERROR and r.output is None
    assert r.error == "ValueError: provider down"
    (step,) = r.steps
    assert step.text == "writing the file"
    assert step.observations == ["exit_code=0\n"] and step.exit_codes == [0]
    assert [m["role"] for m in r.messages] == ["system", "user", "assistant", "tool"]
    assert provider.created[0].closed


class CrashingSandbox(FakeSandbox):
    async def exec(self, cmd, *, timeout=60.0):
        if cmd.startswith("cat"):
            raise RuntimeError("exec plumbing broke")
        return await super().exec(cmd, timeout=timeout)


def test_solver_crash_keeps_steps_up_to_the_crash():
    model = scripted(tool_turn(ECHO), tool_turn(call("bash", '{"command": "cat /app/x.txt"}')))
    (r,), _ = run(agent_task(), model, FakeProvider(sandbox_cls=CrashingSandbox))
    assert r.outcome == Outcome.SOLVER_ERROR
    assert len(r.steps) == 2
    assert r.steps[1].tool_calls[0].arguments == '{"command": "cat /app/x.txt"}'
    assert r.steps[1].observations == []


class VerboseSandbox(FakeSandbox):
    OUTPUT = "".join(f"line {i}\n" for i in range(500))

    async def exec(self, cmd, *, timeout=60.0):
        if cmd == "big":
            return ExecResult(exit_code=0, stdout=self.OUTPUT, stderr="warn")
        return await super().exec(cmd, timeout=timeout)


@pytest.mark.parametrize("with_dir", [True, False])
def test_truncated_tool_output_is_saved_as_an_artifact(tmp_path, with_dir):
    model = scripted(tool_turn(call("bash", '{"command": "big"}'), ECHO), tool_turn(ECHO))
    task = agent_task(BasicAgent(max_steps=2, max_observation_chars=200))
    kwargs = {"artifact_dir": tmp_path} if with_dir else {}
    (r,), _ = run(task, model, FakeProvider(sandbox_cls=VerboseSandbox), **kwargs)

    step = r.steps[0]
    assert "characters omitted" in step.observations[0]
    assert len(step.observations[0]) < 300
    assert step.raw_outputs == ["step-0-call-0.txt", None]
    (artifact,) = r.artifacts
    full = VerboseSandbox.OUTPUT + "\n[stderr]\nwarn"
    assert artifact.name == "step-0-call-0.txt"
    assert artifact.sha256 == hashlib.sha256(full.encode()).hexdigest()
    assert artifact.size == len(full.encode())
    if with_dir:
        assert artifact.path.endswith("/step-0-call-0.txt")
        assert (tmp_path / artifact.path).read_text() == full
    else:
        assert artifact.path is None


class SlowModel(MockModel):
    """Answers instantly except for question 3, which never returns."""

    async def generate(self, messages, config, tools=None):
        if question_number(messages) == 3:
            await asyncio.sleep(3600)
        return await super().generate(messages, config, tools)


def test_completed_samples_are_on_disk_before_the_run_finishes(tmp_path):
    model = SlowModel(lambda m: f"The answer is {2 * question_number(m)}")

    async def interrupted_run():
        job = asyncio.create_task(
            run_eval(model, [(make_task(), GenerateConfig())], output_dir=tmp_path, concurrency=1)
        )
        for _ in range(200):
            files = list(tmp_path.glob("*/toy/samples.jsonl"))
            if files and len(files[0].read_text().splitlines()) == 3:
                break
            await asyncio.sleep(0.01)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job

    asyncio.run(interrupted_run())
    (run_dir,) = tmp_path.iterdir()
    assert json.loads((run_dir / "manifest.json").read_text())["status"] == "interrupted"
    results = read_results(run_dir / "toy/samples.jsonl")
    assert [r.sample.id for r in results] == ["toy/0", "toy/1", "toy/2"]
    assert all(r.outcome == Outcome.CORRECT for r in results)


class LeakySandbox(FakeSandbox):
    async def close(self):
        raise RuntimeError("container is stuck")

    def describe(self):
        return {"type": "fake", "container_id": "c0ffee"}


class HangingSandbox(FakeSandbox):
    async def close(self):
        await asyncio.sleep(3600)


def test_cleanup_failure_is_recorded_with_the_sandbox_identity():
    model = scripted(tool_turn(call("submit", "{}")))
    (r,), _ = run(agent_task(), model, FakeProvider(sandbox_cls=LeakySandbox))
    assert r.outcome == Outcome.CORRECT
    assert r.sandbox == {"type": "fake", "container_id": "c0ffee"}
    assert r.cleanup_error == "RuntimeError: container is stuck"
    assert summarize([r])["cleanup_errors"] == 1


def test_hanging_cleanup_times_out(monkeypatch):
    monkeypatch.setattr(runner_mod, "CLOSE_TIMEOUT", 0.05)
    model = scripted(tool_turn(call("submit", "{}")))
    (r,), _ = run(agent_task(), model, FakeProvider(sandbox_cls=HangingSandbox))
    assert r.cleanup_error.startswith("TimeoutError")
    assert r.sandbox == {"type": "HangingSandbox"}


class BlockingSandbox(FakeSandbox):
    """``sleep`` blocks until cancelled; records whether the command was cancelled."""

    def __init__(self):
        super().__init__()
        self.cancelled = False

    async def exec(self, cmd, *, timeout=60.0):
        if cmd.splitlines()[-1] == "sleep 999":
            self.commands.append(cmd)
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return await super().exec(cmd, timeout=timeout)


SLEEP = call("bash", '{"command": "sleep 999"}')


@pytest.mark.parametrize("passes", [False, True])
def test_time_limit_cancels_the_agent_and_still_scores(passes):
    task = agent_task()
    task.time_limit = 0.2
    provider = FakeProvider(sandbox_cls=BlockingSandbox)
    provider.test_exit_code = 0 if passes else 1
    start = time.monotonic()
    (r,), _ = run(task, scripted(tool_turn(ECHO), tool_turn(SLEEP)), provider)

    assert time.monotonic() - start < 5
    assert r.outcome == (Outcome.CORRECT if passes else Outcome.TIME_LIMIT)
    assert r.error is None and r.output.finish_reason == "time_limit"
    assert len(r.steps) == 2 and r.steps[1].observations == []
    sandbox = provider.created[0]
    assert sandbox.cancelled and sandbox.closed
    assert sandbox.commands[-1] == "bash /tests/test.sh"


def test_time_limit_is_in_the_manifest(tmp_path):
    task = make_task()
    task.time_limit = 12.5
    run_dir = asyncio.run(
        run_eval(MockModel(lambda m: "1"), [(task, GenerateConfig())], output_dir=tmp_path)
    )
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["tasks"][0]["time_limit"] == 12.5
    assert manifest["results"]["toy"]["outcomes"]["time_limit"] == 0


def test_solver_errors_are_not_mistaken_for_the_time_limit():
    class TimeoutSolver(BasicAgent):
        async def __call__(self, sample, generate, sandbox=None):
            raise TimeoutError("solver's own timeout")

    task = agent_task(TimeoutSolver())
    task.time_limit = 60
    (r,), _ = run(task, scripted("x"))
    assert r.error_kind == ErrorKind.SOLVER and r.outcome == Outcome.SOLVER_ERROR
