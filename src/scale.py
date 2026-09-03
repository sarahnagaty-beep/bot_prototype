"""
Generate a floor-sized dataset by running real conversations.

Every lead here comes out of the same engine a live buyer would drive - the
answers are sampled, not the results. That matters: the scores, shortlists and
briefs on the dashboard at 3,000 leads are computed the same way they are at 3.

    python main.py scale --leads 3000 --days 90
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .crm import CRM
from .flow import Engine
from .inventory import load_units
from .models import REGIONS, Unit

# Weighted answer pools. Shares reflect a primary-market Cairo floor: most
# buyers are end users, Cairo East carries the most demand, and the coast skews
# to investors and chalets.
REGION_WEIGHTS = {"cairo_east": 0.44, "cairo_west": 0.34, "north_coast": 0.22}
BUYER_TYPE_WEIGHTS = {"end_user": 0.72, "investor": 0.24, "broker": 0.04}
TIMELINE_WEIGHTS = {"asap": 0.18, "1_3_months": 0.31, "3_6_months": 0.27, "exploring": 0.24}
BUDGET_WEIGHTS = {"under_4m": 0.11, "4_7m": 0.29, "7_12m": 0.32, "12_20m": 0.19, "20m_plus": 0.09}
PAYMENT_WEIGHTS = {"installments": 0.79, "cash": 0.21}
DOWN_WEIGHTS = {"dp_10": 0.44, "dp_15": 0.3, "dp_20": 0.18, "dp_unsure": 0.08}
STATUS_WEIGHTS = {"primary": 0.62, "resale": 0.13, "both": 0.25}
DELIVERY_WEIGHTS = {"off_plan": 0.45, "ready": 0.28, "either": 0.27}
INTENT_WEIGHTS = {"viewing": 0.36, "consultant": 0.22, "brochure": 0.29, "reserve": 0.13}

UNIT_TYPES_BY_REGION = {
    "cairo_east": {"apartment": 0.62, "duplex": 0.12, "townhouse": 0.15, "villa": 0.11},
    "cairo_west": {"apartment": 0.44, "townhouse": 0.22, "twinhouse": 0.16, "villa": 0.18},
    "north_coast": {"chalet": 0.78, "villa": 0.22},
}

# Not everyone finishes. Meta lead-gen traffic is cheap and shallow: a slice
# never opts in, a slice only browses, and each further question loses a few.
BROWSE_SHARE = 0.12
DROP_AFTER = {"opt_in": 0.11, "area": 0.08, "budget": 0.09, "timeline": 0.06}

FIRST = [
    "Ahmed", "Mohamed", "Omar", "Youssef", "Karim", "Tarek", "Hassan", "Amr", "Khaled",
    "Sherif", "Mostafa", "Hany", "Ziad", "Mariam", "Nour", "Salma", "Yara", "Dina",
    "Heba", "Rana", "Menna", "Aya", "Sara", "Nada", "Farida", "Hana", "Reem", "Injy",
]
LAST = [
    "Fahmy", "Adel", "Ibrahim", "Saleh", "Zaki", "Mostafa", "Sobhy", "Hassan", "Kamal",
    "Elwy", "Fathy", "Selim", "Nabil", "Refaat", "Shawky", "Gaber", "Aziz", "Rashad",
]
SOURCES = [
    ("IG_LEADGEN_CAIROEAST_Q3", 0.24), ("FB_LEADGEN_CAIROEAST_Q3", 0.14),
    ("IG_LEADGEN_CAIROWEST_Q3", 0.17), ("FB_LEADGEN_CAIROWEST_Q3", 0.09),
    ("IG_LEADGEN_NORTHCOAST_Q3", 0.15), ("FB_LEADGEN_INVEST_Q3", 0.07),
    ("IG_RETARGETING_Q3", 0.05), ("WEBSITE_CHAT", 0.06), ("QR_BILLBOARD_RING_ROAD", 0.03),
]


def _pick(rng: random.Random, weights: dict[str, float]) -> str:
    roll = rng.random()
    running = 0.0
    for key, weight in weights.items():
        running += weight
        if roll <= running:
            return key
    return list(weights)[-1]


def _answers(rng: random.Random, region: str, buyer_type: str) -> list[str]:
    """Build one buyer's replies, stopping early if they drop out."""
    if rng.random() < BROWSE_SHARE:
        return ["browsing", "yes"]  # featured projects, then saved for nurture

    replies = ["yes"]
    if rng.random() < DROP_AFTER["opt_in"]:
        return replies

    replies.append(buyer_type)
    if buyer_type == "broker":
        return replies

    if buyer_type == "investor":
        replies += [_pick(rng, {"appreciation": 0.4, "yield": 0.42, "both": 0.18}),
                    _pick(rng, {"1": 0.55, "2_3": 0.32, "portfolio": 0.13})]
    else:
        replies.append(region)
        areas = REGIONS[region]["areas"]
        # Most buyers name a compound; the rest keep the whole region open.
        replies.append("any" if rng.random() < 0.28 else rng.choice(areas))
        if rng.random() < DROP_AFTER["area"]:
            return replies
        replies += [
            _pick(rng, UNIT_TYPES_BY_REGION[region]),
            _pick(rng, {"1": 0.05, "2": 0.28, "3": 0.44, "4+": 0.23}),
            _pick(rng, STATUS_WEIGHTS),
            _pick(rng, DELIVERY_WEIGHTS),
        ]

    replies.append(_pick(rng, BUDGET_WEIGHTS))
    if rng.random() < DROP_AFTER["budget"]:
        return replies

    payment = _pick(rng, PAYMENT_WEIGHTS)
    replies.append(payment)
    if payment == "installments":
        replies += [
            _pick(rng, DOWN_WEIGHTS),
            _pick(rng, {"monthly": 0.63, "quarterly": 0.37}),
            _pick(rng, {"equal": 0.58, "backloaded": 0.42}),
        ]

    replies.append(_pick(rng, TIMELINE_WEIGHTS))
    if rng.random() < DROP_AFTER["timeline"]:
        return replies

    replies.append(_pick(rng, {"yes": 0.34, "no": 0.66}))   # the upsell
    replies.append(_pick(rng, INTENT_WEIGHTS))
    return replies


