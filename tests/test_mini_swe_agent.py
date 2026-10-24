import asyncio

import pytest

pytest.importorskip("minisweagent")

from fakes import call, scripted, tool_turn  # noqa: E402

from harness.core.model import MockModel  # noqa: E402
from harness.core.types import GenerateConfig, Outcome, Sample, classify  # noqa: E402
from harness.sandbox.base import ExecResult, Sandbox  # noqa: E402
from harness.solvers.mini_swe_agent import MiniSweAgent  # noqa: E402

PATCH = "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-x\n+y\n"
SUBMIT = '{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt"}'


class ScriptSandbox(Sandbox):
    """Answers based on the agent's command, i.e. the last line of the wrapper script."""

    def __init__(self, outputs: dict[str, ExecResult]) -> None:
        self.outputs = outputs
        self.scripts: list[str] = []

    async def exec(self, cmd: str, *, timeout: float = 60.0) -> ExecResult:
        self.scripts.append(cmd)
        command = cmd.splitlines()[-1]
        return self.outputs.get(
            command, ExecResult(exit_code=0, stdout=f"ran {command}\n", stderr="")
        )

    async def write_file(self, path: str, content: bytes | str) -> None:
        raise NotImplementedError

    async def read_file(self, path: str) -> bytes:
        raise NotImplementedError

    async def close(self) -> None:
        pass


def solve(model: MockModel, sandbox: Sandbox, **kwargs):
    solver = MiniSweAgent(**kwargs)
    seen_tools = []

    async def generate(messages, tools=None):
        seen_tools.append(tools)
        return await model.generate(messages, GenerateConfig(), tools)

    sample = Sample(id="s", input="Fix the bug in m.py", target="resolved")
    return asyncio.run(solver(sample, generate, sandbox)), seen_tools


def submitting_sandbox() -> ScriptSandbox:
    submit_out = ExecResult(
        exit_code=0, stdout=f"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n{PATCH}", stderr=""
    )
    return ScriptSandbox(
        {"echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt": submit_out}
    )


def test_submission_becomes_output_and_steps_are_recorded():
    model = scripted(
        tool_turn(call("bash", '{"command": "ls"}'), text="look around"),
        tool_turn(call("bash", SUBMIT), text="submit"),
    )
    sandbox = submitting_sandbox()
    result, seen_tools = solve(model, sandbox)

    assert result.output.text == PATCH
    assert result.output.finish_reason == "submit"
    assert [s.text for s in result.steps] == ["look around", "submit"]
    assert result.steps[0].observations == ["ran ls\n"]
    assert result.steps[0].exit_codes == [0]
    assert seen_tools[0][0]["function"]["name"] == "bash"
    assert all("extra" not in m for m in result.messages)
    assert [m["role"] for m in result.messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
        "exit",
    ]
    assert "Fix the bug in m.py" in result.messages[1]["content"]
    first = sandbox.scripts[0]
    assert first.startswith("exec 2>&1\n") and "cd /testbed\n" in first
    assert "export PAGER=cat\n" in first


def test_failed_submit_command_does_not_submit():
    model = scripted(tool_turn(call("bash", SUBMIT)))
    sandbox = ScriptSandbox(
        {
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt": ExecResult(
                exit_code=1, stdout="COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\n", stderr="no file"
            )
        }
    )
    result, _ = solve(model, sandbox, max_steps=2)
    assert result.output.finish_reason == "max_steps"
    assert len(result.steps) == 2


def test_step_limit_is_reported_as_step_limit():
    model = scripted(tool_turn(call("bash", '{"command": "ls"}')))
    result, _ = solve(model, ScriptSandbox({}), max_steps=3)

    assert result.output.finish_reason == "max_steps"
    assert result.output.text == ""
    assert len(result.steps) == 3
    from harness.core.types import SampleResult, Score

    r = SampleResult(
        sample=Sample(id="s", input="", target=""),
        output=result.output,
        score=Score(value=0.0, extracted="exit_code=1", method="test_script"),
    )
    assert classify(r) == Outcome.STEP_LIMIT


def test_replies_without_tool_calls_end_as_format_errors():
    model = scripted("I think the fix is obvious.")
    result, _ = solve(model, ScriptSandbox({}))

    assert result.output.finish_reason == "RepeatedFormatError"
    assert len(result.steps) == 3
    feedback = [m for m in result.messages if m["role"] == "user"][1:]
    assert feedback and "No tool calls found" in feedback[0]["content"]


def test_timeouts_are_shown_to_the_agent():
    model = scripted(tool_turn(call("bash", '{"command": "sleep 999"}')))
    sandbox = ScriptSandbox(
        {"sleep 999": ExecResult(exit_code=124, stdout="", stderr="", timed_out=True)}
    )
    result, _ = solve(model, sandbox, max_steps=1, command_timeout=5)

    tool_msg = next(m for m in result.messages if m["role"] == "tool")
    assert "timed out after 5s" in tool_msg["content"]


def test_model_errors_propagate():
    def boom(messages):
        raise RuntimeError("api down")

    with pytest.raises(RuntimeError, match="api down"):
        solve(MockModel(boom), ScriptSandbox({}))


def test_model_call_failures_through_the_bridge_are_api_errors():
    from fakes import FakeProvider

    from harness.core.runner import run_samples
    from harness.core.task import get_task

    def down(messages):
        raise ValueError("provider down")

    class Provider(FakeProvider):
        async def create(self, spec, *, labels):
            return ScriptSandbox({})

    task = get_task("terminal", solver="mini", ids=["hello-file"])
    [r] = asyncio.run(
        run_samples(task, task.load(), MockModel(down), GenerateConfig(), sandbox_provider=Provider())
    )
    assert r.outcome == Outcome.API_ERROR
    assert r.error == "ValueError: provider down"


def test_describe_is_stable_and_reflects_settings():
    a = MiniSweAgent(max_steps=10).describe()
    assert a == MiniSweAgent(max_steps=10).describe()
    assert a["fingerprint"] != MiniSweAgent(max_steps=11).describe()["fingerprint"]
    assert a["agent_config"]["step_limit"] == 10 and a["config"] == "swebench"


def test_terminal_task_accepts_mini_solver():
    from harness.core.task import get_task

    task = get_task("terminal", solver="mini", max_steps=5)
    assert task.solver.describe()["config"] == "mini"
    assert task.solver.cwd == "/app"
