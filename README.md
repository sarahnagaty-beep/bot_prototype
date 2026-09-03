# Buyer Bot Prototype — AI Customer Profiler

A working prototype of the WhatsApp buyer bot described in *Buyer Bot — Conversation
Prototype*, built as the entry-point product from the *Tier 2 Brokerage Penetration
Strategy*: intercept the buyer at the Meta lead-gen ad, qualify them in a short
WhatsApp conversation, and hand the agent a **buyer brief before the first call**.

It runs end to end offline — no WhatsApp credentials, no CRM, no API keys.

```bash
pip install -r requirements.txt
python main.py demo      # run every scenario, write samples/, refresh the dashboard
python main.py scale     # add 3,000 generated conversations across a 632-agent floor
python main.py chat      # talk to the bot in your terminal
python main.py serve     # webhook + dashboard at http://localhost:8000
```

## What's in it

| Piece | Where | What it does |
|---|---|---|
| Conversation engine | `src/flow.py` | Nodes 0–11 of the script as a state machine |
| Language understanding | `src/nlu.py` | Buttons *and* free text ("around 10 million", "near the coast") |
| Qualification scoring | `src/scoring.py` | The §3 signal table → Cold / Warm (MQL) / Hot (SQL) |
| Inventory matching | `src/inventory.py` | Ranked best-fit, widening, the Node 7 upsell |
| CRM / profile store | `src/crm.py` | Returning-buyer retrieval, lead writes, region-aware assignment, event log |
| Floor + volume data | `src/scale.py`, `scripts/make_agents.py` | A 600-agent roster and thousands of real generated conversations |
| Buyer brief | `src/brief.py` | What the consultant reads before dialling |
| WhatsApp adapter | `src/whatsapp.py` | Cloud API payloads: buttons, list messages, unit cards |
| Service | `src/app.py` | Meta webhook + the dashboard's read API |
| Dashboard | `dashboard/index.html` | Floor + leadership view (see below) |
| Sample output | `samples/` | Transcript and brief for every branch |

## Locations

The market is segmented the way brokerages segment their floors — **Cairo East, Cairo
West, North Coast** — with compounds inside each:

| Region | Compounds |
|---|---|
| Cairo East | New Cairo, Mostakbal City, New Capital, Madinaty |
| Cairo West | Sheikh Zayed, 6th October, Zayed North |
| North Coast | Sidi Abdel Rahman, Ras El Hekma, New Alamein |

