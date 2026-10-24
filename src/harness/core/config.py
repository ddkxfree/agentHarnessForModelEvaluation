from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from harness.core.types import GenerateConfig


class TaskConfig(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)
    generate: dict[str, Any] = Field(default_factory=dict)


class RunConfig(BaseModel):
    model: str
    base_url: str | None = None
    tasks: list[TaskConfig]
    generate: GenerateConfig = Field(default_factory=GenerateConfig)
    concurrency: int = 8
    retries: int = 3
    cache: bool = True
    # auto: reuse cached outputs only at temperature 0; always; never. See runner.use_cache.
    cache_policy: Literal["auto", "always", "never"] = "auto"
    cache_dir: Path = Path(".cache/requests")
    output_dir: Path = Path("runs")

    @classmethod
    def from_yaml(cls, path: Path) -> RunConfig:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    def generate_for(self, task: TaskConfig) -> GenerateConfig:
        return self.generate.model_copy(update=task.generate)
