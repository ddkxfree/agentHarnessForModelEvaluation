from __future__ import annotations

import json
import time
from typing import Any

from harness.core.fingerprint import fingerprint
from harness.core.solver import Generate
from harness.core.types import Message, ModelOutput, Sample, SolveResult, Step, ToolCall, Usage
from harness.sandbox.base import Sandbox

SYSTEM_PROMPT = """\
You are an autonomous agent working inside a Linux container. Your working directory is {workdir}.
Use the bash tool to inspect and change the environment. Run one command at a time and check
its output before deciding the next step. When the task is complete, call the submit tool."""

BASH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a bash command in the container and return its exit code and output.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The command to run."}},
            "required": ["command"],
        },
    },
}

SUBMIT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit",
        "description": "Call when the task is complete. Optionally include a final answer.",
        "parameters": {
            "type": "object",
            "properties": {"answer": {"type": "string", "description": "Final answer, if any."}},
        },
    },
}


class BasicAgent:
    """Minimal tool-calling loop used as a baseline and to exercise the sandbox pipeline.

    The loop ends when the model calls ``submit``, replies without calling a tool, or
    reaches ``max_steps`` (reported as outcome ``step_limit``).
    """

    name = "basic_agent"

    def __init__(
        self,
        *,
        system_prompt: str = SYSTEM_PROMPT,
        workdir: str = "/app",
        max_steps: int = 20,
        command_timeout: float = 60.0,
        max_observation_chars: int = 4000,
    ) -> None:
        self.system_prompt = system_prompt.format(workdir=workdir)
        self.max_steps = max_steps
        self.command_timeout = command_timeout
        self.max_observation_chars = max_observation_chars
        self.tools = [BASH_TOOL, SUBMIT_TOOL]

    @staticmethod
    def _parse_args(call: ToolCall) -> tuple[dict[str, Any] | None, str | None]:
        try:
            args = json.loads(call.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as exc:
            return None, f"Error: could not parse tool arguments ({exc})."
        return args, None

    def _parse_submit(self, call: ToolCall) -> tuple[str | None, str | None]:
        """Return ``(answer, None)`` for a valid submission or ``(None, error)``."""
        args, error = self._parse_args(call)
        if args is None:
            return None, f"{error} Nothing was submitted; call submit again."
        answer = args.get("answer", "")
        if isinstance(answer, bool) or not isinstance(answer, str | int | float):
            return None, "Error: 'answer' must be a string. Nothing was submitted."
        return str(answer), None

    async def _run_tool(self, call: ToolCall, sandbox: Sandbox | None) -> tuple[str, int | None]:
        args, error = self._parse_args(call)
        if args is None:
            return error or "", None
        if call.name != "bash":
            return f"Error: unknown tool {call.name!r}. Available tools: bash, submit.", None
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return "Error: 'command' must be a non-empty string.", None
        if sandbox is None:
            return "Error: this task has no sandbox to run commands in.", None

        res = await sandbox.exec(command, timeout=self.command_timeout)
        header = f"exit_code={res.exit_code}"
        if res.timed_out:
            header += f" (timed out after {self.command_timeout:g}s)"
        return f"{header}\n{res.combined(self.max_observation_chars)}", res.exit_code

    async def __call__(
        self, sample: Sample, generate: Generate, sandbox: Sandbox | None = None
    ) -> SolveResult:
        messages: list[Message] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": sample.input},
        ]
        steps: list[Step] = []
        usage = Usage()
        all_cached = True
        final_text = ""
        finish = "max_steps"

        for i in range(self.max_steps):
            out = await generate(messages, tools=self.tools)
            usage += out.usage
            all_cached = all_cached and out.cached
            step = Step(
                index=i,
                text=out.text,
                tool_calls=out.tool_calls,
                usage=out.usage,
                latency_s=out.latency_s,
            )
            assistant: Message = {"role": "assistant", "content": out.text}
            if out.tool_calls:
                assistant["tool_calls"] = [c.to_message() for c in out.tool_calls]
            messages.append(assistant)

            if not out.tool_calls:
                steps.append(step)
                final_text = out.text
                finish = "length" if out.finish_reason == "length" else "no_tool_call"
                break

            submitted = False
            start = time.perf_counter()
            for call in out.tool_calls:
                exit_code: int | None = None
                if submitted:
                    observation = "Not executed: submit was already called in this turn."
                elif call.name == "submit":
                    answer, error = self._parse_submit(call)
                    if answer is None:
                        observation = error or ""
                    else:
                        observation, submitted, final_text = "Submitted.", True, answer
                else:
                    observation, exit_code = await self._run_tool(call, sandbox)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": observation})
                step.observations.append(observation)
                step.exit_codes.append(exit_code)
            step.tool_latency_s = time.perf_counter() - start
            steps.append(step)
            if submitted:
                finish = "submit"
                break

        output = ModelOutput(
            text=final_text,
            finish_reason=finish,
            usage=usage,
            latency_s=sum(s.latency_s + s.tool_latency_s for s in steps),
            cached=all_cached and bool(steps),
        )
        return SolveResult(messages=messages, output=output, steps=steps)

    def describe(self) -> dict[str, Any]:
        desc = {
            "name": self.name,
            "system_prompt": self.system_prompt,
            "max_steps": self.max_steps,
            "command_timeout": self.command_timeout,
            "max_observation_chars": self.max_observation_chars,
            "tools": self.tools,
            # 2: invalid submit calls are rejected, and calls after submit are not executed
            "loop_version": 2,
        }
        desc["fingerprint"] = fingerprint(desc)
        return desc
