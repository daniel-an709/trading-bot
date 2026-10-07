"""Append-only JSONL record of every run (logs/runs.jsonl)."""

import dataclasses
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

LOG_FILE = "runs.jsonl"


def _default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"cannot log {type(obj).__name__}")


def append_record(record: dict, log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / LOG_FILE
    with path.open("a") as f:
        f.write(json.dumps(record, default=_default) + "\n")
    return path


def month_cost_usd(log_dir: Path, now: datetime) -> Decimal:
    """Claude spend logged in `now`'s calendar month (UTC). Unreadable lines are skipped."""
    path = log_dir / LOG_FILE
    if not path.exists():
        return Decimal(0)
    month = now.astimezone(UTC).strftime("%Y-%m")
    total = Decimal(0)
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
            if not str(record.get("timestamp_utc", "")).startswith(month):
                continue
            cost = (record.get("agent") or {}).get("cost_usd")
            if cost is not None:
                total += Decimal(str(cost))
        except (ValueError, AttributeError, ArithmeticError):
            continue
    return total
