from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Protocol

from harness.core.fingerprint import fingerprint
from harness.core.types import Sample, Score

if TYPE_CHECKING:
    from harness.sandbox.base import Sandbox

_NUM = r"-?[\d,]*\.?\d+"


class Scorer(Protocol):
    """Scores from the raw model output alone, so saved runs can be rescored offline."""

    name: str

    def __call__(self, sample: Sample, text: str) -> Score: ...

    def describe(self) -> dict[str, Any]: ...


class SandboxScorer(Protocol):
    """Scores by inspecting the sample's sandbox after the solver finished, e.g. running tests.

    The sandbox is gone once the sample completes, so these runs cannot be rescored offline.
    """

    name: str
    requires_sandbox: bool

    async def __call__(self, sample: Sample, text: str, sandbox: Sandbox) -> Score: ...

    def describe(self) -> dict[str, Any]: ...


def requires_sandbox(scorer: Scorer | SandboxScorer) -> bool:
    return bool(getattr(scorer, "requires_sandbox", False))


def normalize_number(raw: str) -> str | None:
    """Canonical exact form of a number, or None if ``raw`` is not a finite number.

    Uses Decimal so large integers are not rounded (floats would make 10**25 + 1 == 10**25),
    and rejects NaN/Infinity so they can never compare equal to anything. Only exact
    operations are used: arithmetic such as ``Decimal.normalize()`` rounds to the current
    context precision (28 digits by default) and would make long decimals compare equal.
    """
    s = raw.strip().replace(",", "").replace("$", "").rstrip(".")
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    if value == value.to_integral_value():
        return str(int(value))
    # Not integral, so the fixed-point form has a non-zero fractional digit to stop at.
    return format(value, "f").rstrip("0")


class NumberMatchScorer:
    """Extracts a final numeric answer and compares it to the target.

    Rules are tried in order; the name of the rule that matched is recorded so that
    a run can report how often the model followed the requested answer format.
    """

    name = "number_match"

    RULES: list[tuple[str, str]] = [
        ("answer_is", rf"answer is[:\s]*\**\s*\$?\s*({_NUM})"),
        ("hash_marker", rf"####\s*\$?\s*({_NUM})"),
        ("boxed", rf"\\boxed\{{\s*\$?\s*({_NUM})\s*\}}"),
    ]

    def __init__(self, *, fallback_last_number: bool = True) -> None:
        self.fallback_last_number = fallback_last_number

    def extract(self, text: str) -> tuple[str | None, str | None]:
        for rule, pattern in self.RULES:
            matches = re.findall(pattern, text, flags=re.IGNORECASE)
            for m in reversed(matches):
                if (value := normalize_number(m)) is not None:
                    return value, rule
        if self.fallback_last_number:
            for m in reversed(re.findall(_NUM, text)):
                if (value := normalize_number(m)) is not None:
                    return value, "last_number"
        return None, None

    def __call__(self, sample: Sample, text: str) -> Score:
        target = normalize_number(sample.target)
        if target is None:
            raise ValueError(f"target {sample.target!r} of sample {sample.id} is not a number")
        extracted, method = self.extract(text)
        return Score(value=float(extracted == target), extracted=extracted, method=method)

    def describe(self) -> dict[str, Any]:
        desc = {
            "name": self.name,
            "rules": self.RULES,
            "fallback_last_number": self.fallback_last_number,
            "normalization": "decimal_exact_v2",
        }
        desc["fingerprint"] = fingerprint(desc)
        return desc


class ChoiceScorer:
    """Extracts a multiple-choice letter (A, B, C, ...) and compares it to the target."""

    name = "choice"

    def __init__(self, letters: str = "ABCD", *, fallback_last_letter: bool = True) -> None:
        self.letters = letters
        self.fallback_last_letter = fallback_last_letter
        cls = f"[{letters}]"
        self.rules: list[tuple[str, str]] = [
            # only the phrase is case-insensitive, so "the answer is a bit..." is not "A"
            ("answer_is", rf"(?i:answer is)[:\s]*\**\s*\(?({cls})\b"),
            ("only_letter", rf"^\s*\(?({cls})\)?\s*\.?\s*$"),
            ("leading_letter", rf"^\s*\(?({cls})[\)\.:]"),
        ]

    def extract(self, text: str) -> tuple[str | None, str | None]:
        for rule, pattern in self.rules:
            matches = re.findall(pattern, text)
            if matches:
                return matches[-1], rule
        if self.fallback_last_letter:
            matches = re.findall(rf"\b([{self.letters}])\b", text)
            if matches:
                return matches[-1], "last_letter"
        return None, None

    def __call__(self, sample: Sample, text: str) -> Score:
        target = sample.target.strip().upper()
        if len(target) != 1 or target not in self.letters:
            raise ValueError(
                f"target {sample.target!r} of sample {sample.id} is not one of {self.letters}"
            )
        extracted, method = self.extract(text)
        return Score(value=float(extracted == target), extracted=extracted, method=method)

    def describe(self) -> dict[str, Any]:
        desc = {
            "name": self.name,
            "letters": self.letters,
            "rules": self.rules,
            "fallback_last_letter": self.fallback_last_letter,
        }
        desc["fingerprint"] = fingerprint(desc)
        return desc
