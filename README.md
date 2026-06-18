# ZoomLogi — Cold-Chain Visibility

A live temperature-monitoring dashboard for pharmaceutical cold-chain shipments. It
merges **FedEx** tracking with **Tive** sensor data, detects temperature excursions
against product-specific stability rules, and turns the raw signal into an **action** for
the ops team — for a single shipment *and* across the whole 90-day shipment book.

Built for MercyHealth Specialty Pharmacy (ZoomLogi FDE exercise).

---

## Two screens

| Screen | URL | Answers | Needs a token? |
|---|---|---|---|
| **Live shipment** | `/` | "Is *this* shipment OK, and what do I do about it?" | No — serves a real cached snapshot if no live token |
| **Fleet Command Center** | `/fleet` | "Across everything, what should I be watching?" | No — runs entirely off the CSV |

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.zoomlogi.main:app --reload --port 8000
# open http://localhost:8000
```

No credentials required: the live screen serves the bundled Tive **snapshot** (real cached
data, labelled "NOT LIVE"), and the fleet view runs off the bundled CSV. To go live, copy
`.env.example` → `.env` and fill in tokens (see [Configuration](#configuration)).

**Tests:** `pytest -q` (41 tests).
**Deploy as a shareable link:** see [DEPLOY.md](DEPLOY.md).

---

## How it works

```
FedEx Track API ─┐                          ┌─ normalize_fedex ─┐
                 ├─ clients.py (OAuth + ───►─┤                   ├─► build_shipment_view ─► /api/shipment ─► live dashboard
Tive Public API ─┘   token cache + snapshot) └─ parse_tive ──────┘        │
                                                                     detect_excursions (the engine)
