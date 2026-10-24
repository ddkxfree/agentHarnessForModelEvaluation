"""Run mini-swe-agent (https://github.com/SWE-agent/mini-swe-agent) as a harness Solver.

mini-swe-agent's ``DefaultAgent`` is synchronous and talks to two objects: a *model* and an
*environment*. We hand it bridge implementations of both, so that

* every model call goes through the harness ``generate`` (cache, retries, timeout, usage), and
* every command runs in the harness sandbox (same container the scorer later inspects).

The agent loop runs in a worker thread; the bridges submit coroutines back to the event loop.
Needs the optional dependency: ``uv sync --extra swebench``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import shlex
import threading
import time
from importlib.resources import files
from types import SimpleNamespace
from typing import Any

import yaml

from harness.core.fingerprint import fingerprint
from harness.core.solver import Generate
from harness.core.trajectory import Trajectory, current_trajectory
from harness.core.types import Message, ModelOutput, Sample, SolveResult, Step, Usage
from harness.sandbox.base import Sandbox, SandboxError

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

FINISH_REASONS = {"Submitted": "submit", "LimitsExceeded": "max_steps"}


def load_mini_config(name: str) -> dict[str, Any]:
    """Load a config shipped with mini-swe-agent, e.g. ``swebench`` or ``default``."""
    import minisweagent.config as cfg

    root = files(cfg)
    path = root / "benchmarks" / f"{name}.yaml"
    if not path.is_file():
        path = root / f"{name}.yaml"
    return yaml.safe_load(path.read_text())


class _Cancelled(BaseException):
    """Raised inside the worker thread to unwind the agent after the sample was cancelled.

    A BaseException, so mini-swe-agent's ``except Exception`` handlers do not swallow it.
    """


class _Bridge:
    """State shared by the bridge model and environment for one sample."""

    def __init__(self, loop: asyncio.AbstractEventLoop, trajectory: Trajectory) -> None:
        self.loop = loop
        self.trajectory = trajectory
        self.steps = trajectory.steps
        self.usage = Usage()
        self.all_cached = True
        self._lock = threading.Lock()
        self._cancelled = False
        self._pending: set[concurrent.futures.Future[Any]] = set()

    def run(self, coro: Any) -> Any:
        """Run ``coro`` on the event loop and wait for it, from the worker thread."""
        with self._lock:
            if self._cancelled:
                coro.close()
                raise _Cancelled
            future = asyncio.run_coroutine_threadsafe(coro, self.loop)
            self._pending.add(future)
        try:
            return future.result()
        except concurrent.futures.CancelledError:
            raise _Cancelled from None
        finally:
            with self._lock:
                self._pending.discard(future)

    def cancel(self) -> None:
        """Stop the worker: in-flight calls are cancelled and no new ones are started."""
        with self._lock:
            self._cancelled = True
            for future in self._pending:
                future.cancel()


class HarnessModel:
    """mini-swe-agent Model protocol backed by the harness ``generate``."""

    def __init__(self, generate: Generate, bridge: _Bridge, model_config: dict[str, Any]) -> None:
        from minisweagent.models.utils.actions_toolcall import BASH_TOOL

        self.generate = generate
        self.bridge = bridge
        self.tools = [BASH_TOOL]
        self.config = SimpleNamespace(
            observation_template=model_config["observation_template"],
            format_error_template=model_config.get("format_error_template", "{{ error }}"),
        )

    def query(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        from minisweagent.exceptions import FormatError
        from minisweagent.models.utils.actions_toolcall import parse_toolcall_actions

        prepared = [{k: v for k, v in m.items() if k != "extra"} for m in messages]
        out: ModelOutput = self.bridge.run(self.generate(prepared, tools=self.tools))
        self.bridge.usage += out.usage
        self.bridge.all_cached = self.bridge.all_cached and out.cached
        self.bridge.steps.append(
            Step(
                index=len(self.bridge.steps),
                text=out.text,
                tool_calls=out.tool_calls,
                usage=out.usage,
                latency_s=out.latency_s,
            )
        )
        calls = [
            SimpleNamespace(id=c.id, function=SimpleNamespace(name=c.name, arguments=c.arguments))
            for c in out.tool_calls
        ]
        try:
            actions = parse_toolcall_actions(
                calls,
                format_error_template=self.config.format_error_template,
                template_kwargs={"finish_reason": out.finish_reason},
            )
        except FormatError as e:
            e.messages[0]["extra"]["cost"] = 0.0
            raise
        message: dict[str, Any] = {"role": "assistant", "content": out.text}
        if out.tool_calls:
            message["tool_calls"] = [c.to_message() for c in out.tool_calls]
        message["extra"] = {"actions": actions, "cost": 0.0, "timestamp": time.time()}
        return message

    def format_message(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        from minisweagent.models.utils.actions_toolcall import (
            format_toolcall_observation_messages,
        )

        formatted = format_toolcall_observation_messages(
            actions=message.get("extra", {}).get("actions", []),
            outputs=outputs,
            observation_template=self.config.observation_template,
            template_vars=template_vars,
        )
        if self.bridge.steps and formatted:
            self._record_observations(self.bridge.steps[-1], outputs, formatted)
        return formatted

    def _record_observations(self, step: Step, outputs: list[dict], formatted: list[dict]) -> None:
        """Replace the raw outputs recorded by the environment with what the model is shown;
        an output the template truncated is kept as an artifact."""
        first = len(step.observations) - len(outputs)
        if first < 0 or len(formatted) != len(outputs):
            return
        for k, (out, msg) in enumerate(zip(outputs, formatted, strict=True)):
            shown, raw = str(msg.get("content", "")), out.get("output", "")
            step.observations[first + k] = shown
            if raw not in shown:
                name = f"step-{step.index}-call-{k}.txt"
                step.raw_outputs[first + k] = self.bridge.trajectory.save_artifact(name, raw).name

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return {**vars(self.config), **kwargs}

    def serialize(self) -> dict[str, Any]:
        return {"info": {"config": {"model_type": "harness"}}}


class SandboxEnvironment:
    """mini-swe-agent Environment protocol backed by a harness sandbox."""

    def __init__(
        self,
        sandbox: Sandbox,
        bridge: _Bridge,
        *,
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.sandbox = sandbox
        self.bridge = bridge
        self.cwd = cwd
        self.env = env or {}
        self.timeout = timeout

    def execute(
        self, action: dict, cwd: str = "", *, timeout: float | None = None
    ) -> dict[str, Any]:
        timeout = timeout or self.timeout
        prefix = "".join(f"export {k}={shlex.quote(str(v))}\n" for k, v in self.env.items())
        if cwd := cwd or self.cwd:
            prefix += f"cd {shlex.quote(cwd)}\n"
        # mini-swe-agent expects stdout and stderr interleaved in one stream.
        script = f"exec 2>&1\n{prefix}{action.get('command', '')}"
        start = time.perf_counter()
        res = self.bridge.run(self.sandbox.exec(script, timeout=timeout))
        elapsed = time.perf_counter() - start
        output: dict[str, Any] = {
            "output": res.stdout + res.stderr,
            "returncode": res.exit_code,
            "exception_info": "",
        }
        if res.timed_out:
            output["returncode"] = -1
            output["exception_info"] = f"Command timed out after {timeout:g}s."
        if self.bridge.steps:
            step = self.bridge.steps[-1]
            step.observations.append(output["output"])
            step.raw_outputs.append(None)
            step.exit_codes.append(res.exit_code)
            step.tool_latency_s += elapsed
        self._check_finished(output)
        return output

    def _check_finished(self, output: dict[str, Any]) -> None:
        from minisweagent.exceptions import Submitted

        lines = output["output"].lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT":
            if output["returncode"] == 0:
                submission = "".join(lines[1:])
                raise Submitted(
                    {
                        "role": "exit",
                        "content": submission,
                        "extra": {"exit_status": "Submitted", "submission": submission},
                    }
                )

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "cwd": self.cwd,
            "timeout": self.timeout,
            "system": "Linux",
            "release": "",
            "version": "",
            "machine": "x86_64",
            **kwargs,
        }

    def serialize(self) -> dict[str, Any]:
        return {"info": {"config": {"environment_type": "harness-sandbox"}}}


class MiniSweAgent:
    """Solver that runs mini-swe-agent's ``DefaultAgent`` with one of its shipped configs.

    The default ``swebench`` config asks the agent to finish by printing a git diff; that
    submission becomes the output text, which the SWE-bench scorer applies as the model patch.
    """

    name = "mini_swe_agent"

    def __init__(
        self,
        *,
        config: str = "swebench",
        max_steps: int = 50,
        command_timeout: float | None = None,
        cwd: str | None = None,
        cancel_grace: float = 30.0,
    ) -> None:
        self.config_name = config
        # How long a cancelled sample waits for the worker thread to unwind.
        self.cancel_grace = cancel_grace
        self.mini_config = load_mini_config(config)
        self.max_steps = max_steps
        env_cfg = self.mini_config.get("environment", {})
        self.command_timeout = float(command_timeout or env_cfg.get("timeout", 60))
        self.cwd = cwd if cwd is not None else env_cfg.get("cwd", "")
        self.env = dict(env_cfg.get("env", {}))

    def _agent_config(self) -> dict[str, Any]:
        keep = ("system_template", "instance_template", "max_consecutive_format_errors")
        cfg = {k: v for k, v in self.mini_config["agent"].items() if k in keep}
        cfg["step_limit"] = self.max_steps
        cfg["cost_limit"] = 0
        return cfg

    async def __call__(
        self, sample: Sample, generate: Generate, sandbox: Sandbox | None = None
    ) -> SolveResult:
        from minisweagent.agents.default import DefaultAgent

        if sandbox is None:
            raise SandboxError("mini-swe-agent needs a sandbox")
        loop = asyncio.get_running_loop()
        trajectory = current_trajectory()
        bridge = _Bridge(loop, trajectory)
        model = HarnessModel(generate, bridge, self.mini_config["model"])
        env = SandboxEnvironment(
            sandbox, bridge, cwd=self.cwd, env=self.env, timeout=self.command_timeout
        )
        agent = DefaultAgent(model, env, **self._agent_config())
        worker = loop.run_in_executor(None, agent.run, sample.input)
        try:
            # shield: on cancellation we must keep the future to wait for the thread to stop.
            info = await asyncio.shield(worker)
        except asyncio.CancelledError:
            bridge.cancel()
            done, _ = await asyncio.wait({worker}, timeout=self.cancel_grace)
            if not done:
                raise RuntimeError(
                    f"mini-swe-agent worker did not stop within {self.cancel_grace:g}s"
                ) from None
            worker.exception()  # the expected _Cancelled; retrieve it so asyncio does not warn
            raise
        finally:
            trajectory.messages[:] = [
                {k: v for k, v in m.items() if k != "extra"} for m in agent.messages
            ]

        exit_status = info.get("exit_status", "")
        messages: list[Message] = trajectory.messages
        output = ModelOutput(
            text=info.get("submission", "") or "",
            finish_reason=FINISH_REASONS.get(exit_status, exit_status or "unknown"),
            usage=bridge.usage,
            latency_s=sum(s.latency_s + s.tool_latency_s for s in bridge.steps),
            cached=bridge.all_cached and bool(bridge.steps),
        )
        return SolveResult(messages=messages, output=output, steps=bridge.steps)

    def describe(self) -> dict[str, Any]:
        from minisweagent import __version__

        desc = {
            "name": self.name,
            "mini_swe_agent_version": __version__,
            "config": self.config_name,
            "agent_config": self._agent_config(),
            "model_templates": {
                k: self.mini_config["model"].get(k)
                for k in ("observation_template", "format_error_template")
            },
            "environment": {"cwd": self.cwd, "env": self.env, "timeout": self.command_timeout},
        }
        desc["fingerprint"] = fingerprint(desc)
        return desc
