"""
Temperature excursion detection.

Input: a chronological list of sensor readings (timestamp + temperature) and
the product's TempBand.  Output: a structured excursion report.

Design decisions (be ready to defend these in the debrief):

1. Excursion = any reading strictly outside the band's [lo, hi].  We report
   both *cumulative minutes out of range* and *distinct breach events*,
   because cold-chain stability budgets are about total time out of temp, not
   a single instantaneous spike.

2. Time attribution uses a step model: a reading "holds" until the next
   sample.  An interval contributes to excursion time iff the reading that
   opens it is out of range.  This is simple, auditable, and slightly
   conservative (a breach is counted from the first bad reading).

3. Sensor dropouts: if the gap between two consecutive readings exceeds
   DROPOUT_GAP_MINUTES we do NOT interpolate temperature across it.  We mark
   the gap as a dropout and exclude it from both in-range and excursion time.
   We report data-coverage % so the viewer knows how much of the trip we were
   actually blind for — silence is never reported as "in temp".
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .config import DROPOUT_GAP_MINUTES, SPIKE_WINDOW_MIN, TempBand

# Mean Kinetic Temperature (USP <1079>): the single temperature that captures
# cumulative thermal stress, which is what actually degrades product — a brief
# spike and a long warm soak are not the same even at the same peak temp.
# Delta-H = 83.144 kJ/mol (USP convention) over R = 8.314 J/mol/K gives 10000 K.
_DELTA_H_OVER_R = 10000.0


def mean_kinetic_temp_c(temps_c: list[float]) -> Optional[float]:
    """Mean Kinetic Temperature in °C, or None if it can't be computed.

    Robust to garbage input: only physically valid temperatures (above absolute
    zero) feed the Arrhenius term, which guards the div-by-zero at -273.15 °C and
    the log() domain error when extreme cold underflows every term to 0.
    """
    kelvins = [t + 273.15 for t in temps_c if t is not None and t > -273.15]
    if not kelvins:
        return None
    try:
        mean_term = sum(math.exp(-_DELTA_H_OVER_R / k) for k in kelvins) / len(kelvins)
        if mean_term <= 0.0:                       # extreme cold underflowed to 0
            return None
        mkt_k = _DELTA_H_OVER_R / (-math.log(mean_term))
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return round(mkt_k - 273.15, 2)


@dataclass
class Reading:
    ts: datetime
    temp_c: Optional[float]  # None = sensor reported but no temperature value
    lat: Optional[float] = None
    lng: Optional[float] = None
    place: Optional[str] = None


@dataclass
class Breach:
    start: datetime
    end: datetime
    direction: str          # "high" or "low"
    peak_temp_c: float      # most extreme temperature during the breach
    duration_min: float


@dataclass
class Dropout:
    start: datetime
    end: datetime
    duration_min: float


@dataclass
class Spike:
    """A sudden temperature swing — possible physical event, alerts at once."""
    ts: datetime
    from_c: float
    to_c: float
    delta_c: float
    over_min: float


@dataclass
class ExcursionReport:
    status: str                 # "ok" | "excursion" | "no_data"
    severity: str               # "ok" | "watch" | "alert" | "no_data"
    temp_class: str
    band_lo: float
    band_hi: float
    n_readings: int
    min_temp_c: Optional[float]
    max_temp_c: Optional[float]
    mkt_c: Optional[float]
    total_excursion_minutes: float
    alert_budget_min: float = 0.0
    budget_used_pct: Optional[float] = None
    breaches: list[Breach] = field(default_factory=list)
    dropouts: list[Dropout] = field(default_factory=list)
    spikes: list[Spike] = field(default_factory=list)
    monitored_minutes: float = 0.0     # time we had eyes on the sensor
    dropout_minutes: float = 0.0       # time we were blind
    coverage_pct: Optional[float] = None  # monitored / (monitored + dropout)
    above_minutes: float = 0.0         # of out-of-range time, how long too WARM
    below_minutes: float = 0.0         # of out-of-range time, how long too COLD
    median_interval_min: Optional[float] = None  # typical gap between samples
    window_minutes: float = 0.0        # first reading -> last reading span
    dropout_threshold_min: float = 0.0  # gap length we call a "dropout" (cadence-based)
    spike_delta_c: float = 0.0         # °C swing that counts as a sudden swing
    spike_window_min: float = 0.0      # ...within this many minutes
    mkt_limit_c: Optional[float] = None  # MKT acceptance ceiling = band's nominal upper


def _minutes(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60.0


def detect_excursions(readings: list[Reading], band: TempBand) -> ExcursionReport:
    """Analyse a chronological reading series against a temperature band."""
    temps = [r for r in readings if r.temp_c is not None]
    temps.sort(key=lambda r: r.ts)

    if not temps:
        return ExcursionReport(
            status="no_data", severity="no_data", temp_class=band.label,
            band_lo=band.lo, band_hi=band.hi, n_readings=0,
            min_temp_c=None, max_temp_c=None, mkt_c=None,
            total_excursion_minutes=0.0, coverage_pct=None,
        )

    # Sampling cadence drives the dropout threshold below (and is reported to the UI).
    gaps_all = [_minutes(temps[i].ts, temps[i + 1].ts) for i in range(len(temps) - 1)]
    median_gap = round(statistics.median(gaps_all), 1) if gaps_all else None
    # A gap counts as a DROPOUT (we were blind) once it exceeds ~2x the sensor's own
    # median interval — i.e. at least one expected reading is missing — rather than a
    # fixed 30 min. This adapts to the logger: a 10-min cadence is judged blind at
    # ~20 min, a 5-min cadence at ~15 min. Floored at 15 min (temperature barely moves
    # in a sealed parcel under that, so a single late sample shouldn't trip it) and
    # capped at 60 min so even a slow logger is flagged blind within the hour.
    dropout_threshold = (min(60.0, max(15.0, round(2 * median_gap)))
                         if median_gap else DROPOUT_GAP_MINUTES)

    def out_of_range(t: float) -> Optional[str]:
        if t < band.lo:
            return "low"
        if t > band.hi:
            return "high"
        return None

    breaches: list[Breach] = []
    dropouts: list[Dropout] = []
    total_exc = 0.0
    above_min = 0.0
    below_min = 0.0
    monitored = 0.0
    dropout_min = 0.0

    open_breach: Optional[dict] = None

    for i, r in enumerate(temps):
        direction = out_of_range(r.temp_c)

        # --- maintain the current breach event ---
        if direction:
            if open_breach is None:
                open_breach = {
                    "start": r.ts, "direction": direction,
                    "peak": r.temp_c, "end": r.ts,
                }
            else:
                open_breach["end"] = r.ts
                # track the most extreme temperature in the breach
                if direction == "high":
                    open_breach["peak"] = max(open_breach["peak"], r.temp_c)
                else:
                    open_breach["peak"] = min(open_breach["peak"], r.temp_c)
        elif open_breach is not None:
            breaches.append(_close_breach(open_breach))
            open_breach = None

        # --- attribute the interval to the NEXT reading ---
        if i + 1 < len(temps):
            gap = _minutes(r.ts, temps[i + 1].ts)
            if gap > dropout_threshold:
                # We were blind here. Close any open breach at this reading;
                # do not extend an excursion across an unmonitored gap.
                dropouts.append(Dropout(r.ts, temps[i + 1].ts, gap))
                dropout_min += gap
                if open_breach is not None:
                    breaches.append(_close_breach(open_breach))
                    open_breach = None
            else:
                monitored += gap
                if direction == "high":     # interval opened too warm
                    total_exc += gap
                    above_min += gap
                elif direction == "low":    # interval opened too cold
                    total_exc += gap
                    below_min += gap

    if open_breach is not None:
        breaches.append(_close_breach(open_breach))

    # --- sudden-swing detection: a fast jump between two MONITORED readings ---
    # Reuse gaps_all (no recompute). A swing is only meaningful across a gap we
    # actually watched: skip dropouts (gap > threshold) — we can't call a jump
    # "sudden" across a window we were blind for.
    spikes: list[Spike] = []
    for i, gap in enumerate(gaps_all):
        if 0 < gap <= SPIKE_WINDOW_MIN and gap <= dropout_threshold:
            delta = temps[i + 1].temp_c - temps[i].temp_c
            if abs(delta) >= band.spike_delta_c:
                spikes.append(Spike(
                    ts=temps[i + 1].ts, from_c=round(temps[i].temp_c, 2),
                    to_c=round(temps[i + 1].temp_c, 2),
                    delta_c=round(delta, 1), over_min=round(gap, 1),
                ))

    # --- severity: budget governs WATCH vs ALERT; a spike forces ALERT ---
    budget = band.alert_budget_min
    used_pct = round(total_exc / budget * 100, 0) if budget > 0 else None
    over_budget = budget > 0 and total_exc >= budget
    if not breaches and not spikes:
        severity = "ok"
    elif over_budget or spikes:
        severity = "alert"
    else:
        severity = "watch"

    all_temps = [r.temp_c for r in temps]
    total_window = monitored + dropout_min
    coverage = (monitored / total_window * 100.0) if total_window > 0 else None

    window = _minutes(temps[0].ts, temps[-1].ts) if len(temps) > 1 else 0.0

    return ExcursionReport(
        status="excursion" if breaches else "ok",
        severity=severity,
        temp_class=band.label,
        band_lo=band.lo, band_hi=band.hi,
        n_readings=len(temps),
        min_temp_c=min(all_temps), max_temp_c=max(all_temps),
        mkt_c=mean_kinetic_temp_c(all_temps),
        total_excursion_minutes=round(total_exc, 1),
        alert_budget_min=budget,
        budget_used_pct=used_pct,
        breaches=breaches,
        dropouts=dropouts,
        spikes=spikes,
        monitored_minutes=round(monitored, 1),
        dropout_minutes=round(dropout_min, 1),
        coverage_pct=round(coverage, 1) if coverage is not None else None,
        above_minutes=round(above_min, 1),
        below_minutes=round(below_min, 1),
        median_interval_min=median_gap,
        window_minutes=round(window, 1),
        dropout_threshold_min=round(dropout_threshold, 1),
        spike_delta_c=band.spike_delta_c,
        spike_window_min=SPIKE_WINDOW_MIN,
        mkt_limit_c=band.nominal_hi,
    )


def _close_breach(b: dict) -> Breach:
    return Breach(
        start=b["start"], end=b["end"], direction=b["direction"],
        peak_temp_c=round(b["peak"], 2),
        duration_min=round(_minutes(b["start"], b["end"]), 1),
    )
