import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal as D

from bot.journal import append_record, month_cost_usd

NOW = datetime(2026, 10, 7, 14, 0, tzinfo=UTC)


@dataclass
class Thing:
    price: D
    day: date


def lines(log_dir):
    return (log_dir / "runs.jsonl").read_text().splitlines()


def test_two_appends_two_lines(tmp_path):
    append_record({"run_id": "a"}, tmp_path / "logs")
    path = append_record({"run_id": "b"}, tmp_path / "logs")
    assert path == tmp_path / "logs/runs.jsonl"
    assert [json.loads(line)["run_id"] for line in lines(tmp_path / "logs")] == ["a", "b"]


def test_decimal_dates_and_dataclasses_serialised(tmp_path):
    append_record({"price": D("399.85"), "at": NOW, "thing": Thing(D("1.10"), date(2026, 10, 7))}, tmp_path)
    record = json.loads(lines(tmp_path)[0])
    assert record == {"price": "399.85", "at": "2026-10-07T14:00:00+00:00",
                      "thing": {"price": "1.10", "day": "2026-10-07"}}


def record(ts: str, cost):
    return {"timestamp_utc": ts, "agent": {"cost_usd": cost}}


def test_month_cost_sums_only_this_month(tmp_path):
    for r in [record("2026-10-01T14:00:00+00:00", 0.5), record("2026-10-06T14:00:00+00:00", 0.25),
              record("2026-09-30T14:00:00+00:00", 9.0), record("2026-10-02T14:00:00+00:00", None),
              {"timestamp_utc": "2026-10-03T14:00:00+00:00"}]:
        append_record(r, tmp_path)
    assert month_cost_usd(tmp_path, NOW) == D("0.75")


def test_month_cost_skips_corrupt_line(tmp_path):
    append_record(record("2026-10-01T14:00:00+00:00", 0.5), tmp_path)
    with (tmp_path / "runs.jsonl").open("a") as f:
        f.write("{not json\n")
    append_record(record("2026-10-02T14:00:00+00:00", 0.5), tmp_path)
    assert month_cost_usd(tmp_path, NOW) == D("1.0")


def test_missing_log_dir_is_zero(tmp_path):
    assert month_cost_usd(tmp_path / "nope", NOW) == 0
