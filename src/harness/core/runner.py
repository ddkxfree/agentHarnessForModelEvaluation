from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from harness.analysis.stats import summarize
from harness.core.cache import DiskCache, cache_key
from harness.core.io import write_json, write_results
from harness.core.manifest import environment_info, now, task_entry
from harness.core.model import Model, ModelCallError
from harness.core.retry import retry_async
from harness.core.scorer import (
    SUBMISSION_ARTIFACT,
    check_score,
    grades_in_fresh_sandbox,
    requires_sandbox,
)
from harness.core.task import Task
from harness.core.trajectory import Trajectory, recording
from harness.core.types import (
    ErrorKind,
    GenerateConfig,
    Message,
    ModelOutput,
    Sample,
    SampleResult,
    SolveResult,
    Usage,
    classify,
)
from harness.sandbox.base import Sandbox, SandboxError, SandboxProvider

log = logging.getLogger(__name__)

# How long closing a sandbox may take before it is recorded as a cleanup failure.
CLOSE_TIMEOUT = 120.0


def sample_dirname(sample_id: str, epoch: int = 1) -> str:
    """Readable, collision-free directory name for a sample's artifacts."""
    digest = hashlib.sha256(sample_id.encode()).hexdigest()[:8]
    name = f"{_slug(sample_id)}-{digest}"
    return name if epoch == 1 else f"{name}-e{epoch}"


def cache_namespace(namespace: str | None, epoch: int) -> str | None:
    """Cache namespace of one epoch. Epochs never share cached outputs, while re-running the
    same epoch (e.g. resuming an interrupted run) does. Epoch 1 without a namespace keeps the
    keys of runs made before epochs existed."""
    if namespace is None and epoch == 1:
        return None
    return f"{namespace or ''}#epoch={epoch}"


async def close_sandbox(sandbox: Sandbox, sample_id: str) -> str | None:
    """Close ``sandbox``; return a description of the failure instead of raising."""
    try:
        await asyncio.wait_for(sandbox.close(), CLOSE_TIMEOUT)
    except Exception as exc:
        error = _describe(exc)
        log.warning("failed to close sandbox %s of %s: %s", sandbox.describe(), sample_id, error)
        return error
    return None


CachePolicy = Literal["auto", "always", "never"]


def use_cache(policy: CachePolicy, config: GenerateConfig) -> bool:
    """Whether model outputs may be read from and written to the cache.

    A cached answer is one fixed draw, so reusing it under sampling (temperature > 0) would
    silently turn repeated runs into copies of the first. ``auto`` therefore caches only
    greedy decoding; ``always`` also caches sampled outputs, e.g. to resume a crashed run.
    """
    if policy == "never":
        return False
    if policy == "always":
        return True
    if policy == "auto":
        return config.temperature == 0
    raise ValueError(f"unknown cache policy {policy!r}; use auto, always or never")


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
    artifact_dir: Path | None = None,
    epoch: int = 1,
    namespace: str | None = None,
) -> list[SampleResult]:
    """Solve and score every sample. ``artifact_dir`` receives files such as untruncated tool
    outputs, one subdirectory per sample; without it only their hashes are recorded."""
    sem = asyncio.Semaphore(concurrency)
    model_spec = model.spec()
    key_namespace = cache_namespace(namespace, epoch)
    if task.sandbox is not None and sandbox_provider is None:
        raise ValueError(f"task {task.name!r} needs a sandbox provider")

    def retryable(exc: BaseException) -> bool:
        return is_timeout(exc) or model.is_retryable(exc)

    async def run_one(sample: Sample) -> SampleResult:
        result = SampleResult(sample=sample, epoch=epoch)
        served: set[str] = set()
        # identities carried by cached outputs: where those responses originally came from
        served_cached: set[str] = set()

        def count_attempt(_: int) -> None:
            result.attempts += 1

        async def generate(
            messages: list[Message], tools: list[dict[str, Any]] | None = None
        ) -> ModelOutput:
            key = cache_key(model_spec, messages, config, tools, key_namespace)
            if cache is not None and (hit := cache.get(key)) is not None:
                if (identity := hit.served_identity()) is not None:
                    served_cached.add(identity)
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
            if (identity := out.served_identity()) is not None:
                served.add(identity)
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

        async def solve(sandbox: Sandbox | None) -> SolveResult:
            timer = asyncio.timeout(task.time_limit)
            try:
                async with timer:
                    return await task.solver(sample, generate, sandbox)
            except TimeoutError:
                if not timer.expired():
                    raise
            # Cancelled by the time limit: score whatever state the solver left behind.
            usage = sum((s.usage for s in trajectory.steps), Usage())
            output = ModelOutput(text="", finish_reason="time_limit", usage=usage)
            return SolveResult(messages=trajectory.messages, output=output, steps=trajectory.steps)

        async def close(sandbox: Sandbox) -> None:
            if (error := await close_sandbox(sandbox, sample.id)) is not None:
                result.cleanup_error = "; ".join(filter(None, [result.cleanup_error, error]))

        trajectory = Trajectory(artifact_dir, sample_dirname(sample.id, epoch))
        labels = {"harness.task": task.name, "harness.sample": sample.id}
        if epoch != 1:
            labels["harness.epoch"] = str(epoch)
        async with sem:
            started = time.perf_counter()
            sandbox: Sandbox | None = None
            grader: Sandbox | None = None
            # Which party is to blame if the next statement raises.
            stage = ErrorKind.ENV
            try:
                with recording(trajectory):
                    if task.sandbox is not None:
                        assert sandbox_provider is not None
                        sandbox = await sandbox_provider.create(task.sandbox(sample), labels=labels)
                        result.sandbox = sandbox.describe()
                    stage = ErrorKind.SOLVER
                    solved = await solve(sandbox)
                    result.messages, result.output, result.steps = (
                        solved.messages,
                        solved.output,
                        solved.steps,
                    )
                    stage = ErrorKind.SCORER
                    text = solved.output.text
                    if grades_in_fresh_sandbox(task.scorer):
                        assert sandbox is not None and sandbox_provider is not None
                        submission = await task.scorer.collect(sample, text, sandbox)
                        trajectory.save_artifact(SUBMISSION_ARTIFACT, submission)
                        await close(sandbox)
                        sandbox = None
                        stage = ErrorKind.ENV
                        grader = await sandbox_provider.create(
                            task.sandbox(sample), labels={**labels, "harness.phase": "grade"}
                        )
                        result.grading_sandbox = grader.describe()
                        stage = ErrorKind.SCORER
                        score = await task.scorer.grade(sample, submission, grader)
                    elif requires_sandbox(task.scorer):
                        score = await task.scorer(sample, text, sandbox)
                    else:
                        score = task.scorer(sample, text)
                    result.score = check_score(score)
            except Exception as exc:
                fail(exc, stage)
                if result.output is None:
                    result.messages, result.steps = trajectory.messages, trajectory.steps
            finally:
                result.artifacts = trajectory.artifacts
                for sb in (sandbox, grader):
                    if sb is not None:
                        await close(sb)
                result.wall_time_s = time.perf_counter() - started
        result.served_models = sorted(served)
        result.cached_served_models = sorted(served_cached)
        result.outcome = classify(result)
        if on_done:
            on_done(result)
        return result

    try:
        return list(await asyncio.gather(*(run_one(s) for s in samples)))
    finally:
        if sandbox_provider is not None:
            await sandbox_provider.drain()


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
    cache_policy: CachePolicy = "auto",
    epochs: int = 1,
    first_epoch: int = 1,
    namespace: str | None = None,
) -> Path:
    """Run every task, writing per-sample logs, summaries and a manifest under one run dir.

    ``progress`` is an optional ``rich.progress.Progress`` used to display progress.
    ``cache_policy`` decides per task whether ``cache`` is used; see ``use_cache``.
    Every sample is run ``epochs`` times, numbered from ``first_epoch``; each epoch has its own
    cache namespace (see ``cache_namespace``).
    """
    if epochs < 1:
        raise ValueError("epochs must be at least 1")
    epoch_numbers = list(range(first_epoch, first_epoch + epochs))
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
            reuse = cache is not None and use_cache(cache_policy, config)
            entry["cache"] = {
                "enabled": cache is not None,
                "policy": cache_policy,
                "used": reuse,
                "namespace": namespace,
            }
            entry["epochs"] = epoch_numbers
            if task.sandbox is not None and sandbox_provider is not None:
                entry["sandbox_provider"] = sandbox_provider.describe()
            manifest["tasks"].append(entry)
            write_json(manifest_path, manifest)

            bar = progress.add_task(task.name, total=len(samples) * epochs) if progress else None
            task_dir = run_dir / task.name
            samples_path = task_dir / "samples.jsonl"
            samples_path.parent.mkdir(parents=True, exist_ok=True)
            # Append each sample as it finishes so an interrupted run keeps completed work;
            # the file is rewritten in sample order once the task is done.
            with open(samples_path, "w") as live:

                def on_done(r: SampleResult, live: Any = live, bar: Any = bar) -> None:
                    live.write(r.model_dump_json() + "\n")
                    live.flush()
                    if progress is not None:
                        progress.advance(bar)

                results = []
                for epoch in epoch_numbers:
                    results += await run_samples(
                        task,
                        samples,
                        model,
                        config,
                        cache=cache if reuse else None,
                        concurrency=concurrency,
                        retries=retries,
                        on_done=on_done,
                        sandbox_provider=sandbox_provider,
                        artifact_dir=task_dir / "artifacts",
                        epoch=epoch,
                        namespace=namespace,
                    )
            summary = summarize(results)
            write_results(samples_path, results)
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
