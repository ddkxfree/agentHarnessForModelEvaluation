"""The onboarding agent: a model writes a Task adapter for a frozen source, the controller
validates it independently and feeds the diagnosis back, within a fixed budget.

Loop:

1. planning: the model reads the description, scoring rules and a data preview and replies with
   a field mapping and plan (or questions, which stop the attempt for a person to answer);
2. writing: the model replies with complete candidate files; writes outside the candidate
   files stop the attempt;
3. validation by ``validator.validate`` (never the agent's own tests alone);
4. on failure the diagnosis goes back to the model for another round, until the rounds, model
   steps, tokens or time run out. ``time_limit_s`` is one deadline for the whole attempt: model
   waits, retries, validation and self tests get only the time left, and nothing that finishes
   after it counts as passed. Getting the executor ready (building the checker image on first
   use) happens before that clock starts, within ``prepare_timeout_s``.

Everything is archived in the attempt directory: spec and source hashes, model identity, prompt
and reply-format versions, budget, every model call, every round's files and validation, the
stop reason, the final diff and the commands to reproduce it.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.core.cache import DiskCache, cache_key
from harness.core.fingerprint import fingerprint
from harness.core.io import write_json
from harness.core.manifest import now
from harness.core.model import Model
from harness.core.retry import retry_async
from harness.core.types import Message, ModelOutput, Usage
from harness.judge.common import extract_json
from harness.onboard.check import PROTECTED_VIOLATION
from harness.onboard.spec import CANDIDATE_FILES, OnboardSpec, source_fingerprint
from harness.onboard.validator import (
    ENV_ERROR,
    SOURCE_CHANGED,
    VALIDATOR_VERSION,
    Executor,
    PrepareTimeout,
    make_executor,
    validate,
)
from harness.onboard.workspace import CandidateWorkspace, WriteRejected, sha256
from harness.sandbox.base import SandboxError

PROMPT_VERSION = "onboard_prompt_v1"
REPLY_FORMAT_VERSION = "onboard_reply_v1"

# Attempt-level categories besides the validation ones.
GENERATION_FAILED = "generation_failed"
BUDGET_EXHAUSTED = "budget_exhausted"
NEEDS_HUMAN = "needs_human"
CANCELLED = "cancelled"

SYSTEM_PROMPT = """\
You add a new evaluation task to a Python evaluation harness. You write the adapter that turns
a dataset in its own format into the harness's Task; a separate validator then checks it with
outputs you do not see, and tells you what failed.

Rules:
- The source description, data, scoring rules and acceptance cases are fixed. Never change
  them, never write them, and never change the official scoring rule to make checks pass.
- You may only write these files: {files}.
- If the scoring rule or the data is ambiguous, ask instead of guessing.

The adapter (adapter.py) contract:
- Register exactly one factory with `@register("{task}")` from `harness.core.task`.
- Signature: `(data_dir: str | None = None, limit: int | None = None, seed: int | None = None)`;
  when data_dir is None use `Path(__file__).parent / "{module}_data"`.
- Return `harness.core.task.Task(name="{task}", dataset=DatasetSpec(path=..., split=...,
  revision=fingerprint_dir(Path(data_dir))), load=..., solver=..., scorer=..., params=...)`.
- `load()` returns `harness.core.types.Sample(id=..., input=..., target=..., metadata=...)`
  objects with stable, unique ids `{task}/<source id>`; use `select(samples, limit, seed)`.
- Solver: `harness.core.solver.PromptSolver(lambda s: <prompt>, template_text=<template>)`.
- Scorer: an object with `name`, `__call__(sample, text) -> harness.core.types.Score(value=0.0
  or 1.0, extracted=..., method=...)` and `describe() -> dict` whose "fingerprint" is
  `harness.core.fingerprint.fingerprint` of the rest of the dict.
- Only these imports are allowed: {imports}.
- test_adapter.py (optional) is run with pytest; the data directory is in the environment
  variable ONBOARD_DATA_DIR and adapter.py is importable as `adapter`.
"""

PLAN_REQUEST = """\
Task name: {task}

=== Description ({description}) ===
{readme}

=== Official scoring rules ({scoring_rules}) ===
{scoring}

=== Data preview ({data}/) ===
{preview}

