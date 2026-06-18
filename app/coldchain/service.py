"""
Orchestration: fetch FedEx + Tive, normalise both, run excursion detection,
and assemble a single shipment view for the dashboard.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from dateutil import parser as dtparse

from . import config
from .clients import fedex_client, tive_client
from .excursions import ExcursionReport, Reading, detect_excursions
from .util import as_utc, fmt_duration, utc_now_iso


# --------------------------------------------------------------------------
# FedEx normalisation
# --------------------------------------------------------------------------
def normalize_fedex(raw: dict[str, Any]) -> dict[str, Any]:
    """Pull the fields Dana cares about out of FedEx's deeply nested payload."""
    try:
        result = raw["output"]["completeTrackResults"][0]["trackResults"][0]
    except (KeyError, IndexError):
        return {"available": False, "error": "No track results returned."}

    dt = {d.get("type"): d.get("dateTime") for d in result.get("dateAndTimes", [])}
    status = result.get("latestStatusDetail", {})
    shipper = result.get("shipperInformation", {}).get("address", {})
    recipient = result.get("recipientInformation", {}).get("address", {})
    service = result.get("serviceDetail", {})

    events = []
    for e in result.get("scanEvents", []):
        events.append({
            "ts": e.get("date"),
            "type": e.get("eventType"),
            "description": e.get("eventDescription"),
            "city": e.get("scanLocation", {}).get("city"),
            "state": e.get("scanLocation", {}).get("stateOrProvinceCode"),
        })
    events.sort(key=lambda e: e.get("ts") or "", reverse=True)

    return {
        "available": True,
        "tracking_number": result.get("trackingNumberInfo", {}).get("trackingNumber"),
        "carrier_code": result.get("trackingNumberInfo", {}).get("carrierCode"),
        "status": status.get("description") or status.get("statusByLocale"),
        "status_code": status.get("code"),
        "service": service.get("description"),
        "origin": _fmt_place(shipper),
        "destination": _fmt_place(recipient),
        "shipped_at": dt.get("SHIP"),
        "picked_up_at": dt.get("ACTUAL_PICKUP"),
        "delivered_at": dt.get("ACTUAL_DELIVERY"),
        "estimated_delivery": dt.get("ESTIMATED_DELIVERY"),
        "events": events,
    }


def _fmt_place(addr: dict[str, Any]) -> Optional[str]:
    city = (addr.get("city") or "").title()
    state = addr.get("stateOrProvinceCode") or ""
    place = ", ".join(p for p in (city, state) if p)
    return place or None


# --------------------------------------------------------------------------
# Tive normalisation
# --------------------------------------------------------------------------
# Tive's trackerData schema is read tolerantly: we scan each record for the
# first key that looks like a timestamp and the first that looks like a
# temperature.  When we see the real payload we lock these down, but this keeps
# the dashboard working across minor schema variations.
_TS_KEYS = ("measurementTime", "timestamp", "eventTime", "dateTime", "recordedAt",
            "time", "ts", "deviceTimeUtc", "utcTimestamp", "occurredAt",
            "processTime")
_TEMP_KEYS = ("temperature", "temperatureCelsius", "tempC", "temp_c", "temp",
              "celsius", "ambientTemperature")


def _find_records(raw: Any) -> list[dict]:
    """Locate the list of reading records inside an arbitrary Tive payload."""
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        # common envelope keys, else first list-of-dicts value we find
        for key in ("trackerData", "data", "readings", "records", "items",
                    "results", "sensorData"):
            v = raw.get(key)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
        for v in raw.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


def _extract(record: dict, keys: tuple[str, ...]) -> Any:
    lower = {k.lower(): v for k, v in record.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] is not None:
            return lower[k.lower()]
    return None


def _to_celsius(value: float) -> float:
    if config.TIVE_TEMP_UNIT == "F":
        return (value - 32.0) * 5.0 / 9.0
    return value


def _extract_coords(rec: dict) -> tuple[Optional[float], Optional[float]]:
    """Pull lat/lng from a Tive record (nested under 'coordinates', or flat)."""
    coord = rec.get("coordinates") or rec.get("location") or {}
    if isinstance(coord, dict):
        lat = coord.get("latitude", coord.get("lat"))
        lng = coord.get("longitude", coord.get("lng", coord.get("lon")))
    else:
        lat = lng = None
    if lat is None:  # flat fallback
        lat, lng = rec.get("latitude"), rec.get("longitude")
    try:
        return (float(lat), float(lng)) if lat is not None and lng is not None else (None, None)
    except (ValueError, TypeError):
        return (None, None)


