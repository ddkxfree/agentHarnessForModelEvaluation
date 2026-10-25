from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from harness.core.fingerprint import fingerprint
from harness.core.judge import JudgeError, JudgePanel, JudgeSpec
from harness.core.types import JudgeCall, JudgeRecord, Message, Sample, Score

# How a judge's reply is turned into a dict; bump when the rules change.
PARSER_VERSION = "json_object_v1"


class NeedsNewJudgement(Exception):
    """The saved judge calls were made with other prompts than the scorer would send now (rubric,
    prompt or judges changed), so re-parsing them would not be a judgement of the new setup."""


def quote(tag: str, text: str) -> str:
    """``text`` inside ``<tag>`` delimiters. Delimiter-like sequences inside the text are
    defused, so graded content cannot close the block and pose as instructions."""
    defused = re.sub(rf"<(\s*/?\s*{re.escape(tag)})", r"&lt;\1", text, flags=re.IGNORECASE)
    return f"<{tag}>\n{defused}\n</{tag}>"


def extract_json(text: str) -> dict[str, Any]:
    """The JSON object a judge replied with: the last fenced ```json block, else the span from
    the first ``{`` to the last ``}``. Raises ValueError when there is none."""
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        candidate = fenced[-1]
    else:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("no JSON object in the reply")
        candidate = text[start : end + 1]
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from None
    if not isinstance(value, dict):
        raise ValueError("the reply's JSON is not an object")
    return value


def call_problem(call: JudgeCall) -> tuple[str, str] | None:
    """(status, error) for a call that cannot be parsed at all, else None."""
    if call.error is not None:
        return "call_error", call.error
    if call.finish_reason == "length":
        return "truncated", "the reply was cut off at max_tokens"
    return None


Request = tuple[str, str, list[Message]]  # judge name, purpose, messages


class JudgeScorerBase:
    """Calls every request through the panel, then parses and aggregates the saved calls. The
    same parse and aggregation run offline in ``reparse``, so a new parser or aggregation rule
    can be applied to saved judgements without calling a model."""

    name = "judge"
    mode = "judge"
    requires_judges = True
    prompt_version = ""
    aggregation = ""

    def __init__(self, judges: list[JudgeSpec]) -> None:
        if not judges:
            raise ValueError("a judge scorer needs at least one judge")
        self.judges = judges

    # --- implemented by each mode -------------------------------------------------------
    def requests(self, sample: Sample, text: str) -> list[Request]:
        raise NotImplementedError

    def parse(self, call: JudgeCall) -> dict[str, Any]:
        raise NotImplementedError

    def aggregate(self, verdicts: list[dict[str, Any]]) -> tuple[Score | None, dict[str, Any]]:
        """The score, or None when the verdicts do not support one, plus the summary."""
        raise NotImplementedError

    def prompt_description(self) -> dict[str, Any]:
        raise NotImplementedError

    # --- shared ---------------------------------------------------------------------------
    async def judge(
        self,
        sample: Sample,
        text: str,
        panel: JudgePanel,
        record: JudgeRecord,
        namespace: str | None = None,
    ) -> Score:
        missing = sorted({j.name for j in self.judges} - set(panel.judges))
        if missing:
            raise ValueError(f"no model for judges {missing}")
        requests = self.requests(sample, text)
        # Calls are recorded in request order, whatever order they finish in.
        calls = await asyncio.gather(
            *(
                panel.call(
                    judge, messages, purpose=purpose, record=JudgeRecord(), namespace=namespace
                )
                for judge, purpose, messages in requests
            )
        )
        record.calls.extend(calls)
        return self.conclude(record)

    def reparse(self, sample: Sample, text: str, record: JudgeRecord) -> Score:
        expected = self.requests(sample, text)
        saved = [(c.judge, c.purpose, c.messages) for c in record.calls]
        if expected != saved:
            raise NeedsNewJudgement("the saved judge prompts differ from the current ones")
        return self.conclude(record)

    def conclude(self, record: JudgeRecord) -> Score:
        record.verdicts = [self.parse(c) for c in record.calls]
        score, record.aggregate = self.aggregate(record.verdicts)
        if score is None:
            raise JudgeError(record.aggregate.get("error", "no usable judgement"))
        return score

    def inputs_fingerprint(self) -> str:
        """Everything that decides what the judges are asked and by whom."""
        return fingerprint(
            {
                "mode": self.mode,
                "prompt": self.prompt_description(),
                "judges": [j.model_dump(mode="json") for j in self.judges],
            }
        )

    def describe(self) -> dict[str, Any]:
        desc = {
            "name": self.name,
            "mode": self.mode,
            "prompt": self.prompt_description(),
            "parser_version": PARSER_VERSION,
            "aggregation": self.aggregation,
            "judges": [j.model_dump(mode="json") for j in self.judges],
            "inputs_fingerprint": self.inputs_fingerprint(),
        }
        desc["fingerprint"] = fingerprint(desc)
        return desc


def require_all_valid(verdicts: list[dict[str, Any]]) -> str | None:
    """Declared rule shared by both modes: a judgement needs every verdict to be valid, so a
    failed or unparsable judge never drops out silently to leave a result."""
    bad = [v for v in verdicts if v["status"] != "ok"]
    if not bad:
        return None
    parts = ", ".join(f"{v['judge']}/{v.get('purpose', '')}: {v['status']}" for v in bad)
    return f"invalid judge verdicts ({parts})"