First, plan. Reply with only a JSON object:
{{"field_mapping": {{"id": "...", "input": "...", "target": "...", "metadata": "..."}},
  "plan": "<how the adapter loads, prompts and scores>",
  "questions": ["<only if something is ambiguous>"]}}"""

WRITE_REQUEST = """\
Now write the candidate files. Reply with each complete file as

<file path="adapter.py">
...file content...
</file>

and nothing else. adapter.py is required. If you cannot continue without a decision from a
person, reply with <question>...</question> instead."""

FEEDBACK = """\
Independent validation failed ({category}).

{detail}

Your own tests: {self_test}

Fix the candidate files and reply with the complete files again, in the same format."""

_FILE = re.compile(r'<file\s+path="([^"]+)"\s*>\n?(.*?)</file>', re.DOTALL)
_QUESTION = re.compile(r"<question>(.*?)</question>", re.DOTALL)
_FENCE = re.compile(r"^```[\w.-]*\n(.*?)\n?```\s*$", re.DOTALL)


class StopAttempt(Exception):
    def __init__(self, reason: str, category: str | None, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason, self.category, self.detail = reason, category, detail


def parse_plan(text: str) -> dict[str, Any]:
    plan = extract_json(text)
    mapping = plan.get("field_mapping")
    if not isinstance(mapping, dict) or not {"id", "input", "target"} <= set(mapping):
        raise ValueError("'field_mapping' must map at least id, input and target")
    if not isinstance(plan.get("plan"), str) or not plan["plan"].strip():
        raise ValueError("'plan' must be a non-empty string")
    questions = plan.get("questions", [])
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        raise ValueError("'questions' must be a list of strings")
    plan["questions"] = [q for q in questions if q.strip()]
    return plan


def parse_files(text: str) -> tuple[dict[str, str], list[str]]:
    files: dict[str, str] = {}
    for path, content in _FILE.findall(text):
        if m := _FENCE.match(content.strip()):
            content = m.group(1) + "\n"
        files[path] = content
    questions = [q.strip() for q in _QUESTION.findall(text) if q.strip()]
    return files, questions


def data_preview(spec: OnboardSpec, lines: int = 8) -> str:
    out = []
    for f in sorted(p for p in spec.path(spec.data).rglob("*") if p.is_file()):
        head = f.read_text(errors="replace").splitlines()[:lines]
        out.append(f"--- {f.relative_to(spec.source).as_posix()} (first {len(head)} lines)")
        out.extend(head)
    return "\n".join(out)


TASKS_INIT = "src/harness/tasks/__init__.py"


def _registry_change(module: str) -> tuple[str, str]:
    """The tasks package's import line before and after adding ``module``."""
    import harness.tasks

    old = Path(harness.tasks.__file__).read_text()
    m = re.match(r"from harness\.tasks import ([\w, ]+?)(\s*#.*)?\n", old)
    if m is None:
        raise ValueError(f"unexpected {TASKS_INIT}; add the import by hand")
    names = sorted({n.strip() for n in m.group(1).split(",")} | {module})
    new = f"from harness.tasks import {', '.join(names)}{m.group(2) or ''}\n" + old[m.end() :]
    return old, new


def make_diff(spec: OnboardSpec, files: dict[str, str]) -> str:
    """The candidate as a patch against the repo: its files at their repo paths and the task's
    import in the tasks package. For review only; it is never applied automatically. The data
    is not part of it (see the reproduce commands)."""
    parts = []
    for name, content in files.items():
        target = CANDIDATE_FILES[name].format(module=spec.module, task=spec.task)
        lines = content.splitlines(keepends=True)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        parts.append(f"diff --git a/{target} b/{target}\nnew file mode 100644\n")
        parts.extend(difflib.unified_diff([], lines, "/dev/null", f"b/{target}"))
    if "adapter.py" in files:
        old, new = _registry_change(spec.module)
        if new != old:
            parts.append(f"diff --git a/{TASKS_INIT} b/{TASKS_INIT}\n")
            parts.extend(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    f"a/{TASKS_INIT}",
                    f"b/{TASKS_INIT}",
                )
            )
    return "".join(parts)