def parse_tive_readings(raw: Any) -> list[Reading]:
    readings: list[Reading] = []
    for rec in _find_records(raw):
        ts_raw = _extract(rec, _TS_KEYS)
        temp_raw = _extract(rec, _TEMP_KEYS)
        if ts_raw is None:
            continue
        try:
            ts = as_utc(dtparse.parse(str(ts_raw)))   # naive -> UTC, so series sort safely
        except (ValueError, OverflowError, TypeError):
            continue
        temp = None
        if temp_raw is not None:
            try:
                temp = round(_to_celsius(float(temp_raw)), 2)
            except (ValueError, TypeError):
                temp = None
        lat, lng = _extract_coords(rec)
        place = rec.get("location") if isinstance(rec.get("location"), str) else None
        readings.append(Reading(ts=ts, temp_c=temp, lat=lat, lng=lng, place=place))
    readings.sort(key=lambda r: r.ts)
    return readings


# --------------------------------------------------------------------------
# Top-level assembly
# --------------------------------------------------------------------------
def build_shipment_view(
    tracking_number: str | None = None,
    temp_class: str | None = None,
    transit_only: bool = False,
) -> dict[str, Any]:
    tracking_number = tracking_number or config.DEFAULT_TRACKING_NUMBER
    # Validate the class against the policy table; unknown -> documented default.
    temp_class = temp_class if temp_class in config.TEMP_BANDS else config.DEFAULT_TEMP_CLASS
    band = config.TEMP_BANDS[temp_class]

    view: dict[str, Any] = {
        "tracking_number": tracking_number,
        "temp_class": temp_class,
        "band": {"lo": band.lo, "hi": band.hi, "rationale": band.rationale},
        "generated_at": utc_now_iso(),
        "transit_only": transit_only,
    }

    # --- FedEx (live, via the shared client so the token cache persists) ---
    try:
        view["fedex"] = normalize_fedex(fedex_client.track(tracking_number))
    except Exception as exc:  # surface, don't crash the dashboard
        view["fedex"] = {"available": False, "error": str(exc)}

    # --- Tive: live first, then fall back to the last cached snapshot ---
    tive_raw, source, captured_at, err = None, None, None, None
    try:
        tive_raw, source = tive_client.tracker_data(), "live"
    except Exception as exc:  # auth lapsed / API down -> try the snapshot
        err = str(exc)
        snap = tive_client.snapshot()
        if snap is not None:
            tive_raw, captured_at, source = snap[0], snap[1], "snapshot"

    if tive_raw is None:
        # No live token and no snapshot — honest "no data", never fabricated.
        view["tive"] = {"available": False, "source": "none", "error": err}
        view["excursion"] = {"status": "no_data", "severity": "no_data"}
        view["transit_window"] = None
        return view

    readings = parse_tive_readings(tive_raw)

    # Optionally clip to the FedEx pickup->delivery window. The logger keeps
    # recording ~30h past delivery; clipping isolates IN-TRANSIT risk from
    # post-delivery dock time. Off by default so the full trace stays the baseline.
    window = _transit_window(view["fedex"]) if transit_only else None
    if window is not None:
        readings = [r for r in readings if window[0] <= r.ts <= window[1]]
    view["transit_window"] = (
        {"start": window[0].isoformat(), "end": window[1].isoformat()}
        if window is not None else None
    )

    view["tive"] = {
        "available": True,
        "source": source,                           # "live" or "snapshot"
        "captured_at": captured_at,                 # set only for snapshots
        "stale_reason": err if source == "snapshot" else None,
        "series": [
            {"ts": r.ts.isoformat(), "temp_c": r.temp_c,
             "lat": r.lat, "lng": r.lng, "place": r.place}
            for r in readings
        ],
    }
    view["excursion"] = _report_to_dict(detect_excursions(readings, band))
    return view


def _transit_window(fedex: dict[str, Any]) -> Optional[tuple[datetime, datetime]]:
    """The (pickup, delivery) instants from a normalised FedEx view, or None."""
    if not fedex.get("available"):
        return None
    pu, dl = fedex.get("picked_up_at"), fedex.get("delivered_at")
    try:
        start = as_utc(dtparse.parse(pu)) if pu else None
        end = as_utc(dtparse.parse(dl)) if dl else None
    except (ValueError, TypeError, OverflowError):
        return None
    return (start, end) if start and end and start < end else None


