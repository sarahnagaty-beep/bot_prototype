"""
Demo data: one scripted conversation per branch of the script, plus an
illustrative pilot cohort so the dashboard's A/B panel has something to show.

Every conversation below runs through the real engine - nothing is faked. The
only synthetic data is the downstream outcome table (see seed_pilot_outcomes),
which in a live pilot would come from the brokerage's own CRM.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from .brief import build_brief, render_markdown
from .crm import CRM
from .flow import Engine
from .inventory import load_units
from .simulator import run_script, transcript_markdown

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

SCENARIOS: list[dict[str, Any]] = [
    {
        "key": "enduser_hot",
        "title": "End user · hot lead · books a viewing",
        "wa_id": "+201001234567",
        "name": "Hana Mostafa",
        "source_ref": "IG_LEADGEN_NEWCAIRO_Q3",
        "replies": [
            "Yes, let's go", "Buy to live in", "New Cairo", "Apartment", "3 bedrooms",
            "Primary", "Off-plan", "7-12M", "Installments", "15%", "Quarterly",
            "Backloaded", "ASAP", "Yes, show me", "Book a viewing",
        ],
    },
    {
        "key": "investor_hot",
        "title": "Investor · rental yield · handed to a consultant",
        "wa_id": "+201009876543",
        "name": "Omar Zaki",
        "source_ref": "FB_LEADGEN_INVEST_Q3",
        "replies": [
            "yes", "I want to invest", "rental yield", "2-3", "4-7M", "cash",
            "1-3 months", "yes, show me", "talk to a consultant",
        ],
    },
    {
        "key": "enduser_warm",
        "title": "End user · warm · brochure, nurtured not handed off",
        "wa_id": "+201112223344",
        "name": "Ahmed Sobhy",
        "source_ref": "IG_LEADGEN_OCTOBER_Q3",
        "replies": [
            "yes", "buy to live in", "6th October", "apartment", "3", "primary",
            "either", "4-7M", "installments", "10%", "monthly", "equal",
            "3-6 months", "no thanks", "get full brochure",
        ],
    },
    {
        "key": "freetext_natural",
        "title": "Free text · no buttons tapped · natural language throughout",
        "wa_id": "+201223334455",
        "name": "Sara Nabil",
        "source_ref": "WEBSITE_CHAT",
        "source": "website",
        "replies": [
            "sure", "it's for me and my family", "somewhere near the coast",
            "chalet", "2 bedrooms", "resale is fine", "ready to move",
            "around 6 million", "cash", "this month", "book a viewing",
        ],
    },
    {
        "key": "browsing_cold",
        "title": "Just browsing · cold · exits to nurture",
        "wa_id": "+201556667788",
        "name": "Dalia Hassan",
        "source_ref": "IG_LEADGEN_BRAND_Q3",
        "replies": ["I'm just browsing", "Yes, save them"],
    },
    {
        "key": "nomatch_alert",
        "title": "No exact match · search widened · alert registered",
        "wa_id": "+201667778899",
        "name": "Nada Kamal",
        "source_ref": "IG_LEADGEN_NORTHCOAST_Q3",
        "replies": [
            "yes", "buy to live in", "North Coast", "villa", "4+", "resale",
            "ready to move", "under 4M", "installments", "10%", "monthly",
            "equal", "just exploring", "widen the search", "get full brochure",
        ],
    },
    {
        "key": "fallback_human",
        "title": "Two low-confidence turns · escalated to a human",
        "wa_id": "+201778889900",
        "name": "Karim Elwy",
        "source_ref": "QR_BILLBOARD_RING_ROAD",
        "source": "qr",
        "replies": [
            "yes", "hmm", "not sure what you mean", "talk to a consultant",
        ],
    },
    {
        "key": "broker_routed",
        "title": "Broker · routed to the Broker Bot",
        "wa_id": "+201889990011",
        "name": "Mahmoud Fathy",
        "source_ref": "IG_LEADGEN_BRAND_Q3",
        "replies": ["yes", "I'm a broker"],
    },
    {
        "key": "after_hours",
        "title": "After hours · handoff promises a morning call",
        "wa_id": "+201990001122",
        "name": "Yara Selim",
        "source_ref": "FB_LEADGEN_ZAYED_Q3",
        "now_hour": 23,
        "replies": [
            "yes", "buy to live in", "Sheikh Zayed", "townhouse", "4+", "both",
            "either", "12-20M", "cash", "asap", "express interest",
        ],
    },
]

# Node 0.5: same buyer comes back a week later and resumes.
RETURNING = {
    "key": "returning_resume",
    "title": "Returning buyer · profile retrieved · no repeated questions",
    "wa_id": "+201001234567",
    "name": "Hana Mostafa",
    "source_ref": "IG_RETARGETING_Q3",
    "replies": ["Pick up", "no thanks", "express interest"],
}


def build_demo(crm: CRM, write_samples: bool = True, echo: bool = False) -> list[Engine]:
    """Run every scenario through the real engine and write sample artefacts."""
    units = load_units()
    engines: list[Engine] = []

    for scenario in SCENARIOS + [RETURNING]:
        engine = run_script(
            crm,
            wa_id=scenario["wa_id"],
            name=scenario["name"],
            replies=scenario["replies"],
            source=scenario.get("source", "meta_ad"),
            source_ref=scenario["source_ref"],
            now_hour=scenario.get("now_hour", 12),
            echo=echo,
            color=False,
            units=units,
        )
        engines.append(engine)

        if write_samples:
            SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
            key = scenario["key"]
            (SAMPLES_DIR / f"transcript_{key}.md").write_text(
                transcript_markdown(crm, engine, scenario["title"])
            )
            brief = build_brief(engine.profile, units, crm.transcript(engine.conversation_id))
            (SAMPLES_DIR / f"brief_{key}.md").write_text(render_markdown(brief))

    if write_samples:
        _write_brief_json(crm, engines[0], units)
    return engines


def _write_brief_json(crm: CRM, engine: Engine, units: list) -> None:
    import json

    brief = build_brief(engine.profile, units, crm.transcript(engine.conversation_id))
    (SAMPLES_DIR / "brief_enduser_hot.json").write_text(
        json.dumps(brief, indent=2, ensure_ascii=False)
    )


def seed_pilot_outcomes(
    crm: CRM, profiled: int = 180, control: int = 180, seed: int = 7
) -> None:
    """Illustrative downstream outcomes for the A/B panel.

    The prototype cannot observe real closings, so the pilot cohort is
    simulated with plausible stage-conversion rates: the profiled arm converts
    better because the agent calls with a brief, not cold. Replace this with the
    brokerage's CRM export during a real pilot - the dashboard reads the same
    outcome table either way.
    """
    rng = random.Random(seed)

    # stage -> probability of reaching it, given the previous stage.
    rates = {
        "profiled": {"contacted": 0.93, "viewing": 0.46, "reserved": 0.29, "closed": 0.60},
        "control": {"contacted": 0.81, "viewing": 0.33, "reserved": 0.22, "closed": 0.52},
    }

    def walk(cohort: str) -> str:
        stage = "lead"
        for nxt in ("contacted", "viewing", "reserved", "closed"):
            if rng.random() > rates[cohort][nxt]:
                return stage
            stage = nxt
        return stage

    for i in range(profiled):
        crm.record_outcome(f"P-{i:04d}", "profiled", walk("profiled"))
    for i in range(control):
        crm.record_outcome(f"C-{i:04d}", "control", walk("control"))

    # Real bot leads join the profiled arm at the stage they actually reached.
    for lead in crm.leads():
        profile = lead["profile"]
        if profile.get("consultant"):
            stage = "viewing" if profile.get("intent_action") == "viewing" else "contacted"
        elif profile.get("intent_action"):
            stage = "contacted"
        else:
            stage = "lead"
        crm.record_outcome(lead["lead_id"], "profiled", stage, {"real": True})