class OnboardAgent:
    def __init__(
        self,
        spec: OnboardSpec,
        model: Model,
        *,
        cache: DiskCache | None = None,
        executor: Executor | None = None,
        retries: int = 3,
        retry_base_delay: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        spec_path: Path | None = None,
    ) -> None:
        self.spec, self.model, self.cache = spec, model, cache
        self.executor = executor or make_executor(spec.executor)
        self.retries, self.retry_base_delay, self.clock = retries, retry_base_delay, clock
        self.spec_path = spec_path

    # --- archive ---------------------------------------------------------------------------
    def _new_dir(self) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        base = Path(self.spec.output_dir) / f"{stamp}-{self.spec.task}"
        path, i = base, 1
        while path.exists():
            i += 1
            path = base.with_name(f"{base.name}-{i}")
        path.mkdir(parents=True)
        return path

    def _save(self) -> None:
        write_json(self.dir / "attempt.json", self.record)
        write_json(self.dir / "conversation.json", self.messages)

    # --- budget and model calls --------------------------------------------------------------
    def _remaining(self) -> float:
        return self.spec.budget.time_limit_s - (self.clock() - self.started)

    def _out_of_time(self, during: str) -> StopAttempt:
        limit = self.spec.budget.time_limit_s
        return StopAttempt("time_limit", BUDGET_EXHAUSTED, f"{limit}s used, during {during}")

    def _check_budget(self) -> None:
        b = self.spec.budget
        if len(self.record["steps"]) >= b.max_model_steps:
            raise StopAttempt(
                "max_model_steps", BUDGET_EXHAUSTED, f"{b.max_model_steps} steps used"
            )
        if self._remaining() <= 0:
            raise self._out_of_time("the previous step")
        used = self.usage.input_tokens + self.usage.output_tokens
        if b.token_limit is not None and used >= b.token_limit:
            raise StopAttempt("token_limit", BUDGET_EXHAUSTED, f"{used} of {b.token_limit} tokens")

    async def _ask(self, phase: str, content: str) -> str:
        self._check_budget()
        self.messages.append({"role": "user", "content": content})
        messages: list[Message] = list(self.messages)
        gen = self.spec.generate
        step: dict[str, Any] = {
            "n": len(self.record["steps"]) + 1,
            "phase": phase,
            "messages": len(messages),
            "request_sha256": sha256(json.dumps(messages, sort_keys=True).encode()),
            "attempts": 0,
            "cached": False,
            "error": None,
        }
        self.record["steps"].append(step)
        key = cache_key(self.model.spec(), messages, gen, None, f"onboard/{self.spec.task}")
        out: ModelOutput | None = self.cache.get(key) if self.cache is not None else None
        started = time.perf_counter()
        if out is not None:
            step["cached"] = True
        else:

            def count(_: int) -> None:
                step["attempts"] += 1

            try:
                # the attempt's deadline bounds every wait, retries and backoff included
                async with asyncio.timeout(self._remaining()) as deadline:
                    out = await retry_async(
                        lambda: asyncio.wait_for(self.model.generate(messages, gen), gen.timeout),
                        retries=self.retries,
                        is_retryable=lambda e: (
                            isinstance(e, TimeoutError) or self.model.is_retryable(e)
                        ),
                        base_delay=self.retry_base_delay,
                        on_attempt=count,
                    )
            except Exception as exc:
                step["error"] = f"{type(exc).__name__}: {exc}"
                step["latency_s"] = time.perf_counter() - started
                self._save()
                if deadline.expired():
                    raise self._out_of_time(f"model step {step['n']}") from None
                raise StopAttempt("model_call_failed", GENERATION_FAILED, step["error"]) from None
            if self.cache is not None:
                self.cache.put(key, out)
        step.update(
            latency_s=time.perf_counter() - started,
            text=out.text,
            finish_reason=out.finish_reason,
            usage=out.usage.model_dump(),
            served_model=out.served_identity(),
        )
        if not step["cached"]:
            self.usage = self.usage + out.usage
        self.record["usage"] = self.usage.model_dump()
        self.messages.append({"role": "assistant", "content": out.text})
        self._save()
        return out.text

    # --- phases ------------------------------------------------------------------------------
    async def _plan(self) -> None:
        spec = self.spec
        request = PLAN_REQUEST.format(
            task=spec.task,
            description=spec.description,
            readme=spec.path(spec.description).read_text(),
            scoring_rules=spec.scoring_rules,
            scoring=spec.path(spec.scoring_rules).read_text(),
            data=spec.data,
            preview=data_preview(spec),
        )
        while True:
            text = await self._ask("plan", request)
            try:
                plan = parse_plan(text)
            except ValueError as exc:
                request = f"Your plan could not be read: {exc}. Reply with only the JSON object."
                continue
            self.record["plan"] = plan
            if plan["questions"]:
                self.record["questions"] = plan["questions"]
                raise StopAttempt(NEEDS_HUMAN, NEEDS_HUMAN, "the agent asked for a decision")
            return

    def _write(self, rnd: dict[str, Any], text: str) -> str | None:
        """Applies one writing reply; returns a problem to report back, or None."""
        files, questions = parse_files(text)
        if questions:
            self.record["questions"] = questions
            raise StopAttempt(NEEDS_HUMAN, NEEDS_HUMAN, "the agent asked for a decision")
        if not files:
            return "No <file path=...> blocks were found in your reply."
        rejected = []
        for path in files:
            try:
                self.workspace.check(path)
            except WriteRejected as exc:
                rejected.append({"path": exc.path, "reason": exc.reason})
        if rejected:
            rnd["rejected"] = rejected
            raise StopAttempt(
                "protected_write",
                PROTECTED_VIOLATION,
                "; ".join(f"{r['path']}: {r['reason']}" for r in rejected),
            )
        rnd["files_written"] = {
            self.workspace.write(p, c): sha256(c.encode()) for p, c in files.items()
        }
        if "adapter.py" not in self.workspace.files():
            return "adapter.py is required."
        return None

    async def _rounds(self) -> None:
        request = WRITE_REQUEST
        for n in range(1, self.spec.budget.max_rounds + 1):
            text = await self._ask("write", request)
            rnd: dict[str, Any] = {"round": n, "step": len(self.record["steps"])}
            self.record["rounds"].append(rnd)
            problem = self._write(rnd, text)
            if problem is not None:
                rnd["category"] = GENERATION_FAILED
                rnd["detail"] = problem
                request = f"{problem} Reply with the complete files as described."
                self._save()
                continue
            report = await self._validate()
            rnd["validation"] = report
            rnd["category"] = report["category"]
            self._save()
            if self._remaining() <= 0:
                # a result that arrives after the deadline does not count, passed or not
                raise self._out_of_time(f"validation of round {n}")
            if report["passed"]:
                return
            if report["category"] in (ENV_ERROR, SOURCE_CHANGED):
                # not the candidate's fault: nothing for the agent to fix
                raise StopAttempt(report["category"], report["category"], report.get("detail", ""))
            if report["category"] == PROTECTED_VIOLATION:
                raise StopAttempt("protected_change", PROTECTED_VIOLATION, report.get("detail", ""))
            self_test = report.get("self_test") or {}
            request = FEEDBACK.format(
                category=report["category"],
                detail=report.get("detail", ""),
                self_test=self_test.get("status", "not run"),
            )
        last = self.record["rounds"][-1].get("category")
        raise StopAttempt(
            "max_rounds",
            BUDGET_EXHAUSTED,
            f"{self.spec.budget.max_rounds} rounds used; last round: {last}",
        )

    async def _prepare(self) -> None:
        """Get the executor ready (e.g. build the checker image) within its own limit, before
        the attempt's clock starts; a cold build is not charged to ``time_limit_s``."""
        limit = self.spec.budget.prepare_timeout_s
        try:
            self.record["preparation"] = await asyncio.to_thread(self.executor.prepare, limit)
        except asyncio.CancelledError:
            self.executor.cancel()
            raise
        except PrepareTimeout as exc:
            self.record["preparation"] = {"error": str(exc)}
            raise StopAttempt("prepare_timeout", ENV_ERROR, str(exc)) from None
        except SandboxError as exc:
            self.record["preparation"] = {"error": str(exc)}
            raise StopAttempt(ENV_ERROR, ENV_ERROR, str(exc)) from None
        self.started = self.clock()
        self._save()

    async def _validate(self) -> dict[str, Any]:
        """Independent validation in a worker thread, within the time left; cancelling it
        kills the check processes or containers."""
        remaining = self._remaining()
        if remaining <= 0:
            raise self._out_of_time("writing")
        try:
            return await asyncio.to_thread(
                validate, self.spec, self.workspace.root, self.executor, timeout=remaining
            )
        except asyncio.CancelledError:
            self.executor.cancel()
            raise

    def identity(self) -> dict[str, Any]:
        return {
            "spec": self.spec.identity(),
            "source_sha256": source_fingerprint(self.spec),
            "model": self.model.spec(),
            "prompt_version": PROMPT_VERSION,
            "reply_format_version": REPLY_FORMAT_VERSION,
            "validator_version": VALIDATOR_VERSION,
        }

    async def run(self) -> Path:
        spec = self.spec
        self.dir = self._new_dir()
        self.workspace = CandidateWorkspace(self.dir / "candidate", spec)
        self.started = self.clock()
        self.usage = Usage()
        ident = self.identity()
        self.record: dict[str, Any] = {
            "attempt_id": self.dir.name,
            "status": "running",
            "started_at": now(),
            "finished_at": None,
            "fingerprint": fingerprint(ident),
            **ident,
            "spec_path": str(self.spec_path) if self.spec_path else None,
            "budget": spec.budget.model_dump(),
            "usage": self.usage.model_dump(),
            "preparation": None,
            "plan": None,
            "questions": [],
            "steps": [],
            "rounds": [],
        }
        system = SYSTEM_PROMPT.format(
            files=", ".join(CANDIDATE_FILES),
            task=spec.task,
            module=spec.module,
            imports=", ".join(spec.allowed_imports),
        )
        self.messages: list[Message] = [{"role": "system", "content": system}]
        self._save()
        stop = StopAttempt("validated", None)
        try:
            if ident["source_sha256"] != spec.source_sha256:
                raise StopAttempt(
                    SOURCE_CHANGED,
                    SOURCE_CHANGED,
                    f"source fingerprint is {ident['source_sha256']}, the spec pins "
                    f"{spec.source_sha256}",
                )
            await self._prepare()
            await self._plan()
            await self._rounds()
        except StopAttempt as exc:
            stop = exc
        except asyncio.CancelledError:
            self._finish(StopAttempt(CANCELLED, CANCELLED, "the attempt was cancelled"))
            raise
        self._finish(stop)
        return self.dir

    def _finish(self, stop: StopAttempt) -> None:
        files = self.workspace.files()
        passed = stop.category is None
        self.record.update(
            status="passed" if passed else "failed",
            stop_reason=stop.reason,
            category=stop.category,
            stop_detail=stop.detail,
            finished_at=now(),
            elapsed_s=self.clock() - self.started,
            final_files={n: sha256(c.encode()) for n, c in files.items()},
        )
        if files:
            (self.dir / "final.diff").write_text(make_diff(self.spec, files))
            self.record["diff"] = "final.diff"
        spec_arg = self.record["spec_path"] or "<spec.yaml>"
        self.record["reproduce"] = [
            f"harness onboard -c {spec_arg} -m <provider/model>  # same cache replays the replies",
            f"harness onboard-verify {self.dir}",
            f"git apply {self.dir / 'final.diff'} && cp -r {self.spec.path(self.spec.data)} "
            f"src/harness/tasks/{self.spec.module}_data  # after human review only",
        ]
        self._save()


def verify_attempt(attempt_dir: Path, executor: Executor | None = None) -> dict[str, Any]:
    """Validate a saved attempt's final candidate again. A changed source package or changed
    candidate files are reported, never covered by the old result."""
    attempt_dir = Path(attempt_dir)
    record = json.loads((attempt_dir / "attempt.json").read_text())
    spec = OnboardSpec(**record["spec"])
    workspace = CandidateWorkspace(attempt_dir / "candidate", spec)
    report: dict[str, Any] = {"attempt": str(attempt_dir), "at": now()}
    if workspace.hashes() != record.get("final_files"):
        report.update(
            passed=False,
            category="candidate_changed",
            detail="candidate files differ from the attempt record",
        )
    else:
        report |= validate(spec, workspace.root, executor, expected_source=record["source_sha256"])
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    write_json(attempt_dir / f"verify-{stamp}.json", report)
    return report
