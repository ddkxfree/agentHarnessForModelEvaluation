from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.analysis.stats import summarize
from harness.core.cache import DiskCache, cache_key
from harness.core.io import write_json, write_results
from harness.core.manifest import environment_info, now, task_entry
from harness.core.model import Model, ModelCallError
from harness.core.retry import retry_async
from harness.core.scorer import check_score, requires_sandbox
from harness.core.task import Task
from harness.core.types import (
    ErrorKind,
    GenerateConfig,
    Message,
    ModelOutput,
    Sample,
    SampleResult,
    classify,
)
from harness.sandbox.base import Sandbox, SandboxError, SandboxProvider


def is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


async def run_samples(
    task: Task,
    samples: list[Sample],
    model: Model,
    config: GenerateConfig,
    *,
    cache: DiskCache | None = None,
    concurrency: int = 8,
    retries: int = 3,
    retry_base_delay: float = 1.0,
    on_done: Callable[[SampleResult], None] | None = None,
    sandbox_provider: SandboxProvider | None = None,
) -> list[SampleResult]:
    sem = asyncio.Semaphore(concurrency)
    model_spec = model.spec()
    if task.sandbox is not None and sandbox_provider is None:
        raise ValueError(f"task {task.name!r} needs a sandbox provider")

    def retryable(exc: BaseException) -> bool:
        return is_timeout(exc) or model.is_retryable(exc)

    async def run_one(sample: Sample) -> SampleResult:
        result = SampleResult(sample=sample)

        def count_attempt(_: int) -> None:
            result.attempts += 1

        async def generate(
            messages: list[Message], tools: list[dict[str, Any]] | None = None
        ) -> ModelOutput:
            key = cache_key(model_spec, messages, config, tools)
            if cache is not None and (hit := cache.get(key)) is not None:
                return hit.model_copy(update={"cached": True})
            try:
                out = await retry_async(
                    lambda: asyncio.wait_for(
                        model.generate(messages, config, tools), config.timeout
                    ),
                    retries=retries,
                    is_retryable=retryable,
                    base_delay=retry_base_delay,
                    on_attempt=count_attempt,
                )
            except Exception as exc:
                raise ModelCallError(_describe(exc)) from exc
            if cache is not None:
                cache.put(key, out)
            return out

        def fail(exc: Exception, stage: ErrorKind) -> None:
            if isinstance(exc, ModelCallError):
                result.error, result.error_kind = str(exc), ErrorKind.MODEL
            elif isinstance(exc, SandboxError):
                result.error, result.error_kind = _describe(exc), ErrorKind.ENV
            else:
                result.error, result.error_kind = _describe(exc), stage

        async with sem:
            sandbox: Sandbox | None = None
            # Which party is to blame if the next statement raises.
            stage = ErrorKind.ENV
            try:
                if task.sandbox is not None:
                    assert sandbox_provider is not None
                    labels = {"harness.task": task.name, "harness.sample": sample.id}
                    sandbox = await sandbox_provider.create(task.sandbox(sample), labels=labels)
                stage = ErrorKind.SOLVER
                solved = await task.solver(sample, generate, sandbox)
                result.messages, result.output, result.steps = (
                    solved.messages,
                    solved.output,
                    solved.steps,
                )
                stage = ErrorKind.SCORER
                if requires_sandbox(task.scorer):
                    score = await task.scorer(sample, solved.output.text, sandbox)
                else:
                    score = task.scorer(sample, solved.output.text)
                result.score = check_score(score)
            except Exception as exc:
                fail(exc, stage)
            finally:
                if sandbox is not None:
                    with contextlib.suppress(Exception):
                        await sandbox.close()
        result.outcome = classify(result)
        if on_done:
            on_done(result)
        return result

    return list(await asyncio.gather(*(run_one(s) for s in samples)))


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:60]


def new_run_dir(root: Path, model: Model) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    base = root / f"{stamp}-{_slug(model.name)}"
    path, i = base, 1
    while path.exists():
        i += 1
        path = base.with_name(f"{base.name}-{i}")
    path.mkdir(parents=True)
    return path


async def run_eval(
    model: Model,
    tasks: list[tuple[Task, GenerateConfig]],
    *,
    output_dir: Path,
    cache: DiskCache | None = None,
    concurrency: int = 8,
    retries: int = 3,
    run_config: dict[str, Any] | None = None,
    progress: Any | None = None,
    sandbox_provider: SandboxProvider | None = None,
) -> Path:
    """Run every task, writing per-sample logs, summaries and a manifest under one run dir.

    ``progress`` is an optional ``rich.progress.Progress`` used to display progress.
    """
    run_dir = new_run_dir(Path(output_dir), model)
    manifest: dict[str, Any] = {
        "run_id": run_dir.name,
        "status": "running",
        "started_at": now(),
        "finished_at": None,
        "model": model.spec(),
        "run_config": run_config or {},
        "environment": environment_info(),
        "tasks": [],
        "results": {},
    }
    manifest_path = run_dir / "manifest.json"
    write_json(manifest_path, manifest)

    try:
        for task, config in tasks:
            samples = task.load()
            entry = task_entry(task, samples)
            entry["generate_config"] = config.model_dump()
            if task.sandbox is not None and sandbox_provider is not None:
                entry["sandbox_provider"] = sandbox_provider.describe()
            manifest["tasks"].append(entry)
            write_json(manifest_path, manifest)

            on_done = None
            if progress is not None:
                bar = progress.add_task(task.name, total=len(samples))

                def on_done(_: SampleResult, bar: Any = bar) -> None:
                    progress.advance(bar)

            results = await run_samples(
                task,
                samples,
                model,
                config,
                cache=cache,
                concurrency=concurrency,
                retries=retries,
                on_done=on_done,
                sandbox_provider=sandbox_provider,
            )
            summary = summarize(results)
            write_results(run_dir / task.name / "samples.jsonl", results)
            write_json(run_dir / task.name / "summary.json", summary)
            manifest["results"][task.name] = summary
            write_json(manifest_path, manifest)
        manifest["status"] = "completed"
    except BaseException:
        manifest["status"] = "interrupted"
        raise
    finally:
        manifest["finished_at"] = now()
        write_json(manifest_path, manifest)
    return run_dir
