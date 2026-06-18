"""
Unit tests for the excursion engine.

The live Tive trace happens to have no sensor gaps, so these tests exercise
the parts that real data didn't: dropout handling, low/high breach detection,
and the "no readings" case.  Run:  pytest app/tests
"""
from datetime import datetime, timedelta

from app.zoomlogi.config import TEMP_BANDS
from app.zoomlogi.excursions import Reading, detect_excursions, mean_kinetic_temp_c

BAND = TEMP_BANDS["2-8C"]  # 2-8 C
T0 = datetime(2025, 10, 21, 0, 0)


def _r(minute, temp):
    return Reading(ts=T0 + timedelta(minutes=minute), temp_c=temp)


def test_all_in_range_is_ok():
    readings = [_r(i * 10, 5.0) for i in range(6)]  # 50 min @ 5 C
    rep = detect_excursions(readings, BAND)
    assert rep.status == "ok"
    assert rep.total_excursion_minutes == 0
    assert rep.coverage_pct == 100.0


def test_high_breach_counts_minutes():
    # 0,10 in range; 20,30 hot (9 C); 40 back in range.
    readings = [_r(0, 5), _r(10, 5), _r(20, 9), _r(30, 9), _r(40, 5)]
    rep = detect_excursions(readings, BAND)
    assert rep.status == "excursion"
    assert len(rep.breaches) == 1
    assert rep.breaches[0].direction == "high"
    # excursion time = interval opened by the first hot reading (20->30) +
    # (30->40) = 20 min.
    assert rep.total_excursion_minutes == 20.0
    assert rep.max_temp_c == 9


def test_low_breach_detected():
    readings = [_r(0, 5), _r(10, 1.0), _r(20, 1.0), _r(30, 5)]
    rep = detect_excursions(readings, BAND)
    assert rep.breaches[0].direction == "low"
    assert rep.min_temp_c == 1.0


def test_dropout_not_counted_as_excursion_and_lowers_coverage():
    # Reading at 0 (in range), then a 120-min gap (> DROPOUT_GAP_MINUTES),
    # then back. The gap must NOT be counted as in-range or excursion time,
    # and coverage must drop below 100%.
    readings = [_r(0, 5), _r(120, 5), _r(130, 5)]
    rep = detect_excursions(readings, BAND)
    assert rep.status == "ok"
    assert rep.dropout_minutes == 120.0
    assert rep.monitored_minutes == 10.0   # only the 120->130 interval
    assert rep.coverage_pct is not None and rep.coverage_pct < 50


def test_excursion_does_not_bridge_a_dropout():
    # Hot reading, then a long blind gap, then hot again. We must NOT report
    # one continuous breach across the gap (we were blind), but two.
    readings = [_r(0, 9), _r(120, 9), _r(130, 9)]
    rep = detect_excursions(readings, BAND)
    assert len(rep.breaches) == 2


def test_no_readings_is_no_data():
    rep = detect_excursions([], BAND)
    assert rep.status == "no_data"
    assert rep.severity == "no_data"
    assert rep.coverage_pct is None


# --- alerting model: budget tiers + sudden-spike override --------------------
def test_small_excursion_under_budget_is_watch_not_alert():
    # 2-8C budget is 60 min. A gentle drift just over 8C for one 10-min interval
    # is out of range but well under budget, and the swing is too small to be a
    # spike -> WATCH (don't page Dana yet).
    readings = [_r(0, 7.0), _r(10, 8.5), _r(20, 7.0)]
    rep = detect_excursions(readings, BAND)
    assert rep.status == "excursion"
    assert not rep.spikes
    assert rep.total_excursion_minutes < rep.alert_budget_min
    assert rep.severity == "watch"


def test_excursion_over_budget_is_alert():
    # Sustained hot for >60 min (the 2-8C budget) -> ALERT.
    readings = [_r(i * 10, 9.0) for i in range(9)]  # 80 min hot
    rep = detect_excursions(readings, BAND)
    assert rep.total_excursion_minutes >= rep.alert_budget_min
    assert rep.severity == "alert"


def test_mkt_constant_equals_temp_and_varying_exceeds_mean():
    # A constant temperature series -> MKT equals that temperature.
    assert abs(mean_kinetic_temp_c([20.0] * 10) - 20.0) < 0.01
    # MKT is always >= the arithmetic mean (it weights hot readings more).
    temps = [10.0, 30.0]
    assert mean_kinetic_temp_c(temps) >= sum(temps) / len(temps)
    assert mean_kinetic_temp_c([]) is None


def test_sudden_spike_forces_alert_even_within_budget():
    # All readings in range, but a sudden jump (5 -> 9 = within band? 9>8 so
    # it's also a breach). Use an in-band fast swing to isolate the spike rule:
    # 2-8C spike_delta_c = 3, window 20 min. 4 -> 7.5 in 10 min = 3.5 C swing.
    readings = [_r(0, 4.0), _r(10, 7.5), _r(20, 4.0)]
    rep = detect_excursions(readings, BAND)
    assert len(rep.spikes) >= 1
    assert rep.severity == "alert"