def _ops_guidance(r: ExcursionReport) -> dict[str, Any]:
    """Translate the technical verdict into an outcome + action for the team.

    This is the business layer: the excursion engine reports facts; here we say
    what it MEANS and what ops should DO.  Action language is intentionally
    conservative for a patient-bound pharma shipment.
    """
    dur = fmt_duration(r.total_excursion_minutes)
    budget = fmt_duration(r.alert_budget_min)
    drivers: list[str] = []
    if r.breaches:
        worst = max(r.breaches, key=lambda b: b.duration_min)
        extreme = (f"hottest point {worst.peak_temp_c}°C" if worst.direction == "high"
                   else f"coldest point {worst.peak_temp_c}°C")
        drivers.append(
            f"Out of {r.temp_class} range for {dur} total — {extreme}.")
    if r.alert_budget_min:
        drivers.append(
            f"{dur} of the {budget} stability budget used "
            f"({int(r.budget_used_pct or 0)}%).")
    for s in r.spikes:
        drivers.append(
            f"Sudden {abs(s.delta_c)}°C swing in {int(s.over_min)} min "
            f"({s.from_c}→{s.to_c}°C) — possible physical event.")
    if r.dropouts:
        drivers.append(
            f"{len(r.dropouts)} sensor dropout(s); "
            f"{fmt_duration(r.dropout_minutes)} with no reading.")

    if r.severity == "ok":
        return {"label": "In temp", "duration_h": dur, "drivers": drivers or
                ["Stayed inside the allowed band the entire trip."],
                "why": "Cold chain held. Product is within spec.",
                "action": "No action — clear for normal release."}
    if r.severity == "watch":
        return {"label": "Watch", "duration_h": dur, "drivers": drivers,
                "why": (f"Out of temp, but within the product's stability "
                        f"budget ({int(r.budget_used_pct or 0)}% used). Low risk "
                        f"unless it recurs."),
                "action": ("No hold required. Log it and flag the lane/carrier "
                           "if this repeats.")}
    # alert
    spike_first = bool(r.spikes)
    why = (f"Time out of temp ({dur}) exceeds the {budget} stability budget."
           if (r.budget_used_pct or 0) >= 100 else
           "Sudden temperature swing detected (likely a physical handling event).")
    action = ("QUARANTINE on arrival. Hold from patient release pending QA "
              "stability review")
    action += (" and inspect packaging/coolant." if spike_first else ".")
    return {"label": "Alert", "duration_h": dur, "drivers": drivers,
            "why": why, "action": action}


def _report_to_dict(r: ExcursionReport) -> dict[str, Any]:
    return {
        "status": r.status,
        "severity": r.severity,
        "temp_class": r.temp_class,
        "band_lo": r.band_lo,
        "band_hi": r.band_hi,
        "n_readings": r.n_readings,
        "min_temp_c": r.min_temp_c,
        "max_temp_c": r.max_temp_c,
        "mkt_c": r.mkt_c,
        "total_excursion_minutes": r.total_excursion_minutes,
        "total_excursion_hms": fmt_duration(r.total_excursion_minutes),
        "above_minutes": r.above_minutes,
        "above_hms": fmt_duration(r.above_minutes),
        "below_minutes": r.below_minutes,
        "below_hms": fmt_duration(r.below_minutes),
        "median_interval_min": r.median_interval_min,
        "window_minutes": r.window_minutes,
        "window_hms": fmt_duration(r.window_minutes),
        "dropout_threshold_min": r.dropout_threshold_min,
        "spike_delta_c": r.spike_delta_c,
        "spike_window_min": r.spike_window_min,
        "mkt_limit_c": r.mkt_limit_c,
        "alert_budget_min": r.alert_budget_min,
        "alert_budget_hms": fmt_duration(r.alert_budget_min),
        "budget_used_pct": r.budget_used_pct,
        "coverage_pct": r.coverage_pct,
        "monitored_minutes": r.monitored_minutes,
        "dropout_minutes": r.dropout_minutes,
        "dropout_hms": fmt_duration(r.dropout_minutes),
        "ops": _ops_guidance(r),
        "breaches": [
            {
                "start": b.start.isoformat(), "end": b.end.isoformat(),
                "direction": b.direction, "peak_temp_c": b.peak_temp_c,
                "duration_min": b.duration_min, "duration_hms": fmt_duration(b.duration_min),
            } for b in r.breaches
        ],
        "dropouts": [
            {"start": d.start.isoformat(), "end": d.end.isoformat(),
             "duration_min": d.duration_min, "duration_hms": fmt_duration(d.duration_min)}
            for d in r.dropouts
        ],
        "spikes": [
            {"ts": s.ts.isoformat(), "from_c": s.from_c, "to_c": s.to_c,
             "delta_c": s.delta_c, "over_min": s.over_min}
            for s in r.spikes
        ],
    }
