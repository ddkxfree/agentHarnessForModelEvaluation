"""The onboarding spec: which frozen source to adapt, what the agent may write, and its budget."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from harness.core.fingerprint import fingerprint_dir
from harness.core.types import GenerateConfig

# Files the agent may write in its candidate workspace, and where each would go in the repo.
CANDIDATE_FILES = {
    "adapter.py": "src/harness/tasks/{module}.py",
    "test_adapter.py": "tests/test_{module}.py",
    "config.yaml": "configs/{task}.yaml",
    "NOTES.md": "docs/onboard/{task}.md",
}


class Budget(BaseModel):
    # write-and-validate rounds, the first included
    max_rounds: int = Field(default=3, ge=1)
    # model calls of any kind (planning, writing, after invalid replies)
    max_model_steps: int = Field(default=6, ge=1)
    time_limit_s: float = Field(default=1800.0, gt=0)
    # input + output tokens reported by the server, over all model calls
    token_limit: int | None = Field(default=None, ge=1)
    validation_timeout_s: float = Field(default=120.0, gt=0)


class OnboardSpec(BaseModel):
    task: str
    # The frozen source package; ``source_sha256`` pins its content (``fingerprint_dir``).
    source: Path
    source_sha256: str
    description: str = "README.md"
    scoring_rules: str = "SCORING.md"
    data: str = "data"
    # Independent positive/negative outputs. Only the validator reads them; the agent never
    # sees or writes them.
    acceptance: str = "acceptance.jsonl"
    # Top-level modules the adapter may import.
    allowed_imports: list[str] = Field(
        default_factory=lambda: [
            "__future__",
            "csv",
            "json",
            "math",
            "pathlib",
            "re",
            "string",
            "typing",
            "unicodedata",
            "harness",
        ]
    )
    model: str | None = None
    generate: GenerateConfig = Field(
        default_factory=lambda: GenerateConfig(temperature=0.0, max_tokens=4096)
    )
    budget: Budget = Field(default_factory=Budget)
    output_dir: Path = Path("runs/onboard")

    @field_validator("task")
    @classmethod
    def _task_name(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", v):
            raise ValueError("task names are lower-case words joined by '-'")
        return v

    @property
    def module(self) -> str:
        return self.task.replace("-", "_")

    def path(self, name: str) -> Path:
        return self.source / name

    def protected(self) -> list[Path]:
        """The inputs the agent must not change: description, data, rules and acceptance."""
        return [self.path(n) for n in (self.description, self.data, self.scoring_rules)] + [
            self.path(self.acceptance)
        ]

    def identity(self) -> dict[str, Any]:
        """What decides an attempt, apart from the model's replies."""
        return self.model_dump(mode="json", exclude={"output_dir"})


def load_spec(path: Path, **overrides: Any) -> OnboardSpec:
    """Read a spec; a relative ``source`` is taken from the current directory, like the paths
    in other configs."""
    raw = yaml.safe_load(Path(path).read_text())
    raw.update({k: v for k, v in overrides.items() if v is not None})
    return OnboardSpec(**raw)


def source_fingerprint(spec: OnboardSpec) -> str:
    return fingerprint_dir(spec.source)


def data_fingerprint(data_dir: Path) -> str:
    return fingerprint_dir(data_dir)
