# UAT Runbook — Buyer Bot Prototype

Two tracks. **Track A** needs nothing but a laptop and proves the conversation,
scoring, briefs and dashboard. **Track B** puts it on real WhatsApp for testers'
own phones. Run A first — every defect it finds is cheaper to fix before Meta is
involved.

---

## Track A — Internal UAT (no Meta account, ~10 minutes)

```bash
git clone https://github.com/sarahnagaty-beep/bot_prototype.git
cd bot_prototype
git checkout claude/buyer-whatsapp-ai-bot-7l201r

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python main.py demo      # seeds sample conversations
python -m pytest -q      # 43 tests should pass
python main.py serve     # dashboard at http://localhost:8000
```

Testers then:

- **Talk to it:** `python main.py chat --name "Your Name"` — a WhatsApp conversation
  in the terminal, same engine the webhook uses.
- **Read the output:** open the dashboard, click any lead, check the brief matches
  what was said in the chat.
- **Check the branches:** `samples/` has a transcript and brief for all ten paths.

This is the track to use for sales-floor sign-off: consultants read the briefs and
answer one question — *would this have made your call better?*

---

## Track B — WhatsApp UAT

### 1. Meta prerequisites (Views' side, not code)

| Need | Where | Notes |
|---|---|---|
| Meta Business account | business.facebook.com | Views' existing one is fine |
| WhatsApp Business app | developers.facebook.com → Create App → Business | |
| Test phone number | App → WhatsApp → API Setup | Free, sends to 5 whitelisted testers |
| Tester numbers | Same screen → "To" → Manage phone number list | Add each tester before they message |
| Permanent token | Business Settings → System Users → generate token | The 24-hour dev token will expire mid-UAT |
| App Secret | App Settings → Basic | Required on any public host |

A **test number is enough for UAT.** Only move to a real number (business
verification, display-name review, template approval) when the pilot goes live to
actual ad traffic.

### 2. Configure

```bash
cp .env.example .env
# fill in WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID,
# WHATSAPP_VERIFY_TOKEN (any string you choose), WHATSAPP_APP_SECRET
```

### 3. Get it on a public HTTPS URL

Meta will only call an HTTPS endpoint it can reach.

**Quickest — laptop + tunnel** (good for a first live test, not for a week of UAT;
the URL changes each restart):

```bash
python main.py serve --host 0.0.0.0
ngrok http 8000            # use the https URL it prints
```

**Steadier — a small host** (any VM, Render, Railway, Fly.io, or Views' own
infrastructure). Docker is included:

```bash
docker compose up -d --build
curl https://<your-host>/health
```

`data/` is mounted as a volume, so leads, transcripts and half-finished
conversations survive restarts.

### 4. Point Meta at it

App → WhatsApp → Configuration → Edit webhook:

- **Callback URL:** `https://<your-host>/webhook`
- **Verify token:** the `WHATSAPP_VERIFY_TOKEN` you set
- **Subscribe to:** the `messages` field

Meta calls `GET /webhook` immediately; a green tick means the handshake passed.

### 5. Verify before handing out the number

```bash
python scripts/smoke.py https://<your-host>
```

It drives a whole buyer through the real webhook and checks a hot lead, a
shortlist and a brief come out the other end. It also prints whether signature
checking is on — **if it says OFF on a public host, set `WHATSAPP_APP_SECRET`
before testers start**, or anyone can post fake leads at you.

Then message the test number from a whitelisted phone and check the reply arrives.

---

## Test cases

Each maps to a scripted scenario in `src/demo.py`, so expected behaviour is
verifiable against `samples/`.

| # | Case | Steps | Expected |
|---|---|---|---|
| 1 | Hot end user | "yes" → live in → New Cairo → apartment → 3 → primary → off-plan → 7-12M → installments → 15% → quarterly → backloaded → ASAP → book a viewing | 5 matching units shown; upsell offered as a monthly delta; band **hot**, score 8; consultant named; lead in dashboard |
| 2 | Investor | "yes" → invest → rental yield → 2-3 → 4-7M → cash → 1-3 months | No layout questions asked; ready-to-move, high-yield stock ranked first; upsell leads with yield |
| 3 | Free text only | Never tap a button. "it's for me and my family", "somewhere near the coast", "around 6 million", "this month" | Every answer understood; no "didn't catch that" |
| 4 | Just browsing | "I'm just browsing" | 3 featured projects; offer to save preferences; **no** consultant handoff |
| 5 | Broker | "yes" → "I'm a broker" | Routed to Broker Bot, buyer flow ends |
| 6 | No match | villa, North Coast, resale, ready, under 4M | Offers to widen or alert; widening never swaps in a different home type; ends with an alert registered |
| 7 | Confusion → human | "yes" then "hmm" then "no idea what you're asking" | One re-prompt, then offer of a consultant on the second miss |
| 8 | Returning buyer | Complete case 1, then message again the next day | "Welcome back… last time you were looking at…"; **no repeated questions**; resumes at recommendations |
| 9 | Opt-out | "STOP" at any point | Confirms opt-out, conversation ends, no further messages |
| 10 | After hours | Complete a hot flow outside 09:00–21:00 | Handoff promises a morning call instead of a 2-hour callback |
| 11 | Restart resilience | Mid-conversation, restart the service, then reply | Continues from the same question — does not start over |
| 12 | Duplicate delivery | Poor signal / Meta retry | Buyer is answered once, never twice |
| 13 | Brief quality | Open any hot lead in the dashboard | Talking points, captured profile, shortlist, score breakdown and full transcript all match the chat |

### What to record

For each case: tester, phone, date, **pass / fail**, and for failures the buyer's
exact words plus the bot's reply. Wording complaints matter as much as breakage —
the copy is placeholder and tone-configurable, so "this sounds wrong in Egypt" is a
valid, expected finding.

Every conversation is already stored: `data/store.json` holds transcripts, and any
lead's full history is one click away in the dashboard.

---

## Known limits during UAT

- **English only.** Arabic and Franco-Arabic are the next iteration. Testers will
  type Arabic — log what they type, it is the lexicon input for the next build.
- **Sample inventory.** `data/inventory.json` is illustrative, not Views' stock.
  Recommendations are structurally right, commercially fictional.
- **Simulated pilot outcomes.** The profiled-vs-control panel is seeded. Real
  conversion numbers need the CRM export from a live cohort.
- **24-hour window.** WhatsApp only allows free-form replies within 24 hours of the
  buyer's last message. The Node 10 nurture follow-ups need approved message
  templates before they can actually send.
- **Single instance.** Session state is a JSON file. Fine for UAT; a pilot on real
  ad volume needs a database.

## Exit criteria

UAT is done when, on the test number:

1. All 13 cases pass, cases 1, 3, 8 and 11 on real phones.
2. Five consultants have read a brief for a lead they did not handle and agreed it
   would have improved the call.
3. Scoring bands have been reviewed against real judgement — the floor agrees the
   hot leads are hot. **Expect to retune the weights here; that is what UAT is for.**
4. No conversation ends with the buyer stuck, unanswered, or asked something they
   already answered.
