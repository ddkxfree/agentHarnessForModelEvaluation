from __future__ import annotations

import itertools
import shlex
from collections.abc import Callable

from harness.core.model import MockModel
from harness.core.types import Message, ModelOutput, ToolCall
from harness.sandbox.base import ExecResult, Sandbox, SandboxProvider, SandboxSpec


class FakeSandbox(Sandbox):
    """In-memory sandbox understanding a few commands, enough to test agent plumbing."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.closed = False
        self.test_exit_code = 0

    async def exec(self, cmd: str, *, timeout: float = 60.0) -> ExecResult:
        self.commands.append(cmd)
        if cmd == "bash /tests/test.sh":
            return ExecResult(exit_code=self.test_exit_code, stdout="test output", stderr="")
        if cmd.startswith("echo ") and " > " in cmd:
            text, path = cmd[5:].split(" > ", 1)
            self.files[path.strip()] = (shlex.split(text)[0] + "\n").encode()
            return ExecResult(exit_code=0, stdout="", stderr="")
        if cmd.startswith("cat "):
            path = cmd[4:].strip()
            if path in self.files:
                return ExecResult(exit_code=0, stdout=self.files[path].decode(), stderr="")
            return ExecResult(exit_code=1, stdout="", stderr=f"cat: {path}: No such file")
        if cmd == "sleep":
            return ExecResult(exit_code=124, stdout="", stderr="", timed_out=True)
        return ExecResult(exit_code=127, stdout="", stderr=f"{cmd}: command not found")

    async def write_file(self, path: str, content: bytes | str) -> None:
        self.files[path] = content.encode() if isinstance(content, str) else content

    async def read_file(self, path: str) -> bytes:
        return self.files[path]

    async def close(self) -> None:
        self.closed = True


class FakeProvider(SandboxProvider):
    name = "fake"

    def __init__(self, fail: bool = False, sandbox_cls: type[FakeSandbox] | None = None) -> None:
        self.fail = fail
        self.sandbox_cls = sandbox_cls or FakeSandbox
        self.test_exit_code = 0
        self.created: list[FakeSandbox] = []

    async def create(self, spec: SandboxSpec, *, labels: dict[str, str]) -> Sandbox:
        from harness.sandbox.base import SandboxError

        if self.fail:
            raise SandboxError("image build failed")
        sb = self.sandbox_cls()
        sb.test_exit_code = self.test_exit_code
        self.created.append(sb)
        return sb


_ids = itertools.count()


def call(name: str, arguments: str) -> ToolCall:
    return ToolCall(id=f"call_{next(_ids)}", name=name, arguments=arguments)


def scripted(*turns: ModelOutput | str) -> MockModel:
    """A model that replays ``turns`` in order, one per call, for every sample."""

    def respond(messages: list[Message]) -> ModelOutput | str:
        i = sum(1 for m in messages if m["role"] == "assistant")
        return turns[min(i, len(turns) - 1)]

    return MockModel(respond)


def tool_turn(*calls: ToolCall, text: str = "") -> ModelOutput:
    return ModelOutput(text=text, tool_calls=list(calls), finish_reason="tool_calls")


Respond = Callable[[list[Message]], ModelOutput | str]
