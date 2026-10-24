import asyncio
from pathlib import Path

import pytest
from fakes import FakeProvider, FakeSandbox, call, scripted, tool_turn

from harness.analysis.stats import summarize
from harness.core.cache import DiskCache, cache_key
from harness.core.runner import run_samples
from harness.core.task import DatasetSpec, Task, get_task
from harness.core.types import GenerateConfig, Outcome, Sample
from harness.sandbox.base import ExecResult, SandboxSpec
from harness.solvers.basic_agent import BasicAgent
from harness.tasks.terminal import DATA_DIR, TestScriptScorer, discover, load_task_dir


def agent_task(solver=None, n=1, scorer=None) -> Task:
    samples = [
        Sample(
            id=f"t/{i}",
            input="make a file",
            target="tests pass",
            metadata={"task_dir": str(DATA_DIR / "hello-file")},
        )
        for i in range(n)
    ]
    return Task(
        name="agent_toy",
        dataset=DatasetSpec(path="local", split="test"),
        load=lambda: samples,
        solver=solver or BasicAgent(max_steps=5),
        scorer=scorer or TestScriptScorer(),
        sandbox=lambda s: SandboxSpec(image="unused"),
    )


def run(task, model, provider=None, **kwargs):
    provider = provider or FakeProvider()
    results = asyncio.run(
        run_samples(
            task,
            task.load(),
            model,
            GenerateConfig(),
            sandbox_provider=provider,
            retry_base_delay=0,
            **kwargs,
        )
    )
    return results, provider


def test_agent_runs_tools_in_sandbox_then_submits():
    model = scripted(
        tool_turn(call("bash", '{"command": "echo hello > /app/x.txt"}')),
        tool_turn(call("bash", '{"command": "cat /app/x.txt"}')),
        tool_turn(call("submit", '{"answer": "done"}')),
    )
    (r,), provider = run(agent_task(), model)

    assert r.outcome == Outcome.CORRECT
    assert r.output.finish_reason == "submit"
    assert r.output.text == "done"
    assert len(r.steps) == 3
    assert r.steps[1].observations[0] == "exit_code=0\nhello\n"
    assert r.steps[1].exit_codes == [0]
    sandbox = provider.created[0]
    assert sandbox.commands[:2] == ["echo hello > /app/x.txt", "cat /app/x.txt"]
    assert sandbox.commands[-1] == "bash /tests/test.sh"
    assert "/tests/test.sh" in sandbox.files
    assert sandbox.closed

    roles = [m["role"] for m in r.messages]
    assert roles == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert r.messages[2]["tool_calls"][0]["function"]["name"] == "bash"
    assert r.messages[3]["tool_call_id"] == r.messages[2]["tool_calls"][0]["id"]


def test_failing_tests_mean_wrong_and_text_reply_ends_loop():
    class Failing(FakeProvider):
        async def create(self, spec, *, labels):
            sb = await super().create(spec, labels=labels)
            sb.test_exit_code = 1
            return sb

    model = scripted("I created the file, all done.")
    (r,), _ = run(agent_task(), model, Failing())
    assert r.output.finish_reason == "no_tool_call"
    assert r.outcome == Outcome.WRONG
    assert r.score.extracted == "exit_code=1"
    assert r.score.details["test_output"] == "test output"


def test_step_limit_outcome():
    model = scripted(tool_turn(call("bash", '{"command": "ls"}')))
    (r,), _ = run(agent_task(BasicAgent(max_steps=3)), model)
    assert len(r.steps) == 3
    assert r.output.finish_reason == "max_steps"


def test_step_limit_is_classified_when_tests_fail():
    class Failing(FakeProvider):
        async def create(self, spec, *, labels):
            sb = await super().create(spec, labels=labels)
            sb.test_exit_code = 1
            return sb

    model = scripted(tool_turn(call("bash", '{"command": "ls"}')))
    (r,), _ = run(agent_task(BasicAgent(max_steps=2)), model, Failing())
    assert r.outcome == Outcome.STEP_LIMIT


def test_bad_tool_calls_are_reported_to_the_model():
    model = scripted(
        tool_turn(
            call("bash", "not json"),
            call("python", "{}"),
            call("bash", '{"command": ""}'),
            call("bash", '{"command": "sleep"}'),
        ),
        tool_turn(call("submit", "{}")),
    )
    (r,), _ = run(agent_task(BasicAgent(command_timeout=7)), model)
    obs = r.steps[0].observations
    assert obs[0].startswith("Error: could not parse tool arguments")
    assert obs[1].startswith("Error: unknown tool 'python'")
    assert obs[2] == "Error: 'command' must be a non-empty string."
    assert obs[3].startswith("exit_code=124 (timed out after 7s)")
    assert r.output.finish_reason == "submit"


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ("{not json", "could not parse tool arguments"),
        ("[1, 2]", "must be a JSON object"),
        ('{"answer": {"a": 1}}', "'answer' must be a string"),
        ('{"answer": true}', "'answer' must be a string"),
    ],
)
def test_invalid_submit_is_rejected_and_the_agent_can_retry(arguments, error):
    model = scripted(
        tool_turn(call("submit", arguments)),
        tool_turn(call("submit", '{"answer": "fixed"}')),
    )
    (r,), _ = run(agent_task(), model)
    assert error in r.steps[0].observations[0]
    assert "Nothing was submitted" in r.steps[0].observations[0]
    assert len(r.steps) == 2
    assert (r.output.finish_reason, r.output.text) == ("submit", "fixed")


