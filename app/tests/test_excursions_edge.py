"""Edge-case / red-team tests for the excursion engine.

These exercise the parts real data didn't: extreme/garbage temperatures, the
cadence-based dropout threshold, spike-vs-dropout interaction, boundary temps,
single/duplicate readings, and the above/below split invariant.
"""
from datetime import datetime, timedelta

from app.coldchain.config import TEMP_BANDS
from app.coldchain.excursions import (Reading, detect_excursions,
                                     mean_kinetic_temp_c)

BAND = TEMP_BANDS["2-8C"]
T0 = datetime(2025, 10, 21, 0, 0)


def _r(minute, temp):
    return Reading(ts=T0 + timedelta(minutes=minute), temp_c=temp)


# --- MKT robustness ---------------------------------------------------------
def test_mkt_does_not_crash_on_absolute_zero_or_below():
    # unguarded, these divide by zero / hit a log domain error
    assert mean_kinetic_temp_c([-273.15]) is None
    assert mean_kinetic_temp_c([-300.0]) is None
    assert mean_kinetic_temp_c([]) is None


def test_mkt_normal_series_and_weighting():
    assert abs(mean_kinetic_temp_c([20.0] * 5) - 20.0) < 0.01
    assert mean_kinetic_temp_c([10.0, 30.0]) >= 20.0   # heat weighted above mean


# --- degenerate series ------------------------------------------------------
def test_single_reading_is_safe():
    rep = detect_excursions([_r(0, 5.0)], BAND)
    assert rep.n_readings == 1
    assert rep.coverage_pct is None          # no interval -> no coverage basis
    assert rep.window_minutes == 0.0
    assert rep.median_interval_min is None
    assert rep.spikes == [] and rep.dropouts == []


def test_duplicate_timestamps_do_not_crash():
    rep = detect_excursions([_r(0, 5.0), _r(0, 6.0), _r(0, 7.0)], BAND)
    assert rep.n_readings == 3


def test_boundary_temps_are_in_range():
    # exactly lo (2) and hi (8) are inside the band (inclusive)
    rep = detect_excursions([_r(0, 2.0), _r(10, 8.0), _r(20, 5.0)], BAND)
    assert rep.status == "ok"
    assert rep.total_excursion_minutes == 0


# --- above/below split invariant -------------------------------------------
def test_above_plus_below_equals_total():
    readings = [_r(0, 5), _r(10, 9), _r(20, 9), _r(30, 5),
                _r(40, 1), _r(50, 1), _r(60, 5)]
    rep = detect_excursions(readings, BAND)
    assert round(rep.above_minutes + rep.below_minutes, 1) == rep.total_excursion_minutes
    assert rep.above_minutes > 0 and rep.below_minutes > 0


# --- cadence-based dropout threshold ---------------------------------------
def test_dropout_threshold_scales_with_cadence():
    # 10-min cadence -> 2x = 20 min threshold
    rep = detect_excursions([_r(i * 10, 5.0) for i in range(6)], BAND)
    assert rep.dropout_threshold_min == 20.0
    # 5-min cadence -> 2x = 10, floored to the 15-min minimum
    rep = detect_excursions([_r(i * 5, 5.0) for i in range(6)], BAND)
    assert rep.dropout_threshold_min == 15.0


def test_spike_suppressed_across_a_dropout_gap():
    # 5-min cadence -> 15-min dropout threshold. An 18-min gap is a dropout even
    # though it's inside the 20-min spike window; a jump across it must NOT spike.
    readings = [_r(0, 5), _r(5, 5), _r(10, 5), _r(28, 9)]
    rep = detect_excursions(readings, BAND)
    assert any(round(d.duration_min) == 18 for d in rep.dropouts)
    assert rep.spikes == []


def test_spike_detected_between_monitored_readings():
    # 4 -> 7.5 in 10 min = 3.5°C swing (>= 2-8C spike_delta_c of 3), monitored
    rep = detect_excursions([_r(0, 4.0), _r(10, 7.5), _r(20, 4.0)], BAND)
    assert len(rep.spikes) >= 1
    assert rep.severity == "alert"           # spike forces alert
