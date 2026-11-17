"""Structured log: one JSON object per line. Used for capture events now, decisions later."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventLog:
    """Thread-safe JSON-lines writer. With path=None events are kept in memory only."""

    def __init__(self, path: str | Path | None = None, keep_in_memory: int = 1000) -> None:
        self._lock = threading.Lock()
        self._fh = None
        self._recent: list[dict[str, Any]] = []
        self._keep = keep_in_memory
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", buffering=1, encoding="utf-8")
        self.log("log_open", wall_time=datetime.now(UTC).isoformat())

    def log(self, kind: str, **fields: Any) -> None:
        record = {"t_ns": time.monotonic_ns(), "kind": kind, **fields}
        with self._lock:
            self._recent.append(record)
            if len(self._recent) > self._keep:
                del self._recent[: len(self._recent) - self._keep]
            if self._fh:
                self._fh.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")

    def recent(self, kind: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [r for r in self._recent if kind is None or r["kind"] == kind]

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None