def test_numeric_answer_is_accepted():
    (r,), _ = run(agent_task(), scripted(tool_turn(call("submit", '{"answer": 42}'))))
    assert (r.output.finish_reason, r.output.text) == ("submit", "42")


def test_calls_after_submit_in_the_same_turn_are_not_executed():
    model = scripted(
        tool_turn(
            call("bash", '{"command": "echo before > /app/a"}'),
            call("submit", "{}"),
            call("bash", '{"command": "echo after > /app/b"}'),
        )
    )
    (r,), provider = run(agent_task(), model)
    sandbox = provider.created[0]
    assert "echo before > /app/a" in sandbox.commands
    assert "echo after > /app/b" not in sandbox.commands
    assert r.steps[0].observations[2] == "Not executed: submit was already called in this turn."
    tool_ids = [m["tool_call_id"] for m in r.messages if m["role"] == "tool"]
    assert len(tool_ids) == 3


def test_sandbox_creation_failure_is_env_error():
    model = scripted(tool_turn(call("submit", "{}")))
    (r,), _ = run(agent_task(), model, FakeProvider(fail=True))
    assert r.outcome == Outcome.ENV_ERROR
    assert "image build failed" in r.error
    assert model.calls == 0


def test_sandbox_is_closed_when_solver_raises():
    def boom(messages):
        raise ValueError("model exploded")

    from harness.core.model import MockModel

    (r,), provider = run(agent_task(), MockModel(boom))
    assert r.outcome == Outcome.API_ERROR
    assert provider.created[0].closed


def test_each_sample_gets_its_own_sandbox():
    model = scripted(tool_turn(call("submit", "{}")))
    results, provider = run(agent_task(n=4), model, concurrency=2)
    assert len(provider.created) == 4
    assert all(sb.closed for sb in provider.created)
    assert all(r.outcome == Outcome.CORRECT for r in results)


def test_agent_replays_from_cache(tmp_path):
    turns = [
        tool_turn(call("bash", '{"command": "echo hi > /a"}')),
        tool_turn(call("submit", "{}")),
    ]
    cache = DiskCache(tmp_path)
    first = scripted(*turns)
    run(agent_task(), first, cache=cache)
    second = scripted(*turns)
    (r,), _ = run(agent_task(), second, cache=cache)
    assert second.calls == 0
    assert r.output.cached
    assert r.outcome == Outcome.CORRECT


def test_cache_key_depends_on_tools():
    msgs = [{"role": "user", "content": "x"}]
    spec = {"name": "m"}
    plain = cache_key(spec, msgs, GenerateConfig())
    assert cache_key(spec, msgs, GenerateConfig(), None) == plain
    assert cache_key(spec, msgs, GenerateConfig(), BasicAgent().tools) != plain


def test_agent_summary_stats():
    model = scripted(
        tool_turn(call("bash", '{"command": "cat /missing"}')),
        tool_turn(call("submit", "{}")),
    )
    results, _ = run(agent_task(n=2), model)
    agent = summarize(results)["agent"]
    assert agent == {
        "finish_reasons": {"submit": 2},
        "mean_steps": 2.0,
        "commands": 2,
        "failed_commands": 2,
    }


def test_runner_requires_provider_for_sandbox_tasks():
    task = agent_task()
    with pytest.raises(ValueError, match="sandbox provider"):
        asyncio.run(run_samples(task, task.load(), scripted("x"), GenerateConfig()))


def test_exec_result_truncates_long_output():
    res = ExecResult(exit_code=0, stdout="a" * 1000 + "b" * 1000, stderr="")
    text = res.combined(100)
    assert text.startswith("a" * 50) and text.endswith("b" * 50)
    assert "1900 characters omitted" in text


def test_builtin_terminal_tasks_follow_the_layout():
    dirs = discover(DATA_DIR)
    assert [d.name for d in dirs] == ["count-errors", "fix-mean", "hello-file"]
    for d in dirs:
        for rel in [
            "instruction.md",
            "task.toml",
            "environment/Dockerfile",
            "tests/test.sh",
            "solution/solve.sh",
        ]:
            assert (d / rel).is_file(), f"{d.name} is missing {rel}"
        s = load_task_dir(d)
        assert s.metadata["environment_fingerprint"] != s.metadata["task_fingerprint"]


def test_terminal_task_options():
    task = get_task("terminal", ids=["fix-mean"], solver="oracle")
    assert [s.id for s in task.load()] == ["terminal/fix-mean"]
    assert task.solver.name == "oracle"
    assert task.sandbox(task.load()[0]).build_context == Path(DATA_DIR / "fix-mean/environment")
    with pytest.raises(ValueError, match="unknown solver"):
        get_task("terminal", solver="nope")


def test_fake_sandbox_sanity():
    sb = FakeSandbox()
    asyncio.run(sb.exec("echo hi > /x"))
    assert sb.files["/x"] == b"hi\n"
