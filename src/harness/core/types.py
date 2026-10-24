from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# OpenAI chat format. Assistant messages may carry "tool_calls"; tool results use role "tool".
Message = dict[str, Any]


class Sample(BaseModel):
    id: str
    input: str
    target: str
    choices: list[str] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenerateConfig(BaseModel):
    temperature: float = 0.0
    max_tokens: int = 1024
    top_p: float | None = None
    seed: int | None = None
    stop: list[str] | None = None
    timeout: float = 120.0

    def cache_fields(self) -> dict[str, Any]:
        # timeout affects how long we wait, not what the model returns
        return self.model_dump(exclude={"timeout"})


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str

    def to_message(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


class ModelOutput(BaseModel):
    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage = Field(default_factory=Usage)
    latency_s: float = 0.0
    cached: bool = False
    # What the server says actually answered, which may differ from the requested alias.
    served_model: str | None = None
    system_fingerprint: str | None = None

    def served_identity(self) -> str | None:
        if self.served_model is None:
            return None
        if self.system_fingerprint:
            return f"{self.served_model}@{self.system_fingerprint}"
        return self.served_model


class Artifact(BaseModel):
    """A file saved next to a sample's results, e.g. a tool output too long to show the model."""

    name: str
    sha256: str
    size: int
    # Relative to the task's artifact directory (``<run>/<task>/artifacts``); None when the
    # run keeps no artifact directory.
    path: str | None = None


class Step(BaseModel):
    """One model turn of an agent loop and the tool executions it triggered."""

    index: int
    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    # What the model was shown for each tool call, after any truncation.
    observations: list[str] = Field(default_factory=list)
    # Per tool call: name of the artifact holding the untruncated output, or None if the
    # observation already is the complete output.
    raw_outputs: list[str | None] = Field(default_factory=list)
    exit_codes: list[int | None] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    latency_s: float = 0.0
    tool_latency_s: float = 0.0


class SolveResult(BaseModel):
    messages: list[Message]
    output: ModelOutput
    steps: list[Step] = Field(default_factory=list)


class Score(BaseModel):
    # NaN or out-of-range values would silently poison accuracy; reject them at construction.
    value: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    extracted: str | None = None
    method: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Outcome(StrEnum):
    CORRECT = "correct"
    WRONG = "wrong"
    EXTRACT_FAILED = "extract_failed"
    TRUNCATED = "truncated"
    STEP_LIMIT = "step_limit"
    TIME_LIMIT = "time_limit"
    API_ERROR = "api_error"
    SOLVER_ERROR = "solver_error"
    SCORER_ERROR = "scorer_error"
    ENV_ERROR = "env_error"


class ErrorKind(StrEnum):
    """Who failed, so infrastructure or harness bugs are not counted as model failures."""

    MODEL = "model"  # the model call failed after retries
    SOLVER = "solver"  # the solver or agent code raised
    SCORER = "scorer"  # the scorer raised or produced an invalid score
    ENV = "env"  # the sandbox or other environment failed


ERROR_OUTCOMES = {
    ErrorKind.MODEL: Outcome.API_ERROR,
    ErrorKind.SOLVER: Outcome.SOLVER_ERROR,
    ErrorKind.SCORER: Outcome.SCORER_ERROR,
    ErrorKind.ENV: Outcome.ENV_ERROR,
}


class SampleResult(BaseModel):
    sample: Sample
    messages: list[Message] = Field(default_factory=list)
    output: ModelOutput | None = None
    steps: list[Step] = Field(default_factory=list)
    score: Score | None = None
    error: str | None = None
    error_kind: ErrorKind | None = None
    outcome: Outcome | None = None
    attempts: int = 0
    artifacts: list[Artifact] = Field(default_factory=list)
    # Served model identities reported by the server, over all of the sample's model calls.
    served_models: list[str] = Field(default_factory=list)
    # Identities recorded with outputs replayed from the cache. They say which server produced
    # those responses originally, not what the server serves now.
    cached_served_models: list[str] = Field(default_factory=list)
    # Identity of the sandbox the sample ran in, e.g. container and image ids.
    sandbox: dict[str, Any] | None = None
    # The fresh sandbox a SubmissionScorer graded in, if any.
    grading_sandbox: dict[str, Any] | None = None
    # Closing the sandbox failed; the result is still valid but resources may have leaked.
    cleanup_error: str | None = None
    # Scoring states replaced by `harness rescore`, oldest first.
    rescore_history: list[dict[str, Any]] = Field(default_factory=list)
    # Seconds from sandbox creation to cleanup, excluding time queued for a concurrency slot.
    wall_time_s: float | None = None

    def usage(self) -> Usage:
        """Tokens spent on the sample, including steps of a solver that failed midway."""
        if self.steps:
            return sum((s.usage for s in self.steps), Usage())
        return self.output.usage if self.output else Usage()


def classify(result: SampleResult) -> Outcome:
    if result.error_kind is not None:
        return ERROR_OUTCOMES[result.error_kind]
    if result.error is not None or result.output is None or result.score is None:
        return Outcome.API_ERROR
    if result.score.value >= 1.0:
        return Outcome.CORRECT
    if result.output.finish_reason == "length":
        return Outcome.TRUNCATED
    if result.output.finish_reason == "max_steps":
        return Outcome.STEP_LIMIT
    if result.output.finish_reason == "time_limit":
        return Outcome.TIME_LIMIT
    if result.score.extracted is None:
        return Outcome.EXTRACT_FAILED
    return Outcome.WRONG