mercyhealth_…csv ───────────────────────────► build_fleet_view ─────────────────────────► /api/fleet ─► fleet dashboard
```

- **`clients.py`** — FedEx + Tive API clients. OAuth with a token cache (module singletons,
  thread-safe refresh). The Tive client **never fabricates readings**: if no valid token, it
  raises, and the service falls back to the last cached snapshot — clearly labelled, never
  presented as live. Successful live pulls are cached to `app/data/tive_snapshot.json`.
- **`service.py`** — normalizes both feeds (FedEx's nested JSON; Tive's tolerant key-matching),
  runs the excursion engine, and assembles the shipment view. Timestamps are normalized to
  UTC-aware on parse so mixed-zone series sort safely.
- **`excursions.py`** — the analytical core (below).
- **`fleet.py`** — portfolio KPIs, a triaged alert queue, breakdowns, and the $-exposure model.
- **`main.py`** — FastAPI routes; sanitizes NaN/Inf out of every response (valid JSON).
- **`util.py`** — shared helpers (durations, safe-float, JSON sanitiser, tz).

---

## The excursion model (the core)

An excursion is **not** automatically an alert — that's how you drown ops in noise. The engine
mirrors how pharma QA actually judges product:

- **Allowed band** `[lo, hi]` per product class. A reading outside it is out-of-range.
- **Step-model time attribution** — each reading "holds" until the next sample; an interval
  counts as out-of-temp if the reading that opens it is out of range. Split into **above** (too
  warm) and **below** (too cold), which sum to the total.
- **Stability budget** — a product tolerates some *cumulative* time out of band before quality
  is at real risk. Under budget → **WATCH** (log it); over budget → **ALERT** (quarantine).
- **Sudden-swing override** — a jump ≥ `spike_delta_c` within 20 min signals a *physical* event
  (coolant failed, box opened) and forces an ALERT regardless of remaining budget. Never fires
  across a sensor dropout (a gap we couldn't see).
- **Sensor dropouts** — a gap longer than **~2× the sensor's own median cadence** (floored at
  15 min, capped at 60) is treated as "blind": excluded from in-range *and* excursion time, and
  surfaced as **coverage %** + **blind time**. Silence is never reported as "in temp".
- **MKT (Mean Kinetic Temperature, USP <1079>)** — cumulative *heat* stress (a long warm soak
  weighs more than a brief spike). Judged against the class's nominal upper limit. Heat-only —
  it does not reflect cold excursions.

Severity tiers: **OK → WATCH → ALERT**. Each verdict is translated into a plain-language
**recommended action** for the ops team.

### Temperature classes (`config.py`)

| Class | Band (°C) | MKT limit | Stability budget | Spike |
|---|---|---|---|---|
| `2-8C` (refrigerated) | 2 – 8 | ≤ 8 | 60 min | 3 °C |
| `CRT` (loose room-temp) | 15 – 30 | ≤ 25 | 8 h | 6 °C |
| `CRT-strict` | 20 – 25 | ≤ 25 | 4 h | 4 °C |
| `Frozen` | ≤ −15 | ≤ −16 | 30 min | 3 °C |

Budgets are **defensible placeholders** pending MercyHealth's real stability data — the ordering
(frozen < refrigerated < tight room-temp < loose room-temp) holds on first principles. All
policy lives in this one table; confirm the numbers with Dana and every alert updates.

### Bonus feature — clip to transit window

The Tive logger keeps recording ~30 h past delivery. The **"Transit only"** toggle (or
`?transit_only=true`) clips the analysis to the FedEx pickup→delivery window, isolating
in-transit risk from post-delivery dock time.

---

## Live dashboard, panel by panel

- **Status banner + ops action** — the severity verdict, the recommended action, and the
  stability-budget bar (how much of the product's tolerance is burned).
- **Sensor temperature chart** — the trace with the allowed band shaded; out-of-range segments
  in red; dashed pickup/delivery guides (hover a line for its timestamp; hover a point for the
  reading). A **LIVE / NOT-LIVE** badge makes the data source unmistakable.
- **Temperature exposure** — max/min/MKT, total time out of temp split into too-warm/too-cold.
  Color = a pass/fail verdict (green in-band, red breached); white = a descriptive number.
- **Sensor data quality** — readings, sample cadence, monitored span, coverage, blind time,
  sudden swings. Mostly green here = the trace is trustworthy.
- **Shipment + FedEx scan history** — status, route, milestones, scan trail.

## Fleet Command Center

KPIs (excursion rate, on-time, $ exposure), a **triaged alert queue** (critical / at-risk /
watch with reason, dollar at risk, and a recommended action), excursion rate by carrier, the
ranked **worst lane × product** combos, exception mix, and a transparent **$-exposure model**:
each excursion is scored by severity → write-off odds × product value → summed → annualized.
(`UNIT_VALUE` and the odds are placeholders pending real data; the legend states this.)

---

## Configuration

All via environment (`.env` locally, host env vars in prod). Nothing is required; see
`.env.example`. Highlights:

| Var | Purpose | Absent → |
|---|---|---|
| `FEDEX_CLIENT_ID` / `_SECRET` | FedEx OAuth | FedEx panel "unavailable" |
| `TIVE_BEARER_TOKEN` | Tive bearer token (~1 h) | snapshot served, labelled NOT LIVE |
| `TIVE_CLIENT_ID` / `_SECRET` | Tive OAuth | snapshot fallback |
| `TIVE_TEMP_UNIT` | sensor unit (`F`/`C`), converted to °C | defaults to `F` |
| `TEMP_CLASS`, `TRACKING_NUMBER` | demo defaults | `CRT`, `885364545720` |

---

## API

| Endpoint | Returns |
|---|---|
| `GET /api/shipment?tracking_number=&temp_class=&transit_only=` | merged FedEx + Tive view + excursion analysis |
| `GET /api/fleet` | KPIs, alert queue, breakdowns, loss model |
| `GET /api/temp-classes` | the temperature policy table |
| `GET /healthz` | readiness: token state, snapshot age, CSV presence |

---

## Project structure

```
app/
  zoomlogi/
    main.py         FastAPI routes (+ JSON sanitisation, no-store headers)
    config.py       credentials (env-only) + temperature policy
    clients.py      FedEx + Tive clients (token cache, snapshot resilience)
    service.py      normalisation + view assembly + transit clipping
    excursions.py   the excursion engine (bands, budget, spikes, dropouts, MKT)
    fleet.py        portfolio KPIs, triage, loss model
    util.py         shared helpers
  static/           index.html (live) + fleet.html — vanilla HTML/CSS/JS, Chart.js
  data/             tive_snapshot.json (real cached sensor pull)
  tests/            41 tests (engine, service, fleet, util)
analysis/           Part-2 historical analysis notebook
deliverables/       one-pager, engineering notes, screenshots
mercyhealth_…csv    the 90-day shipment book
Dockerfile, render.yaml, Procfile, DEPLOY.md   deployment
```

---

## Principles

1. **Never fabricate sensor data.** No token → an explicit "no data" or a clearly-labelled
   snapshot of real cached readings. Inventing temperatures would be worse than showing nothing.
2. **An excursion is not an alert.** Escalate on the product's *stability budget* or a physical
   swing — not on every wobble.
3. **Honest about coverage.** Sensor silence is surfaced as blind time, never assumed in-range.
4. **One source of truth for policy.** Bands, budgets, loss assumptions live in config; change a
   number, everything updates.
5. **Degrade gracefully.** Any data source can be down and the rest of the app keeps working.
