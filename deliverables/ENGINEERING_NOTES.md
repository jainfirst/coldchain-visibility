# Engineering Notes — Alerting Model & Real-World Considerations
*Companion to the live dashboard. Written so the decisions are defensible and the
roadmap is explicit.*

---

## A. The alerting model (what I added and why)

**Problem:** "any reading outside the band = alarm" is useless in the real world.
A truck drifting 1 °C off for ten minutes is not the same as a frozen biologic
thawing for nine hours. If everything pages Dana, nothing does.

So the tool now decides **whether the excursion is worth an alert**, on two rules:

### 1. Stability budget (cumulative time tolerance)
Each product class gets a **budget** = how much *total* time out of band is
tolerable before we escalate. The verdict has three tiers:

| Tier | Meaning | Ops action on the dash |
|---|---|---|
| **OK** (green) | never left the band | clear for normal release |
| **WATCH** (amber) | out of band but **within** budget | log it, no hold; flag the lane if it recurs |
| **ALERT** (red) | **budget exceeded** | quarantine on arrival, hold from patient release pending QA |

Budgets used (in `config.py`, **placeholders pending Dana's real stability data**):
- **2–8 °C refrigerated**: 60 min — biologics/vaccines are time-sensitive (CDC VFC tracks excursions tightly).
- **CRT (15–30 °C)**: 8 h — room-temp products are robust, judged on mean kinetic temperature.
- **CRT-strict (20–25 °C)**: 4 h — tighter-spec room-temp.
- **Frozen**: 30 min — thaws fast, rarely recovers.

### 2. Sudden-swing override
A jump of **≥ N °C within 20 min** fires an **ALERT immediately, regardless of
budget**, because a fast change usually means a *physical event* — coolant pack
failed, box opened, left on a hot tarmac — which is more dangerous than slow
drift. Threshold N is per class (3 °C refrigerated/frozen, 6 °C CRT).

**Why this is the right shape:** it mirrors how pharma QA actually thinks —
cumulative exposure *plus* shock events — and it keeps the alert list short and
trustworthy. The budgets are the one thing I'm guessing at, which is exactly why
"what's your real stability spec?" is my #1 question for Dana.

> Honesty note: under this model the demo shipment is an **ALERT** (15 h cold vs
> an 8 h CRT budget), not the "mild, low-risk" read I'd have given by eyeballing
> it. The budget makes the call rigorous instead of vibes.

### Dashboard readability changes
- Durations are human-readable everywhere (**`15h 00m`**, not `899.8 min`).
- A **"Recommended action for ops"** strip states the verdict in plain English +
  a **stability-budget bar** (e.g. *15h 00m / 8h 00m → 187%*).
- **Drivers** list spells out *why* (which breach, how much budget, any swing).
- I deliberately kept the data points few: temperature curve, budget bar, and a
  6-tile exposure scoreboard. More dials would dilute the one question Dana asks —
  *do I trust this shipment or not?*

---

## B. Real-world considerations & what I'd build next

### Sensor ↔ FedEx data conflicts (the interesting ones)
- **The sensor outlives the shipment.** On this trip FedEx marked *delivered* on
  Oct 23 10:57, but Tive kept logging until Oct 24 16:40 — **~30 h of readings
  are post-delivery** (the box sitting in the pharmacy). Right now I score the
  whole series, so some of the "excursion" isn't the carrier's fault. **Next:
  clip the temperature analysis to the FedEx transit window (pickup → delivery)
  for carrier accountability, and monitor post-delivery handling separately.**
  This is a policy call for Dana, not just code.
- **Timezones.** Tive timestamps are UTC (`Z`); FedEx uses offsets; the UI renders
  local. I normalize on parse, but a production system needs one canonical tz and
  to show shipment-local time, not viewer-local.
- **Units.** Tive returns Fahrenheit on this account with **no unit field** in the
  payload. I convert F→C and made it a config switch — but if the account silently
  flips to Celsius, every verdict breaks. **Next: assert a sane range on ingest
  and alert on a unit mismatch.**
- **Which truth wins?** If FedEx says "exception/returned" but the sensor shows
  motion toward the destination, or vice versa, the dashboard should surface the
  disagreement rather than silently pick one.

### Things that break in production
- **Tive token lifetime is 1 hour.** Fine for a demo; production needs a service
  account / automatic OAuth refresh, not a pasted token.
- **API downtime & rate limits.** Already degrades gracefully (one source down
  still renders the other), but needs retries with backoff, caching of the last
  good pull, and a "stale data" badge.
- **Silent sensor death mid-flight.** Dropout detection exists; production needs
  to *push* an alert when a sensor goes dark on an in-transit shipment, plus watch
  Tive's `battery`/`light` fields as leading indicators.
- **Carrier coverage gap.** The history shows **OnTrac** is the real risk — but we
  can only *live-track FedEx* today. UPS and OnTrac need their own tracking
  integrations or we're blind on exactly the carrier that fails most.

### Healthcare / compliance (this is patient-bound product)
- **Audit trail.** Specialty pharma needs an immutable temperature record per
  shipment (FDA 21 CFR Part 11-style). Today it's a live view, not a system of
  record — needs persistence + tamper-evident logs.
- **Access control & PHI.** Per-user auth, and care that destination/recipient
  data is handled to HIPAA-adjacent standards. Secrets belong in a vault, not
  `.env`.
- **Mean Kinetic Temperature (MKT).** The real pharma metric for cumulative heat
  stress, not just minutes-out-of-band. A natural upgrade to the budget model.

### Roadmap (what turns this prototype into the product Dana bought)
1. **Fleet view** — all active shipments in one list with status + an alert queue,
   not one shipment at a time.
2. **Push alerts** — email/SMS/Slack to ops + Dana on ALERT. Right now it's pull
   (you have to look). Visibility should come to her.
3. **Excursion geolocation** — Tive gives lat/long; pin *where* a breach happened
   ("Memphis hub, 2 a.m.") to drive lane/carrier fixes.
4. **Shipment → product mapping** — so the right band auto-applies instead of an
   operator picking it (removes the biggest manual step and error source).
5. **Predictive** — combine Finding 3 (late = hot) with live FedEx ETA to flag
   *at-risk* shipments before the excursion happens.
