"""Service-layer tests: tolerant parsing (sensor/FedEx swappability), timezone
safety, unit conversion, and the clip-to-transit-window feature."""
from app.zoomlogi import config
from app.zoomlogi import service as svc
from app.zoomlogi.service import (_to_celsius, _transit_window,
                                  build_shipment_view, normalize_fedex,
                                  parse_tive_readings)


# --- tolerant Tive parsing (swap in a different sensor with zero code change) -
def test_parse_handles_mixed_timezones_without_crashing():
    raw = {"trackerData": [
        {"measurementTime": "2025-10-21T13:50:24Z", "temperature": 68},
        {"measurementTime": "2025-10-21T14:00:24", "temperature": 70},   # naive
    ]}
    readings = parse_tive_readings(raw)          # mixed tz must still sort
    assert len(readings) == 2
    assert all(r.ts.tzinfo is not None for r in readings)


def test_parse_tolerates_alternate_sensor_schema(monkeypatch):
    # different vendor: different envelope key, temp key, and nested coordinates.
    # (Unit is an account-level config, not inferred from the key, so pin it to C.)
    monkeypatch.setattr(config, "TIVE_TEMP_UNIT", "C")
    raw = {"records": [
        {"timestamp": "2025-10-21T13:50:24Z", "tempC": 6.5,
         "coordinates": {"lat": 41.8, "lon": -87.6}},
    ]}
    readings = parse_tive_readings(raw)
    assert len(readings) == 1
    assert readings[0].temp_c == 6.5
    assert readings[0].lat == 41.8 and readings[0].lng == -87.6


def test_parse_skips_records_without_a_timestamp():
    raw = [{"temperature": 5.0},
           {"measurementTime": "2025-10-21T00:00:00Z", "temperature": 6.0}]
    assert len(parse_tive_readings(raw)) == 1


def test_parse_empty_or_garbage_returns_empty():
    assert parse_tive_readings({}) == []
    assert parse_tive_readings([]) == []
    assert parse_tive_readings("nonsense") == []


# --- unit conversion --------------------------------------------------------
def test_fahrenheit_conversion(monkeypatch):
    monkeypatch.setattr(config, "TIVE_TEMP_UNIT", "F")
    assert round(_to_celsius(68.0), 1) == 20.0


def test_celsius_passthrough(monkeypatch):
    monkeypatch.setattr(config, "TIVE_TEMP_UNIT", "C")
    assert _to_celsius(6.5) == 6.5


# --- FedEx normalisation ----------------------------------------------------
def test_normalize_fedex_missing_results_is_unavailable():
    assert normalize_fedex({})["available"] is False
    assert normalize_fedex({"output": {}})["available"] is False


def test_transit_window_parsing():
    fedex = {"available": True,
             "picked_up_at": "2025-10-21T13:32:00+00:00",
             "delivered_at": "2025-10-23T10:57:00+00:00"}
    w = _transit_window(fedex)
    assert w is not None and w[0] < w[1]
    assert _transit_window({"available": True}) is None        # no times
    assert _transit_window({"available": False}) is None       # no fedex


# --- end-to-end transit clipping (no network) -------------------------------
def test_build_shipment_view_clips_to_transit(monkeypatch):
    tive_payload = {"trackerData": [
        {"measurementTime": f"2025-10-21T{h:02d}:00:00Z", "temperature": 20}
        for h in range(0, 24, 2)             # 12 readings, every 2h, all in CRT band
    ]}
    fedex_norm = {"available": True, "events": [],
                  "picked_up_at": "2025-10-21T06:00:00Z",
                  "delivered_at": "2025-10-21T12:00:00Z"}
    monkeypatch.setattr(svc, "normalize_fedex", lambda raw: fedex_norm)
    monkeypatch.setattr(svc.fedex_client, "track", lambda tn: {})
    monkeypatch.setattr(svc.tive_client, "tracker_data", lambda: tive_payload)

    full = build_shipment_view("X", "CRT", transit_only=False)
    clipped = build_shipment_view("X", "CRT", transit_only=True)

    assert full["transit_window"] is None
    assert clipped["transit_window"] is not None
    # window 06:00-12:00 keeps readings at 06/08/10/12 -> fewer than the full 12
    assert clipped["excursion"]["n_readings"] == 4
    assert clipped["excursion"]["n_readings"] < full["excursion"]["n_readings"]


def test_invalid_temp_class_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(svc.fedex_client, "track",
                        lambda tn: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(svc.tive_client, "tracker_data",
                        lambda: (_ for _ in ()).throw(RuntimeError("no token")))
    monkeypatch.setattr(svc.tive_client, "snapshot", lambda: None)
    view = build_shipment_view("X", "NOT-A-CLASS")
    assert view["temp_class"] == config.DEFAULT_TEMP_CLASS
