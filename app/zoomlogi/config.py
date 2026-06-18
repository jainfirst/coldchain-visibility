"""
Configuration: API credentials and temperature-excursion policy.

Credentials are read ONLY from the environment (.env locally; host env vars in
production) — no secret values live in source, so the repo is safe to publish.
If a credential is absent the app degrades gracefully: that data source reports
"unavailable", while the cached Tive snapshot and the CSV-backed fleet view
keep working with no token at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

try:  # load .env if present, so credentials/tokens don't live in source
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- API credentials -------------------------------------------------------
# Secrets — env-only, no values in source (safe to publish). Absent -> graceful.
FEDEX_CLIENT_ID = os.getenv("FEDEX_CLIENT_ID", "")
FEDEX_CLIENT_SECRET = os.getenv("FEDEX_CLIENT_SECRET", "")
FEDEX_TOKEN_URL = "https://apis.fedex.com/oauth/token"
FEDEX_TRACK_URL = "https://apis.fedex.com/track/v1/trackingnumbers"

TIVE_CLIENT_ID = os.getenv("TIVE_CLIENT_ID", "")
TIVE_CLIENT_SECRET = os.getenv("TIVE_CLIENT_SECRET", "")
TIVE_TOKEN_URL = "https://api.tive.com/public/v3/authenticate"
TIVE_ACCOUNT_ID = os.getenv("TIVE_ACCOUNT_ID", "6849")   # account id (not a secret)
TIVE_SHIPMENT = os.getenv("TIVE_SHIPMENT", "ZoomLogi_Oct_2025")
TIVE_DATA_URL = f"https://api.tive.com/public/v3/Shipments/{TIVE_SHIPMENT}/trackerData"
# If Tive OAuth is unavailable, paste a vendor-supplied 1-hour bearer token here
# (or set TIVE_BEARER_TOKEN) and the client goes live with zero code changes.
TIVE_BEARER_TOKEN = os.getenv("TIVE_BEARER_TOKEN")

# Tive's trackerData has no unit field; the unit is an account setting.  This
# account returns Fahrenheit (verified: Chicago origin reads ~68 -> 20 C, and
# 68 C is physically impossible for a parcel).  All our temperature bands are
# Celsius, so we convert on ingest.  If MercyHealth's account is switched to
# Celsius, set TIVE_TEMP_UNIT=C and nothing else changes.
TIVE_TEMP_UNIT = os.getenv("TIVE_TEMP_UNIT", "F").upper()

DEFAULT_TRACKING_NUMBER = os.getenv("TRACKING_NUMBER", "885364545720")


# --- Temperature policy ----------------------------------------------------
@dataclass(frozen=True)
class TempBand:
    """Allowed temperature band for a product class, in Celsius.

    `lo`/`hi` are the hard limits — readings outside this band are excursions.
    `nominal_lo`/`nominal_hi` describe the target steady-state range used only
    for display (e.g. "running warm but still in spec").
    """
    label: str
    lo: float
    hi: float
    nominal_lo: float
    nominal_hi: float
    rationale: str
    # --- alerting policy (the part that decides whether to bother Dana) ---
    # An excursion is NOT automatically an alert.  Pharma stability is governed
    # by a *budget*: a product can tolerate some cumulative time out of band.
    # We only escalate to ALERT once that budget is spent.  A sudden temperature
    # SWING (>= spike_delta_c within SPIKE_WINDOW_MIN) overrides the budget and
    # alerts immediately, because a fast jump usually means a physical event
    # (coolant failed, box opened, left on a hot tarmac) rather than slow drift.
    alert_budget_min: float = 60.0   # out-of-range minutes tolerated before ALERT
    spike_delta_c: float = 4.0       # sudden jump that forces an immediate ALERT
    budget_basis: str = ""           # where the budget number comes from


# ASSUMPTION (see README): Dana did not give explicit thresholds before PTO.
# These are the standard pharma cold-chain bands and are intentionally
# conservative.  They live in ONE place so the moment Dana confirms her real
# product specs, we change four numbers and every alert updates.
#
#   2-8C   : USP refrigerated storage. Hard band 2-8 C.
#   CRT    : USP <659> Controlled Room Temp. Nominal 20-25 C, with allowed
#            excursions 15-30 C — we alert outside 15-30.
#   Frozen : Must stay frozen. Ceiling set to -15 C, recovered from the 90-day
#            history (clean frozen shipments top out at -16 C, flagged ones
#            start at -12.2 C, so the data was scored against ~-15 C, not -10).
# Budget numbers below are DEFENSIBLE PLACEHOLDERS pending MercyHealth's real
# stability data (this is the #1 thing we need from Dana — see ONE_QUESTION).
# Rationale: refrigerated biologics/vaccines are time-sensitive (CDC VFC tracks
# excursions tightly) -> short budget. Room-temp products are robust and judged
# on mean kinetic temperature -> generous budget. Frozen product thaws fast and
# rarely recovers -> minimal budget.
TEMP_BANDS: dict[str, TempBand] = {
    "2-8C": TempBand("2-8C", 2.0, 8.0, 2.0, 8.0,
                     "USP refrigerated storage, 2-8 C hard limits.",
                     alert_budget_min=60.0, spike_delta_c=3.0,
                     budget_basis="Refrigerated biologic/vaccine tolerance "
                     "(~1 h cumulative) — placeholder, confirm with MercyHealth."),
    "CRT": TempBand("CRT", 15.0, 30.0, 20.0, 25.0,
                    "USP <659> Controlled Room Temp; nominal 20-25 C, "
                    "allowed excursion band 15-30 C (used for transport "
                    "monitoring to avoid false alarms on normal transit drift).",
                    alert_budget_min=480.0, spike_delta_c=6.0,
                    budget_basis="Room-temp products are robust; ~8 h budget "
                    "as a transport guardrail — placeholder."),
    "CRT-strict": TempBand("CRT-strict", 20.0, 25.0, 20.0, 25.0,
                           "USP <659> nominal Controlled Room Temp label-storage "
                           "range, 20-25 C. Strict band for tight-spec products.",
                           alert_budget_min=240.0, spike_delta_c=4.0,
                           budget_basis="Tight-spec room-temp; ~4 h budget — "
                           "placeholder."),
    "Frozen": TempBand("Frozen", -90.0, -15.0, -25.0, -16.0,
                       "Must remain frozen; warmer than -15 C is an excursion "
                       "(ceiling recovered from the 90-day history, not assumed).",
                       alert_budget_min=30.0, spike_delta_c=3.0,
                       budget_basis="Frozen product thaws fast; ~30 min budget "
                       "— placeholder."),
}

# A sudden temperature swing of >= band.spike_delta_c within this many minutes
# is treated as a likely physical event and alerts immediately, regardless of
# how much budget is left.
SPIKE_WINDOW_MIN = float(os.getenv("SPIKE_WINDOW_MIN", "20"))
# Default class for the demo shipment (885364545720).  Its sensor range of
# ~12-28 C is inconsistent with refrigerated (2-8 C) or frozen handling, so we
# treat it as CRT.  The dashboard lets the operator override per shipment, and
# the real product class should be confirmed with MercyHealth (see ONE_QUESTION).
DEFAULT_TEMP_CLASS = os.getenv("TEMP_CLASS", "CRT")

# A reading gap longer than this (minutes) is treated as a sensor DROPOUT:
# we do not interpolate temperature across it and we do not silently assume
# the product stayed in range.  We surface it instead.
DROPOUT_GAP_MINUTES = float(os.getenv("DROPOUT_GAP_MINUTES", "30"))
