"""Fleet command-center tests: loss-model tiering, CSV validation, NaN
coercion, empty-book handling, and JSON-safety of the whole payload."""
import csv
import math

import pytest

from app.coldchain import fleet
from app.coldchain.fleet import _tier, build_fleet_view


def _write_csv(path, rows):
    """Write a CSV with exactly the required columns; rows override defaults."""
    base = {c: "" for c in fleet._REQUIRED_COLS}
    base.update(
        shipment_id="S1", carrier="FedEx", temp_class="CRT", service_level="2Day",
        origin_dc="DC1", destination_city="Reno", destination_state="NV",
        destination_region="West", ship_date="2025-10-01",
        planned_delivery="2025-10-03", actual_delivery="2025-10-03",
        excursion_minutes="0", min_temp_c="20", recipient_issue="N", issue_type="",
    )
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fleet._REQUIRED_COLS)
        w.writeheader()
        for i, row in enumerate(rows):
            merged = {**base, "shipment_id": f"S{i+1}", **row}
            w.writerow(merged)


def _no_finite_nan(o):
    """Assert no NaN/Inf anywhere (such values are invalid JSON)."""
    if isinstance(o, float):
        assert math.isfinite(o), f"non-finite float in payload: {o}"
    elif isinstance(o, dict):
        for v in o.values():
            _no_finite_nan(v)
    elif isinstance(o, list):
        for v in o:
            _no_finite_nan(v)


def test_tier_boundaries():
    assert _tier(0) == "minor"
    assert _tier(59) == "minor"
    assert _tier(60) == "moder"
    assert _tier(240) == "moder"
    assert _tier(241) == "severe"


def test_real_csv_payload_is_json_safe_and_populated():
    v = build_fleet_view()
    assert v["kpis"]["shipments"] > 0
    assert len(v["loss_model"]["tiers"]) == 3
    _no_finite_nan(v)                       # no NaN/Inf leaks into the response


def test_missing_column_raises_clear_error(tmp_path, monkeypatch):
    bad = tmp_path / "bad_shipments.csv"
    bad.write_text("carrier,temp_class\nFedEx,CRT\n")
    monkeypatch.setattr(fleet, "_csv_path", lambda: bad)
    with pytest.raises(ValueError) as e:
        build_fleet_view()
    assert "missing required column" in str(e.value).lower()


def test_blank_excursion_minutes_is_coerced_not_crashed(tmp_path, monkeypatch):
    p = tmp_path / "blank_shipments.csv"
    _write_csv(p, [{"excursion_minutes": ""}, {"excursion_minutes": "300"}])
    monkeypatch.setattr(fleet, "_csv_path", lambda: p)
    v = build_fleet_view()                  # must not raise on int(NaN)/tiering
    assert v["kpis"]["shipments"] == 2
    _no_finite_nan(v)


def test_all_in_transit_has_no_nan_on_time_rate(tmp_path, monkeypatch):
    p = tmp_path / "transit_shipments.csv"
    _write_csv(p, [{"actual_delivery": ""}, {"actual_delivery": ""}])  # none delivered
    monkeypatch.setattr(fleet, "_csv_path", lambda: p)
    v = build_fleet_view()
    assert isinstance(v["kpis"]["on_time_rate"], float)
    _no_finite_nan(v)


def test_refrigerated_freeze_is_flagged_critical(tmp_path, monkeypatch):
    p = tmp_path / "froze_shipments.csv"
    _write_csv(p, [{"temp_class": "2-8C", "min_temp_c": "-3", "excursion_minutes": "30"}])
    monkeypatch.setattr(fleet, "_csv_path", lambda: p)
    v = build_fleet_view()
    assert v["alert_counts"]["critical"] >= 1
