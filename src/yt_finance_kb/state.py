from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "videos": {}}


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("schema_version") != 1 or not isinstance(data.get("videos"), dict):
        raise ValueError("Unsupported or invalid state file")
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def schedule_retry(failure_count: int = 0, *, hours: int = 24) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def retry_due(record: dict[str, Any]) -> bool:
    value = record.get("next_retry_at")
    if not value:
        return True
    return datetime.fromisoformat(value) <= datetime.now(UTC)
