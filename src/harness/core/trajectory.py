"""Per-sample record that solvers fill in as they go, so a failure keeps what already happened.

The runner activates one ``Trajectory`` per sample. Solvers append to ``current_trajectory()``
instead of keeping private lists; if the solver then raises or is cancelled by the sample time
limit, the runner still saves the messages and steps recorded up to that point.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from harness.core.observability import Journal
from harness.core.types import Artifact, Message, Step

_current: ContextVar[Trajectory | None] = ContextVar("harness_trajectory", default=None)


class Trajectory:
    def __init__(self, root: Path | None = None, subdir: str = "") -> None:
        self.messages: list[Message] = []
        self.steps: list[Step] = []
        self.artifacts: list[Artifact] = []
        self._root = root
        self._subdir = subdir
        self.journal: Journal | None = None
        self.snapshot: Callable[[], dict[str, Any]] | None = None

    def checkpoint(self) -> None:
        if self.journal and self.snapshot:
            data = self.snapshot()
            if self.messages:
                data["messages"] = self.messages
            if self.steps:
                data["steps"] = [s.model_dump(mode="json") for s in self.steps]
            data["artifacts"] = [a.model_dump(mode="json") for a in self.artifacts]
            self.journal.checkpoint(data)

    def event(self, kind: str, **data: Any) -> None:
        if self.journal:
            self.journal.event(kind, **data)
        self.checkpoint()

    def save_artifact(self, name: str, content: str | bytes) -> Artifact:
        """Store ``content`` under ``name`` (unique per sample) and record its hash."""
        if any(a.name == name for a in self.artifacts):
            raise ValueError(f"artifact {name!r} already exists")
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("artifact name must stay inside the sample directory")
        data = content.encode() if isinstance(content, str) else content
        path = None
        if self._root is not None:
            path = f"{self._subdir}/{name}" if self._subdir else name
            target = self._root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        artifact = Artifact(
            name=name, sha256=hashlib.sha256(data).hexdigest(), size=len(data), path=path
        )
        self.artifacts.append(artifact)
        self.event("artifact_saved", artifact=artifact.model_dump(mode="json"))
        return artifact


def current_trajectory() -> Trajectory:
    """The active sample's trajectory, or a detached one when a solver runs outside the runner."""
    return _current.get() or Trajectory()


@contextmanager
def recording(trajectory: Trajectory) -> Iterator[Trajectory]:
    token = _current.set(trajectory)
    try:
        yield trajectory
    finally:
        _current.reset(token)
