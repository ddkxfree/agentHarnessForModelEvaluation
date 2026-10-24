from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from harness.core.types import GenerateConfig, Message, ModelOutput, ToolCall, Usage

Tool = dict[str, Any]


class RetryableError(Exception):
    """Raised by a model when the failure is transient and the call may be retried."""


class ModelCallError(Exception):
    """A model call that still failed after retries. The cause is the last underlying error."""


class Model(ABC):
    name: str

    @abstractmethod
    async def generate(
        self, messages: list[Message], config: GenerateConfig, tools: list[Tool] | None = None
    ) -> ModelOutput: ...

    def is_retryable(self, exc: BaseException) -> bool:
        return isinstance(exc, RetryableError)

    @abstractmethod
    def spec(self) -> dict[str, Any]:
        """Identity of the model for manifests and cache keys. Must not contain secrets."""


# provider -> (default base_url, env var holding the API key, fallback key)
PROVIDERS: dict[str, tuple[str | None, str | None, str | None]] = {
    "openai": (None, "OPENAI_API_KEY", None),
    "dashscope": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", None),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY", None),
    "ollama": ("http://localhost:11434/v1", None, "ollama"),
    "vllm": ("http://localhost:8000/v1", None, "EMPTY"),
}


class OpenAICompatibleModel(Model):
    def __init__(
        self,
        model: str,
        *,
        provider: str = "openai",
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        from openai import AsyncOpenAI

        self.name = model
        self.provider = provider
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        # Retries are owned by the runner so every model type behaves the same way.
        self._client = AsyncOpenAI(base_url=self.base_url, api_key=api_key, max_retries=0)

    async def generate(
        self, messages: list[Message], config: GenerateConfig, tools: list[Tool] | None = None
    ) -> ModelOutput:
        kwargs: dict[str, Any] = {
            "model": self.name,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "timeout": config.timeout,
        }
        if config.top_p is not None:
            kwargs["top_p"] = config.top_p
        if config.seed is not None:
            kwargs["seed"] = config.seed
        if config.stop:
            kwargs["stop"] = config.stop
        if tools:
            kwargs["tools"] = tools

        start = time.perf_counter()
        resp = await self._client.chat.completions.create(**kwargs)
        latency = time.perf_counter() - start

        choice = resp.choices[0]
        usage = Usage()
        if resp.usage is not None:
            usage = Usage(
                input_tokens=resp.usage.prompt_tokens or 0,
                output_tokens=resp.usage.completion_tokens or 0,
            )
        tool_calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "")
            for c in (choice.message.tool_calls or [])
            if c.type == "function"
        ]
        return ModelOutput(
            text=choice.message.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            latency_s=latency,
            served_model=resp.model or None,
            system_fingerprint=getattr(resp, "system_fingerprint", None) or None,
        )

    def is_retryable(self, exc: BaseException) -> bool:
        import openai

        return isinstance(
            exc,
            RetryableError
            | openai.RateLimitError
            | openai.APITimeoutError
            | openai.APIConnectionError
            | openai.InternalServerError,
        )

    def spec(self) -> dict[str, Any]:
        return {
            "type": "openai_compatible",
            "provider": self.provider,
            "name": self.name,
            "base_url": self.base_url,
        }


class MockModel(Model):
    """Deterministic in-process model for tests and offline smoke runs."""

    def __init__(
        self,
        respond: Callable[[list[Message]], str | ModelOutput],
        name: str = "mock",
    ) -> None:
        self.name = name
        self._respond = respond
        self.calls = 0

    async def generate(
        self, messages: list[Message], config: GenerateConfig, tools: list[Tool] | None = None
    ) -> ModelOutput:
        self.calls += 1
        out = self._respond(messages)
        if isinstance(out, ModelOutput):
            return out
        return ModelOutput(text=out, finish_reason="stop")

    def spec(self) -> dict[str, Any]:
        return {"type": "mock", "name": self.name}


def _no_model(messages: list[Message]) -> str:
    raise RuntimeError("no model configured (model 'none/none'); use a solver that needs no model")


def get_model(spec: str, *, base_url: str | None = None, api_key: str | None = None) -> Model:
    """Build a model from ``provider/model-name``, e.g. ``dashscope/qwen-plus``.

    The model name may itself contain slashes (``vllm/Qwen/Qwen2.5-7B-Instruct``).
    ``none/none`` is a placeholder for runs whose solver never calls a model, such as
    the oracle solver.
    """
    provider, sep, name = spec.partition("/")
    if not sep or not name:
        raise ValueError(f"Model spec must look like 'provider/model', got {spec!r}")
    if spec == "none/none":
        return MockModel(_no_model, name="none")
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider {provider!r}. Known: {', '.join(PROVIDERS)}")

    default_url, key_env, fallback_key = PROVIDERS[provider]
    if api_key is None:
        api_key = (os.environ.get(key_env) if key_env else None) or fallback_key
    if api_key is None:
        raise ValueError(f"No API key for provider {provider!r}; set {key_env}")
    return OpenAICompatibleModel(
        name, provider=provider, base_url=base_url or default_url, api_key=api_key
    )