def _backdate(crm: CRM, conversation_id: str, wa_id: str, when: datetime) -> None:
    """Spread the data over real dates so trends and cohorts mean something."""
    stamp = when.isoformat(timespec="seconds")
    profile = crm._data["profiles"].get(wa_id)
    if profile:
        profile["entry_at"] = stamp
        profile["updated_at"] = stamp
    for lead in crm._data["leads"]:
        if lead["conversation_id"] == conversation_id:
            lead["created_at"] = stamp
            lead.pop("updated_at", None)
    for event in crm._data["events"]:
        if event["conversation_id"] == conversation_id:
            event["at"] = stamp
    for turn in crm._data["transcripts"].get(conversation_id, []):
        turn["at"] = stamp


def generate(
    crm: CRM,
    leads: int = 3000,
    days: int = 90,
    seed: int = 3,
    units: Optional[list[Unit]] = None,
    progress: bool = True,
) -> dict[str, Any]:
    """Run `leads` conversations spread over the last `days` days."""
    rng = random.Random(seed)
    units = units if units is not None else load_units()
    now = datetime.now(timezone.utc)
    counts: dict[str, int] = {}

    with crm.bulk():
        for index in range(leads):
            region = _pick(rng, REGION_WEIGHTS)
            buyer_type = _pick(rng, BUYER_TYPE_WEIGHTS)
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            wa_id = f"+2010{rng.randint(10000000, 99999999)}"
            source_ref = _pick(rng, dict(SOURCES))
            source = "website" if source_ref == "WEBSITE_CHAT" else (
                "qr" if source_ref.startswith("QR") else "meta_ad"
            )
            # Volume rises toward the present, as a campaign scales: ages
            # cluster near zero, so recent weeks carry more leads.
            age_days = days * (rng.random() ** 1.7)
            when = now - timedelta(days=age_days, minutes=rng.randint(0, 1439))

            engine = Engine.for_buyer(
                crm, wa_id=wa_id, name=name, source=source, source_ref=source_ref,
                now_hour=when.hour, units=units,
            )
            engine.start()
            for reply in _answers(rng, region, buyer_type):
                if engine.ended:
                    break
                engine.handle(reply)

            _backdate(crm, engine.conversation_id, wa_id, when)
            counts[engine.profile.band] = counts.get(engine.profile.band, 0) + 1

            if progress and (index + 1) % 500 == 0:
                print(f"  {index + 1}/{leads} conversations")

    return {"leads": leads, "bands": counts}
