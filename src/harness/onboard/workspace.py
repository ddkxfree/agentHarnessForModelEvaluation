"""The candidate workspace: the only place the agent's files are written."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from harness.onboard.spec import CANDIDATE_FILES, OnboardSpec


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WriteRejected(Exception):
    """The agent asked to write outside its candidate files."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path!r}: {reason}")
        self.path = path
        self.reason = reason


@dataclass
class CandidateWorkspace:
    root: Path
    spec: OnboardSpec
    written: dict[str, str] = field(default_factory=dict)  # name -> sha256

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def check(self, path: str) -> str:
        """The candidate file name for ``path``, or WriteRejected with the reason."""
        p = PurePosixPath(path.strip())
        protected = {
            self.spec.description,
            self.spec.scoring_rules,
            self.spec.acceptance,
            self.spec.data,
        }
        if p.is_absolute() or ".." in p.parts or "\\" in path:
            raise WriteRejected(path, "outside the candidate workspace")
        if p.parts and (p.parts[0] in protected or p.name in protected):
            raise WriteRejected(
                path, "protected input (source description, data, rules or acceptance)"
            )
        if str(p) not in CANDIDATE_FILES:
            raise WriteRejected(path, f"not a candidate file; allowed: {sorted(CANDIDATE_FILES)}")
        return str(p)

    def write(self, path: str, content: str) -> str:
        name = self.check(path)
        target = self.root / name
        if target.is_symlink():
            raise WriteRejected(path, "the target is a symlink")
        data = content.encode()
        target.write_bytes(data)
        self.written[name] = sha256(data)
        return name

    def files(self) -> dict[str, str]:
        return {
            name: (self.root / name).read_text()
            for name in CANDIDATE_FILES
            if (self.root / name).is_file()
        }

    def hashes(self) -> dict[str, str]:
        return {name: sha256(text.encode()) for name, text in self.files().items()}
