from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .utils import ensure_dir


class JsonCache:
    def __init__(self, root: Path, ttl_hours: float = 6) -> None:
        self.root = root
        self.ttl = timedelta(hours=ttl_hours)
        ensure_dir(root)

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text())
        saved_at = datetime.fromisoformat(payload["saved_at"])
        if datetime.now(timezone.utc) - saved_at > self.ttl:
            return None
        return payload["data"]

    def get_allow_stale(self, key: str) -> Any | None:
        """Return cached data even if stale, or None when missing entirely."""
        path = self._path(key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text())
        return payload.get("data")

    def set(self, key: str, data: Any) -> None:
        path = self._path(key)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
