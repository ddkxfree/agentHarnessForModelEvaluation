from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from harness.core.fingerprint import fingerprint
from harness.core.types import Message, ModelOutput, Sample, SolveResult

if TYPE_CHECKING:
    from harness.sandbox.base import Sandbox


class Generate(Protocol):
    async def __call__(
        self, messages: list[Message], tools: list[dict[str, Any]] | None = None
    ) -> ModelOutput: ...


class Solver(Protocol):
    """Decides how a model answers a sample.

    A solver receives a ``generate`` function (already wrapped with caching, retries
    and timeouts by the runner) so that multi-step solvers such as agent loops can
    call the model as many times as they need. Tasks that declare a sandbox also
    pass the sample's sandbox.
    """

    name: str

    async def __call__(
        self, sample: Sample, generate: Generate, sandbox: Sandbox | None = None
    ) -> SolveResult: ...

    def describe(self) -> dict[str, Any]: ...

    # Optional: ``protocol() -> dict`` states how the solver talks to the model (interface,
    # tools, how it finishes, what one step is, what it needs from the model and task). It
    # goes into the manifest next to ``describe()`` so solver comparisons can say what differed.


class PromptSolver:
    """Single-turn solver: optional system prompt, few-shot turns, then the question."""

    name = "prompt"

    def __init__(
        self,
        user_template: Callable[[Sample], str],
        *,
        template_text: str,
        assistant_template: Callable[[Sample], str] | None = None,
        system: str | None = None,
        fewshot: Callable[[Sample], list[Sample]] | None = None,
        fewshot_desc: dict[str, Any] | None = None,
    ) -> None:
        self._user = user_template
        self._assistant = assistant_template
        self._system = system
        self._fewshot = fewshot
        self._template_text = template_text
        self._fewshot_desc = fewshot_desc or {}

    def build_messages(self, sample: Sample) -> list[Message]:
        messages: list[Message] = []
        if self._system:
            messages.append({"role": "system", "content": self._system})
        if self._fewshot is not None:
            if self._assistant is None:
                raise ValueError("few-shot prompting requires an assistant_template")
            for shot in self._fewshot(sample):
                messages.append({"role": "user", "content": self._user(shot)})
                messages.append({"role": "assistant", "content": self._assistant(shot)})
        messages.append({"role": "user", "content": self._user(sample)})
        return messages

    async def __call__(
        self, sample: Sample, generate: Generate, sandbox: Sandbox | None = None
    ) -> SolveResult:
        messages = self.build_messages(sample)
        return SolveResult(messages=messages, output=await generate(messages))

    def describe(self) -> dict[str, Any]:
        desc = {
            "name": self.name,
            "system": self._system,
            "user_template": self._template_text,
            "fewshot": self._fewshot_desc,
        }
        desc["fingerprint"] = fingerprint(desc)
        return desc

    def protocol(self) -> dict[str, Any]:
        return {
            "interface": "single_turn",
            "tools": [],
            "finish": "the single reply is the answer",
            "step": "one model call",
            "requires": [],
        }