The bot asks for the region, then offers the compounds inside it (or "anywhere in
Cairo East", which keeps the whole region in play). Every dashboard rollup aggregates
to the three regions; leads are assigned to an agent working that region. Investors
are never asked — the script routes them on returns, so they appear as their own row
in the breakdown rather than being dropped from it.

## The conversation

Every node in Part B of the script has a matching node id in `src/flow.py`, so the
code can be read against the doc:

```
N0    entry & opt-in            N3     budget → payment → down payment → structure
N0_5  returning buyer           N4     timeline
N1    buyer type (the fork)     N5     qualification checkpoint (internal)
N2…   area, type, beds,         N6     recommendation carousel (+ no-match / widen / alert)
      status, delivery          N7     upsell / next-best unit
N2I…  investor goal & units     N8     call to action + CRM lead write
N_BROWSE  browsing → nurture    N9     handoff by rotation, with the transcript
N_BROKER  → Broker Bot          N10    re-engagement    N11  fallbacks & human escalation
```

A run looks like this (`samples/transcript_enduser_hot.md` has the full version):

```
BOT  Which area are you interested in?
     [New Cairo] [Sheikh Zayed] [North Coast] [6th October] [Not sure yet]
YOU  somewhere near the coast
BOT  And what type of home?
...
BOT  Based on what you told me, here are your best matches — swipe through:
     ┌ Palm Ridge · 3-bed apartment
     │ 165 m² · New Cairo · primary
     │ 9.2M EGP · 10% down, ~144K/qtr (quarterly, backloaded)
     └ [View details] [Floor plan] [Send brochure] [Book a viewing]
BOT  For about 34K EGP/month more you could move up to a 4-bed duplex (240 m²)
     at Palm Ridge — want to see it?
```

**Scoring is internal and never shown to the buyer.** Budget defined *and realistic for
the chosen area and type* +2 (checked against live inventory, not assumed), payment
readiness +2 (financing-dependent +1), timeline +3/+2/+1/0, completed flow +1, investor
with a defined budget +1. 0–2 Cold → nurture, 3–5 Warm → follow-up, 6+ Hot → immediate
handoff.

## The buyer brief

The product's actual output. `python main.py brief L-234567`:

```
# Buyer brief — Hana Mostafa
**HOT · score 8** · Immediate handoff to consultant via rotation with full transcript.
- Looking for: 3-bed apartment in New Cairo
- Budget: 7-12M · Payment: Installments — 15% down, over 8 years, quarterly, backloaded
- Timeline: ASAP — within a month · Asked for: Book a viewing

## Lead the call with
1. Timeline is inside a month — lead with availability, not options.
2. Comfortable at 15% down — anchor on quarterly instalment, not headline price.
3. Engaged most with Palm Ridge NC-DUP-044 — start there.
```

## The dashboard

`python main.py serve`, then <http://localhost:8000>. Two audiences, one page:

- **The floor** — a searchable, filterable, paginated lead queue with band chips for
  routing, and the full brief (talking points, captured profile, shortlist, score
  breakdown, transcript) one click away.
- **Leadership** — the conversation funnel, weekly volume, demand by region, team
  performance across the floor, source attribution by campaign, and the
  profiled-vs-control conversion panel the pilot is sold on.

**It is built for a floor of thousands, and the demo data is generated at that size**
so the design is tested against it rather than against seven rows. `python main.py
scale` runs 3,000 real conversations across a 632-agent roster (`data/agents.json`,
regenerate with `scripts/make_agents.py --agents 1500`). At that volume:

- aggregates are computed over every lead; the queue is a paginated window onto them
- the queue filters by region, band and buyer type, and searches name, number,
  campaign, consultant, team and compound
- the floor view rolls up to teams — a thousand-row agent list is not a view — and
  leads are assigned inside the buyer's own region to whoever is carrying least

The dashboard reads `/api/snapshot` and `/api/leads` when the service is running, and
falls back to a snapshot embedded in the file, so `dashboard/index.html` also opens
straight from disk. The embedded copy carries the aggregates for every lead plus the
150 most recent rows.

## Running UAT

**[docs/UAT.md](docs/UAT.md) is the runbook** — Meta prerequisites, deployment, and 13
test cases with expected results. The short version:

*Internal UAT, no Meta account needed:* clone, `pip install -r requirements.txt`,
`python main.py demo`, `python main.py serve`. Testers use `python main.py chat` and read
the briefs in the dashboard.

*WhatsApp UAT:* nothing in the flow changes — only the adapter and where it runs.

```bash
cp .env.example .env        # WHATSAPP_TOKEN, PHONE_NUMBER_ID, VERIFY_TOKEN, APP_SECRET
docker compose up -d --build
python scripts/smoke.py https://<your-host>   # drives a whole buyer through the webhook
```

Point the Meta webhook at `POST /webhook` with your verify token and subscribe to
`messages`. A Cloud API **test number** is enough for UAT — no business verification or
template approval needed until real ad traffic. With no token set, `WhatsAppClient` prints
the payloads it would send instead of sending them, which is how the demo runs offline.

Set `WHATSAPP_APP_SECRET` on anything reachable from the internet: it turns on
`X-Hub-Signature-256` verification, without which anyone can post fake leads at the
webhook. `GET /health` reports whether it is on.

## What is real and what is stubbed

**Real:** the whole conversation graph, free-text understanding, scoring, inventory
ranking and upsell logic, profile persistence and returning-buyer retrieval, lead writes,
consultant rotation, event telemetry, the Cloud API payload mapping, and every metric on
the dashboard.

**Stubbed, deliberately:**

- `data/inventory.json` — illustrative sample stock; projects, developers and prices are
  placeholders shaped like Egyptian primary/resale inventory. Replace with a live feed.
- `data/agents.json` — a synthetic roster. Names are generated; the shape (pods of ~22
  under a team leader, split across the three regions) matches a Tier 2 floor.
- The generated conversations in `src/scale.py` sample *answers*, not results: every
  score, shortlist and brief is computed by the same engine a live buyer drives.
- `src/crm.py` — a JSON file stands in for the brokerage's CRM. Swapping in a real one
  means reimplementing that class, not touching the flow.
- Pilot outcome rates in the A/B panel (`seed_pilot_outcomes`) are simulated, since a
  prototype cannot observe real closings. During a pilot these rows come from the
  brokerage's own CRM export; the dashboard reads the same table either way.
- Unit photos and floor plans are referenced by path, not shipped.

## Next iteration

1. **Arabic.** The prototype is English-first, as the script specifies. `src/nlu.py` is
   built so Egyptian Arabic and Franco-Arabic resolve through the same code path — the
   lexicon is the work, not the plumbing. Bot copy needs to move to a message catalogue.
2. **Real inventory ingestion**, so "budget realistic for this area and type" is checked
   against live stock and prices.
3. **CRM write-back adapter** per customer (the one-way write the strategy calls for).
4. **A/B assignment at the ad level**, so the control cohort is produced by the same
   campaign rather than seeded.

## Tests

```bash
python -m pytest tests/ -q      # 46 tests: scoring, matching, the flow, adapter, API, scale
```
