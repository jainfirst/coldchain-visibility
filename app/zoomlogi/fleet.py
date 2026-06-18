"""
Fleet command center: a portfolio-level operations view over the historical
shipment book.  Where the live dashboard answers "is THIS shipment OK?", this
answers Dana's real question — "across everything, what should I be paying
attention to?" — with KPIs, a triaged alert queue, and the patterns behind it.

Reads the 90-day CSV and computes everything on the fly so it re-runs as the
book grows.  No live token needed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .util import fmt_duration

# CSV lives at the repo root; fall back to a glob so a rename doesn't break it.
_ROOT = Path(__file__).resolve().parent.parent.parent
_CSV = _ROOT / "mercyhealth_shipments_90d - mercyhealth_shipments_90d.csv.csv"

# Columns the fleet view relies on; validated on load for a clear failure message
# (a renamed column otherwise surfaces as a cryptic AttributeError deep in pandas).
_REQUIRED_COLS = (
    "shipment_id", "carrier", "temp_class", "service_level",
    "origin_dc", "destination_city", "destination_state", "destination_region",
    "ship_date", "planned_delivery", "actual_delivery",
    "excursion_minutes", "min_temp_c", "recipient_issue", "issue_type",
)


def _csv_path() -> Path:
    if _CSV.exists():
        return _CSV
    hits = sorted(_ROOT.glob("*shipments*.csv"))
    if not hits:
        raise FileNotFoundError("Historical shipments CSV not found at repo root.")
    return hits[0]


def csv_available() -> bool:
    """Whether the shipment book CSV can be located (for the health probe)."""
    try:
        return _csv_path().exists()
    except FileNotFoundError:
        return False


# --- loss model (shared assumptions with the Part-2 notebook) --------------
UNIT_VALUE = 2000.0                       # $/shipment, placeholder (confirm w/ Dana)
P_WRITEOFF = {"minor": 0.10, "moder": 0.30, "severe": 0.70}

# Triage thresholds (minutes) — named so they're not magic numbers downstream.
CRITICAL_EXC_MIN = 240                     # > 4h out of temp -> critical
LATE_WATCH_MIN = 180                       # > 3h late -> watch


def _tier(excursion_min: float) -> str:
    if excursion_min < 60:
        return "minor"
    return "moder" if excursion_min <= CRITICAL_EXC_MIN else "severe"


def _load() -> pd.DataFrame:
    df = pd.read_csv(_csv_path())
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Shipment CSV is missing required column(s): {', '.join(missing)}")
    df["planned"] = pd.to_datetime(df.planned_delivery, errors="coerce")
    df["actual"] = pd.to_datetime(df.actual_delivery, errors="coerce")
    df["late_min"] = (df.actual - df.planned).dt.total_seconds() / 60
    # Coerce numerics so a blank/garbage cell can't crash int()/tiering or be
    # silently mis-classified (NaN passes neither < nor <= tests).
    df["excursion_minutes"] = pd.to_numeric(df.excursion_minutes, errors="coerce").fillna(0.0)
    df["min_temp_c"] = pd.to_numeric(df.min_temp_c, errors="coerce")
    df["issue_type"] = df.issue_type.fillna("")
    df["has_exc"] = df.excursion_minutes > 0
    df["issue"] = df.recipient_issue.eq("Y")
    df["in_transit"] = df.actual.isna()           # still moving (no actual delivery)
    df["froze"] = (df.temp_class == "2-8C") & (df.min_temp_c < 0)
    return df


def _severity(r) -> str | None:
    """Triage one shipment. None = nothing worth surfacing."""
    if r.has_exc and r.excursion_minutes > CRITICAL_EXC_MIN:
        return "critical"                          # >4h out of temp
    if r.issue and r.issue_type in ("temp_concern", "damaged_packaging"):
        return "critical"
    if r.froze:
        return "critical"                          # refrigerated product froze
    if r.has_exc or r.issue:
        return "at_risk"
    if pd.notna(r.late_min) and r.late_min > LATE_WATCH_MIN:
        return "watch"                             # >3h late, cold-chain exposure
    return None


def _reasons(r) -> list[str]:
    out: list[str] = []
    if r.has_exc:
        out.append(f"{fmt_duration(r.excursion_minutes)} out of temp")
    if r.froze:
        out.append(f"froze to {r.min_temp_c}°C")
    if r.issue:
        out.append(f"pharmacy flagged: {str(r.issue_type).replace('_', ' ')}")
    if pd.notna(r.late_min) and r.late_min > 180:
        out.append(f"{fmt_duration(r.late_min)} late")
    return out


def _action(r, sev: str) -> str:
    if r.froze:
        return "Quarantine — refrigerated product froze (likely over-icing); QA review + check packaging spec"
    if r.has_exc and r.temp_class == "Frozen" and r.carrier == "OnTrac":
        return "Quarantine on arrival; reroute frozen off OnTrac (CA/WA lanes)"
    if r.has_exc and r.excursion_minutes > CRITICAL_EXC_MIN:
        return "Quarantine on arrival — hold from patient release pending QA stability review"
    if r.has_exc:
        return "Flag for QA review against the product's stability spec before release"
    if r.issue == True and r.issue_type == "temp_concern":  # noqa: E712
        return "Confirm sensor trace, treat as excursion; QA review"
    if r.issue:
        return f"Follow up on {str(r.issue_type).replace('_', ' ')} with recipient/carrier"
    return "Monitor; expedite if the cold-chain window is at risk"


def _empty_view() -> dict[str, Any]:
    """A valid, zeroed payload for an empty shipment book (keeps the API contract)."""
    return {
        "kpis": {"shipments": 0, "excursion_rate": 0.0, "on_time_rate": 0.0,
                 "issue_rate": 0.0, "excursion_hours": 0, "annual_exposure_usd": 0,
                 "window_days": 0},
        "alert_counts": {"critical": 0, "at_risk": 0, "watch": 0},
        "alerts": [], "by_carrier": [], "by_temp_class": [], "by_region": [],
        "exception_types": {}, "worst_lanes": [],
        "assumptions": {"unit_value_usd": UNIT_VALUE, "p_writeoff": P_WRITEOFF},
        "loss_model": {"unit_value_usd": UNIT_VALUE, "window_days": 0, "tiers": []},
    }


def build_fleet_view() -> dict[str, Any]:
    df = _load()
    n = len(df)
    if n == 0:
        return _empty_view()
    done = df[~df.in_transit]                       # delivered (have an actual time)
    exc = df[df.has_exc]

    # expected write-offs (severity-weighted) and $ exposure, annualized
    exc_loss = exc.excursion_minutes.apply(lambda m: P_WRITEOFF[_tier(m)]).sum()
    ship_dates = pd.to_datetime(df.ship_date, errors="coerce").dropna()
    span_days = (max(int((ship_dates.max() - ship_dates.min()).days), 1)
                 if len(ship_dates) else 1)
    annualize = 365 / span_days

    kpis = {
        "shipments": n,
        "excursion_rate": round(df.has_exc.mean() * 100, 1),
        "on_time_rate": round((done.late_min <= 0).mean() * 100, 1) if len(done) else 0.0,
        "issue_rate": round(df.issue.mean() * 100, 1),
        "excursion_hours": round(df.excursion_minutes.sum() / 60),
        "annual_exposure_usd": round(exc_loss * UNIT_VALUE * annualize),
        "window_days": int(span_days),
    }

    # --- triaged alert queue ---
    alerts: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        sev = _severity(r)
        if sev is None:
            continue
        loss = (P_WRITEOFF[_tier(r.excursion_minutes)] * UNIT_VALUE) if r.has_exc else 0
        alerts.append({
            "id": r.shipment_id,
            "severity": sev,
            "carrier": r.carrier,
            "temp_class": r.temp_class,
            "lane": f"{r.origin_dc} → {r.destination_city}, {r.destination_state}",
            "service": r.service_level,
            "reasons": _reasons(r),
            "action": _action(r, sev),
            "excursion_hms": fmt_duration(r.excursion_minutes) if r.has_exc else None,
            "excursion_minutes": int(r.excursion_minutes),
            "exposure_usd": round(loss),
        })
    sev_order = {"critical": 0, "at_risk": 1, "watch": 2}
    alerts.sort(key=lambda a: (sev_order[a["severity"]], -a["excursion_minutes"]))

    counts = {
        "critical": sum(a["severity"] == "critical" for a in alerts),
        "at_risk": sum(a["severity"] == "at_risk" for a in alerts),
        "watch": sum(a["severity"] == "watch" for a in alerts),
    }

    # --- breakdowns (the patterns) ---
    def by(col):
        g = df.groupby(col).agg(n=("has_exc", "size"), rate=("has_exc", "mean"))
        return [{"key": str(k), "n": int(r.n), "rate": round(r.rate * 100, 1)}
                for k, r in g.sort_values("rate", ascending=False).iterrows()]

    exception_types = {
        "temperature excursion": int(df.has_exc.sum()),
        "refrigerated froze": int(df.froze.sum()),
        ">3h late": int((df.late_min > LATE_WATCH_MIN).sum()),
        "pharmacy complaint": int(df.issue.sum()),
    }

    # worst carrier × temp_class cells
    cells = (df.groupby(["carrier", "temp_class"])
             .agg(n=("has_exc", "size"), exc_n=("has_exc", "sum"),
                  rate=("has_exc", "mean"),
                  avg_min=("excursion_minutes", "mean")).reset_index())
    cells = cells[cells.n >= 5].sort_values("rate", ascending=False).head(5)
    worst_lanes = [{"carrier": str(c.carrier), "temp_class": str(c.temp_class),
                    "label": f"{c.carrier} · {c.temp_class}", "n": int(c.n),
                    "exc_n": int(c.exc_n), "rate": round(c.rate * 100),
                    "avg_hms": fmt_duration(c.avg_min)}
                   for c in cells.itertuples()]

    return {
        "kpis": kpis,
        "alert_counts": counts,
        "alerts": alerts,
        "by_carrier": by("carrier"),
        "by_temp_class": by("temp_class"),
        "by_region": by("destination_region"),
        "exception_types": exception_types,
        "worst_lanes": worst_lanes,
        "assumptions": {"unit_value_usd": UNIT_VALUE, "p_writeoff": P_WRITEOFF},
        "loss_model": {
            "unit_value_usd": UNIT_VALUE,
            "window_days": int(span_days),
            "tiers": [
                {"name": "minor", "range": "< 1h out of temp", "p": P_WRITEOFF["minor"]},
                {"name": "moderate", "range": "1–4h out of temp", "p": P_WRITEOFF["moder"]},
                {"name": "severe", "range": "> 4h out of temp", "p": P_WRITEOFF["severe"]},
            ],
        },
    }
