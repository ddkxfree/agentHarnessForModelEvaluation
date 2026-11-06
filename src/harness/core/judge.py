"""Judge plumbing shared by every LLM-as-judge scorer.

A judge is a model that grades another model's output. Its calls go through the same model
adapters, timeouts, retries and request cache as the evaluated model, but are recorded and
counted apart from it: ``SampleResult.judge`` keeps every judge call (the exact messages, the
raw response, usage and served model) together with the parsed verdicts, so a saved
judgement can be re-parsed or re-aggregated offline.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field

from harness.core.cache import DiskCache, cache_key
from harness.core.observability import call_context, new_id
from harness.core.retry import retry_async
from harness.core.trajectory import current_trajectory
from harness.core.types import (
    GenerateConfig,
    JudgeCall,
    JudgeRecord,
    Message,
    Sample,
    Score,
    Usage,
)

__all__ = ["JudgeCall", "JudgeError", "JudgePanel", "JudgeRecord", "JudgeSpec", "JudgeScorer"]

if TYPE_CHECKING:
    from harness.core.model import Model


class JudgeError(Exception):
    """The judges did not produce a usable judgement (a call failed or a response could not be
    parsed). Reported as outcome ``judge_error``, never as a low score for the answer."""


class JudgeSpec(BaseModel):
    """One judge: which model grades, with which generation settings."""

    name: str
    model: str
    base_url: str | None = None
    generate: GenerateConfig = Field(
        default_factory=lambda: GenerateConfig(temperature=0.0, max_tokens=1024)
    )


class JudgePanel:
    """The judges of one task, each a model plus its settings and (optional) cache."""

    def __init__(
        self,
        judges: list[tuple[JudgeSpec, Model]],
        *,
        cache: Callable[[JudgeSpec], DiskCache | None] = lambda spec: None,
        retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        names = [spec.name for spec, _ in judges]
        if len(set(names)) != len(names):
            raise ValueError(f"judge names must be unique: {names}")
        self.judges = {spec.name: (spec, model) for spec, model in judges}
        self._cache = {spec.name: cache(spec) for spec, _ in judges}
        self.retries = retries
        self.retry_base_delay = retry_base_delay

    def describe(self) -> list[dict[str, Any]]:
        """Judge identities for the manifest. Judges with the same model and settings are not
        independent votes: they are marked so, even though their calls are cached apart."""
        seen: dict[str, str] = {}
        out = []
        for name, (spec, model) in self.judges.items():
            identity = repr((model.spec(), spec.generate.cache_fields()))
            out.append(
                {
                    "name": name,
                    "model": model.spec(),
                    "generate": spec.generate.model_dump(),
                    "cache": self._cache[name] is not None,
                    "same_as": seen.get(identity),
                }
            )
            seen.setdefault(identity, name)
        return out

    async def call(
        self,
        judge: str,
        messages: list[Message],
        *,
        purpose: str,
        record: JudgeRecord,
        namespace: str | None = None,
    ) -> JudgeCall:
        """Ask one judge; the call is appended to ``record`` whether it succeeds or not."""
        spec, model = self.judges[judge]
        cache = self._cache[judge]
        # Judges never share cached responses, so two judges are two calls, not a replay.
        key_namespace = "/".join(filter(None, [namespace, "judge", judge]))
        trajectory = current_trajectory()
        call = JudgeCall(
            call_id=new_id("judge"),
            judge=judge,
            purpose=purpose,
            model=model.spec(),
            messages=messages,
            usage=Usage(input_tokens=None, output_tokens=None, source="unreported"),
        )
        record.calls.append(call)
        key = cache_key(model.spec(), messages, spec.generate, None, key_namespace)
        if cache is not None and (hit := cache.get(key)) is not None:
            out, call.cached = hit, True
            trajectory.event(
                "judge_cache_replayed",
                call_id=call.call_id,
                origin_call_id=hit.call_id,
                judge=judge,
                response=hit.model_dump(mode="json"),
            )
        else:

            def count(_: int) -> None:
                call.attempts += 1

            async def invoke():
                request_id = new_id("judge_request")
                request = {
                    "call_id": request_id,
                    "logical_call_id": call.call_id,
                    "retry_index": len(call.requests),
                    "status": "started",
                }
                call.requests.append(request)
                trajectory.event(
                    "judge_call_started",
                    judge=judge,
                    purpose=purpose,
                    messages=messages,
                    model=model.spec(),
                    config=spec.generate.model_dump(mode="json"),
                    **request,
                )
                identity = trajectory.journal.identity if trajectory.journal else {}
                token = call_context.set(
                    {
                        "run_id": identity.get("run_id", "detached"),
                        "attempt_id": identity.get("attempt_id", "detached"),
                        "call_id": request_id,
                    }
                )
                start = time.perf_counter()
                try:
                    response = await asyncio.wait_for(
                        model.generate(messages, spec.generate), spec.generate.timeout
                    )
                    response = response.model_copy(update={"call_id": request_id})
                    request.update(
                        status="completed",
                        usage=response.usage.model_dump(mode="json"),
                        latency_s=time.perf_counter() - start,
                    )
                    trajectory.event(
                        "judge_call_completed",
                        judge=judge,
                        response=response.model_dump(mode="json"),
                        raw_response=response.raw_response,
                        **request,
                    )
                    return response
                except BaseException as exc:
                    request.update(status="error", usage=None, error=f"{type(exc).__name__}: {exc}")
                    trajectory.event("judge_call_interrupted", judge=judge, **request)
                    raise
                finally:
                    call_context.reset(token)

            started = time.perf_counter()
            try:
                out = await retry_async(
                    invoke,
                    retries=self.retries,
                    is_retryable=lambda e: isinstance(e, TimeoutError) or model.is_retryable(e),
                    base_delay=self.retry_base_delay,
                    on_attempt=count,
                )
            except Exception as exc:
                call.error = f"{type(exc).__name__}: {exc}"
                call.latency_s = time.perf_counter() - started
                trajectory.event("judge_finished", call_id=call.call_id, error=call.error)
                return call
            call.latency_s = time.perf_counter() - started
            if cache is not None:
                cache.put(key, out)
        call.text = out.text
        call.finish_reason = out.finish_reason
        call.usage = out.usage
        call.served_model = out.served_identity()
        trajectory.event("judge_finished", call_id=call.call_id)
        return call


class JudgeScorer(Protocol):
    """Scores with one or more judge models.

    ``judge`` makes the judge calls through the panel; ``reparse`` rebuilds the score from a
    saved record without calling any model (new parser or aggregation rule). Changing what the
    judges see (rubric, prompt, judge model or settings) changes ``inputs_fingerprint``; such
    runs need new judge calls and cannot be rescored offline.
    """

    name: str
    requires_judges: bool
    judges: list[JudgeSpec]

    async def judge(
        self,
        sample: Sample,
        text: str,
        panel: JudgePanel,
        record: JudgeRecord,
        namespace: str | None = None,
    ) -> Score: ...

    def reparse(self, sample: Sample, text: str, record: JudgeRecord) -> Score: ...

    def inputs_fingerprint(self) -> str: ...

    def describe(self) -> dict[str, Any]: ...


def requires_judges(scorer: object) -> bool:
    return bool(getattr(scorer, "requires_judges", False))
