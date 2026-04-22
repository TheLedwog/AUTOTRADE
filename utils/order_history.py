"""Persistent local storage for executed and cancelled trade history."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any


class OrderHistoryStore:
    """Append and retrieve compact order-history records from JSONL storage."""

    def __init__(self, path: Path, max_entries: int = 500) -> None:
        """Store the history path and limit the number of retained entries."""
        self.path = Path(path)
        self.max_entries = max_entries
        self._lock = RLock()

    def append(self, entry: dict[str, Any]) -> None:
        """Persist one order-history entry and trim old records."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            entries = self.recent(limit=self.max_entries)
            entries.append(entry)
            entries = entries[-self.max_entries :]
            with self.path.open("w", encoding="utf-8") as handle:
                for item in entries:
                    handle.write(json.dumps(item, ensure_ascii=True) + "\n")

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent order-history entries, oldest to newest."""
        if limit <= 0 or not self.path.exists():
            return []

        with self._lock:
            with self.path.open("r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()

        records: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
        return records[-limit:]
