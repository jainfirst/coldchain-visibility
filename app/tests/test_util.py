"""Tests for the shared helpers: durations, coercion, JSON sanitisation, tz."""
from datetime import datetime, timezone

from app.zoomlogi.util import (as_utc, fmt_duration, jsonable, safe_float,
                               utc_now_iso)


def test_fmt_duration_basic():
    assert fmt_duration(0) == "0m"
    assert fmt_duration(45) == "45m"
    assert fmt_duration(60) == "1h 00m"
    assert fmt_duration(125) == "2h 05m"
    assert fmt_duration(899.8) == "15h 00m"      # rounds to 900 min -> 15h


def test_fmt_duration_handles_bad_input():
    assert fmt_duration(None) == "—"
    assert fmt_duration(float("nan")) == "—"
    assert fmt_duration(float("inf")) == "—"
    assert fmt_duration(-5) == "—"


def test_safe_float():
    assert safe_float("3.5") == 3.5
    assert safe_float(7) == 7.0
    assert safe_float(None) is None
    assert safe_float("not-a-number") is None
    assert safe_float(float("nan")) is None
    assert safe_float(float("inf")) is None


def test_jsonable_scrubs_nan_and_inf():
    out = jsonable({"a": float("nan"), "b": [1.0, float("inf")], "c": {"d": 2.0}})
    assert out["a"] is None
    assert out["b"] == [1.0, None]
    assert out["c"]["d"] == 2.0           # finite values pass through untouched


def test_as_utc_normalises_naive_to_aware():
    aware = as_utc(datetime(2025, 1, 1, 12, 0))
    assert aware.tzinfo is not None
    already = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert as_utc(already) == already     # idempotent on aware input


def test_utc_now_iso_is_z_suffixed():
    assert utc_now_iso().endswith("Z")
