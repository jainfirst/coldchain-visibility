TO:    Dana, COO — MercyHealth Specialty Pharmacy
FROM:  Pranit Jain, ZoomLogi
DATE:  June 17, 2026
RE:    Cold-chain visibility — what I built and what your data shows

Hi Dana,

Thanks for the time on this. Three things below:
- A live visibility dashboard your team can use Monday. It also has a second page covering your shipment history from the last 90 days.
- Three key findings from those 90 days.
- Key recommendation (plus what I need from you, and the one question I'd ask you).

One note first: while you were on PTO I couldn't reach you to confirm specs, so I
made a couple of quick assumptions — standard industry temperature limits for each
product class, and a placeholder $2K-per-unit product value for the loss math.
Everything below is built on those, and all of it is easy to change the moment you
give me your real numbers.


1 — WHAT YOU CAN USE NEXT WEEK
------------------------------
A live board for any shipment. Two questions, one screen: where is it, and did it
stay in temp.

It draws the temperature curve, flags every excursion — how long, how far out —
and tells your team what to do: green = release, amber = watch, red = quarantine
pending QA. It also flags sensor blind spots, so a silent sensor is never read as
a healthy shipment.

Today you learn a shipment went bad when a pharmacy calls. With the board, your
team sees red and acts before it reaches a patient. Put your highest-value
cold-chain lanes on it day one.


2 — WHAT YOUR LAST 90 DAYS SAY  (~250 shipments)
------------------------------------------------
**Finding 1 — Almost all your risk is one carrier.**
- OnTrac moves only ~15% of your shipments but accounts for 60% of all the time
  product spent out of temp, and a third of the pharmacy complaints.
- Frozen on OnTrac is the worst of it — about 80% went out of temp, averaging
  ~9 hours each.
- FedEx, your biggest carrier, is actually your safest.

**Actionable:** I'd shift frozen and refrigerated off OnTrac, starting with the
CA/WA lanes, onto FedEx, then renegotiate or drop OnTrac for cold-chain
altogether — it's a small slice of volume, about half your loss, and FedEx
already proves those lanes can run in temp.

**Finding 2 — Don't use pharmacy complaints to judge how shipments are doing.**
- The sensors caught 48 excursions, but pharmacies only complained 6 times. 81%
  of the failures reached the pharmacy with no one flagging a thing.
- So recipient feedback has been showing you maybe 1 in 5 of your actual
  failures — which is the whole reason the sensor data matters.

**Actionable:** I've already handled this for you — follow the dashboard. In
short, stop using complaints as the monitor and treat the sensor as the source of
truth: route every red verdict to QA before the product goes out, instead of
waiting for a call.

**Finding 3 — Some of your refrigerated product is freezing, not overheating — which is worse.**
- Of 28 refrigerated excursions, 13 ran too cold and 4 actually froze.
- Freezing is often irreversible for biologics, and it usually comes from
  over-packing dry ice or gel packs.
- No one is watching this — 3 of those 4 frozen ones drew zero complaint.

**Actionable:** This one's an in-house fix, not a carrier fight — audit your
refrigerated packaging spec, because you're over-cooling. I'd have QA pull those
4 frozen shipments and check what packaging they used.

**The Cost** 
I'd estimate this is costing roughly $180K a year in product write-offs, and that's conservative — $2K a unit, before any reship, expedite, or compliance cost — with about half of it behind OnTrac. At specialty-drug unit values it's plausibly north of $0.5M.

**Where You're Fine**
FedEx and UPS on refrigerated and room-temp are performing well, and room-temp product takes care of itself. Don't spend attention there.


3 — ONE KEY RECOMMENDATION
------------------------------------
Move frozen and refrigerated off OnTrac, CA/WA lanes first, onto FedEx — and watch
the excursion rate fall live on the board. It's a small slice of volume, it's
exactly where the losses and complaints sit, and it's the one change that hands
the board a clean before/after number.


4 — WHAT I NEED FROM YOUR TEAM TO PUT THIS IN PRODUCTION
-------------------------------------------------------
To run on every shipment instead of one at a time, my team needs two things from
yours: 
(1) which of your products are cold-chain and their target spec, and
(2) read access to your shipment + sensor feed. 
That turns this dashboard into your daily console.


5 — THE ONE QUESTION I'D ASK YOU
--------------------------------
What does a blown cold-chain shipment actually cost you, all in — the product plus
the reship, the expedite, and the hit when a patient's dose shows up late? I'm
running on a $2K-a-unit ballpark, and the real number swings this between an
$18K-a-year annoyance and a $1.8M problem.

Why this one over everything else: it's the one number I can't guess, and
everything hangs on it — it's what lets us rank every alert and risk flag by what
the shipment's actually worth, so your most valuable product gets the most
attention and the cheap stuff doesn't drown it out.


We're already building live alerts/notifications and a few more features, which
should be ready by next week. Let me know any feedback, or if there's anything
else you need.

Best,
Pranit
