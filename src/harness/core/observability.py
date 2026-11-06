"""Durable per-attempt evidence, isolated from the model's inputs and cache keys."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import uuid4

call_context: ContextVar[dict[str, str] | None] = ContextVar("harness_call_context", default=None)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def safe_data(value: Any) -> Any:
    secrets = [
        v
        for k, v in os.environ.items()
        if len(v) >= 8 and re.search(r"(?:KEY|TOKEN|SECRET|PASSWORD)$", k)
    ]

    def clean(item: Any) -> Any:
        if isinstance(item, dict):
            return {
                str(k): "[REDACTED]"
                if str(k).lower()
                in {"authorization", "api_key", "password", "access_token", "secret"}
                else clean(v)
                for k, v in item.items()
            }
        if isinstance(item, (list, tuple)):
            return [clean(v) for v in item]
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, "[REDACTED]")
            return re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", item)
        return item

    return clean(value)


class Journal:
    def __init__(self, root: Path | None, identity: dict[str, Any]) -> None:
        self.root = root
        self.identity = identity
        self.sequence = 0
        self.lock = threading.RLock()
        if root is not None:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def event(self, kind: str, **data: Any) -> None:
        from datetime import UTC, datetime

        with self.lock:
            self.sequence += 1
            row = safe_data(
                {
                    **self.identity,
                    "sequence": self.sequence,
                    "at": datetime.now(UTC).isoformat(),
                    "kind": kind,
                    **data,
                }
            )
            if self.root is not None:
                with (self.root / "events.jsonl").open("a") as f:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                    os.fsync(f.fileno())

    def checkpoint(self, data: dict[str, Any]) -> None:
        if self.root is None:
            return
        with self.lock:
            fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(
                        safe_data({**self.identity, **data}), f, ensure_ascii=False, default=str
                    )
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.root / "checkpoint.json")
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
