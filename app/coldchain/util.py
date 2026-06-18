"""
Small shared helpers used across the service layer: timezone-safe "now",
human-readable durations, safe numeric coercion, and JSON sanitisation.

Kept dependency-light on purpose so every module can import it without cycles.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now_iso() -> str:
    """Current UTC time as ISO-8601 with a trailing 'Z' (tz-aware, not utcnow())."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def as_utc(dt: datetime) -> datetime:
    """Make a datetime timezone-aware, assuming UTC if it is naive.

    Mixed naive/aware datetimes can't be compared or sorted (TypeError), so we
    normalise every parsed timestamp through here before it enters a series.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def fmt_duration(minutes: float | None) -> str:
    """Bizops-readable duration: 899.8 -> '15h 00m', 45 -> '45m', None/NaN -> '—'."""
    if minutes is None or (isinstance(minutes, float) and not math.isfinite(minutes)):
        return "—"
    m = int(round(minutes))
    if m < 0:
        return "—"
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h {m % 60:02d}m"


def safe_float(x: Any) -> Optional[float]:
    """Coerce to float, returning None for None / NaN / Inf / non-numeric."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def jsonable(obj: Any) -> Any:
    """Recursively replace NaN/Inf floats with None so the payload is valid JSON.

    json.dumps emits bare NaN/Infinity tokens, which are invalid JSON and make
    the browser's JSON.parse throw. We scrub them once, at the API boundary.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj
