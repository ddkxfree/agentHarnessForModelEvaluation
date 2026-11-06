"""Evidence survives interleaving, retries, cancellation and an unavailable grader."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from conftest import make_samples, make_task
from fakes import FakeProvider, FakeSandbox, call, scripted, tool_turn
from test_agent import agent_task

from harness.analysis.stats import summarize
from harness.core.cache import DiskCache
from harness.core.judge import JudgePanel, JudgeSpec
from harness.core.model import MockModel, OpenAICompatibleModel, RetryableError
from harness.core.observability import call_context
from harness.core.runner import run_samples
from harness.core.types import GenerateConfig, JudgeRecord, ModelOutput, SampleResult, Usage
from harness.sandbox.base import SandboxError


def evidence(root):
    checkpoints = sorted(root.rglob("checkpoint.json"))
    return [
        (
            json.loads(p.read_text()),
            [json.loads(x) for x in p.with_name("events.jsonl").read_text().splitlines()],
        )
        for p in checkpoints
    ]


def test_concurrent_requests_remain_bound_to_their_attempt_and_raw_reply(tmp_path):
    class Interleaved(MockModel):
        async def generate(self, messages, config, tools=None):
            context = dict(call_context.get())
            await asyncio.sleep(0.001)
            assert call_context.get() == context
            return ModelOutput(
                text=messages[-1]["content"],
                raw_response={"id": context["call_id"]},
                usage=Usage(input_tokens=13, output_tokens=2),
            )

    task = make_task(make_samples(6))
    results = asyncio.run(
        run_samples(
            task,
            task.load(),
            Interleaved(lambda m: ""),
            GenerateConfig(),
            concurrency=2,
            artifact_dir=tmp_path,
            run_id="run-test",
        )
    )
    assert len({r.attempt_id for r in results}) == 6
    assert len({r.model_calls[0]["call_id"] for r in results}) == 6
    for checkpoint, events in evidence(tmp_path):
        assert checkpoint["run_id"] == "run-test"
        assert checkpoint["assessment_status"] == "completed"
        assert all(e["attempt_id"] == checkpoint["attempt_id"] for e in events)
        reply = next(e for e in events if e["kind"] == "model_call_completed")
        assert reply["raw_response"]["id"] == reply["call_id"]
        assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert summarize(results)["tokens"] == {"input": 78, "output": 12}


def test_each_retry_has_a_request_id_and_unknown_usage_is_explicit(tmp_path):
    count = 0

    def respond(messages):
        nonlocal count
        count += 1
        if count == 1:
            raise RetryableError("transient")
        return ModelOutput(text="0", usage=Usage(input_tokens=10, output_tokens=3))

    task = make_task(make_samples(1))
    [result] = asyncio.run(
        run_samples(
            task,
            task.load(),
            MockModel(respond),
            GenerateConfig(),
            retries=1,
            retry_base_delay=0,
            artifact_dir=tmp_path,
        )
    )
    a, b = result.model_calls
    assert a["call_id"] != b["call_id"]
    assert a["logical_call_id"] == b["logical_call_id"]
    assert (a["status"], a["usage"], b["status"]) == ("error", None, "completed")
    assert result.usage().total == 13
    assert summarize([result])["usage_coverage"]["requests_with_unknown_usage"] == 1


def test_cache_replay_rebinds_call_identity_without_charging_fresh_usage(tmp_path):
    task = make_task(make_samples(1))
    cache = DiskCache(tmp_path / "cache")
    model = MockModel(lambda m: ModelOutput(text="0", usage=Usage(input_tokens=9, output_tokens=2)))

    async def run():
        first = await run_samples(task, task.load(), model, GenerateConfig(), cache=cache)
        second = await run_samples(task, task.load(), model, GenerateConfig(), cache=cache)
        return first[0], second[0]

    first, second = asyncio.run(run())
    assert model.calls == 1
    assert first.run_id != second.run_id and first.attempt_id != second.attempt_id
    assert first.output.call_id != second.output.call_id
    assert second.output.origin_call_id == first.output.call_id
    assert second.calls.fresh_usage.total == 0 and second.usage().total == 11


@pytest.mark.parametrize("where", ["request", "tool"])
def test_cancel_saves_inflight_request_or_tool_and_queued_attempt(tmp_path, where):
    async def exercise():
        entered = asyncio.Event()

        class WaitingSandbox(FakeSandbox):
            async def exec(self, cmd, *, timeout=60):
                entered.set()
                await asyncio.Event().wait()

        class WaitingModel(MockModel):
            async def generate(self, messages, config, tools=None):
                entered.set()
                await asyncio.Event().wait()

        task = agent_task(n=2)
        model = (
            WaitingModel(lambda m: "")
            if where == "request"
            else scripted(tool_turn(call("bash", '{"command":"wait"}')))
        )
        work = asyncio.create_task(
            run_samples(
                task,
                task.load(),
                model,
                GenerateConfig(),
                concurrency=1,
                artifact_dir=tmp_path,
                sandbox_provider=FakeProvider(sandbox_cls=WaitingSandbox),
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work

    asyncio.run(exercise())
    records = evidence(tmp_path)
    assert len(records) == 2
    assert all(c["generation_status"] == "cancelled" for c, _ in records)
    active, events = next((c, e) for c, e in records if c["model_calls"])
    if where == "request":
        assert active["model_calls"][0]["status"] == "cancelled"
    else:
        assert active["steps"][0]["call_id"] == active["model_calls"][0]["call_id"]
        assert any(e["kind"] == "tool_interrupted" for e in events)
    assert any(c["generation_finish_reason"] == "cancelled_before_start" for c, _ in records)


def test_budget_stop_and_grader_environment_failure_are_independent(tmp_path):
    class BrokenScorer:
        name = "broken"

        def __call__(self, sample, text):
            raise SandboxError("verifier unavailable")

        def describe(self):
            return {"name": self.name}

    task = agent_task(scorer=BrokenScorer())
    task.token_limit = 1
    model = scripted(
        ModelOutput(
            text="working",
            tool_calls=[call("bash", '{"command":"pwd"}')],
            usage=Usage(input_tokens=2, output_tokens=1),
        )
    )
    [result] = asyncio.run(
        run_samples(
            task,
            task.load(),
            model,
            GenerateConfig(),
            artifact_dir=tmp_path,
            sandbox_provider=FakeProvider(),
        )
    )
    assert result.generation_status == "budget_stopped"
    assert result.generation_finish_reason == "token_limit"
    assert result.assessment_status == "environment_failed"
    assert result.usage().total == 3


def test_evidence_redacts_secrets_without_changing_model_inputs(tmp_path, monkeypatch):
    secret = "synthetic-test-credential-123456"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    task = make_task(make_samples(1))
    task.load()[0].input = secret
    seen = []
    model = MockModel(lambda messages: seen.append(messages[-1]["content"]) or secret)
    asyncio.run(run_samples(task, task.load(), model, GenerateConfig(), artifact_dir=tmp_path))
    assert seen == [secret]
    assert all(secret not in p.read_text() for p in tmp_path.rglob("*.json*"))
    assert any("[REDACTED]" in p.read_text() for p in tmp_path.rglob("*.json*"))


def test_provider_usage_subsets_and_correlation_headers_are_saved():
    from openai.types.chat import ChatCompletion

    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return ChatCompletion.model_validate(
            {
                "id": "provider-id",
                "object": "chat.completion",
                "created": 0,
                "model": "flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                    "prompt_cache_hit_tokens": 80,
                    "completion_tokens_details": {"reasoning_tokens": 5},
                },
            }
        )

    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model.name = "flash"
    model._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    token = call_context.set({"run_id": "r", "attempt_id": "a", "call_id": "c"})
    try:
        out = asyncio.run(model.generate([], GenerateConfig()))
    finally:
        call_context.reset(token)
    assert captured["extra_headers"]["X-Harness-call-id"] == "c"
    assert out.raw_response["usage"]["prompt_cache_hit_tokens"] == 80
    assert out.provider_response_id == "provider-id"
    assert out.usage.total == 120
    assert out.usage.cached_input_tokens == 80 and out.usage.reasoning_output_tokens == 5
    assert out.usage.cache_included_in_input and out.usage.reasoning_included_in_output


def test_unknown_tokens_do_not_become_zero_and_old_records_stay_unknown():
    known = Usage(input_tokens=10, output_tokens=2)
    unknown = Usage(input_tokens=None, output_tokens=None, source="unreported")
    assert (known + unknown).input_tokens is None
    old = SampleResult.model_validate({"sample": make_samples(1)[0].model_dump()})
    assert old.record_schema_version == 1 and old.generation_status == "unknown"
    result = old.model_copy(
        update={"model_calls": [{"status": "completed", "usage": unknown.model_dump()}]}
    )
    assert summarize([result])["tokens"] == {"input": None, "output": None}


def test_judge_retries_keep_individual_request_evidence(tmp_path):
    # Use the same active trajectory as a real scorer, while keeping judge billing separate.
    from harness.core.observability import Journal
    from harness.core.trajectory import Trajectory, recording

    count = 0

    def respond(messages):
        nonlocal count
        count += 1
        if count == 1:
            raise RetryableError("retry")
        return ModelOutput(
            text="pass",
            raw_response={"id": "judge-provider"},
            usage=Usage(input_tokens=4, output_tokens=1),
        )

    panel = JudgePanel(
        [(JudgeSpec(name="j", model="mock/j"), MockModel(respond))], retries=1, retry_base_delay=0
    )
    record = JudgeRecord()
    trajectory = Trajectory()
    trajectory.journal = Journal(tmp_path, {"run_id": "r", "attempt_id": "a"})
    trajectory.snapshot = lambda: {"judge": record.model_dump()}
    with recording(trajectory):
        result = asyncio.run(panel.call("j", [], purpose="grade", record=record))
    assert len(result.requests) == 2
    assert result.requests[0]["call_id"] != result.requests[1]["call_id"]
    assert result.requests[0]["usage"] is None
    assert result.usage.total == 5
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e.get("raw_response") == {"id": "judge-provider"} for e in events)
