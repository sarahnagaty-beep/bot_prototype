"""
Funnel and pilot metrics for the dashboard.

Everything here is derived from the event log and the outcome table, so the
numbers on the dashboard trace back to something the bot actually did.

The A/B section is the strategy's core claim (§6.3.3): profiled leads against a
control cohort of unprofiled leads, on the metric the brokerage already tracks.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .crm import CRM
from .flow import AREA_LABELS

STAGES = ["lead", "contacted", "viewing", "reserved", "closed"]
STAGE_LABELS = {
    "lead": "Leads",
    "contacted": "Contacted",
    "viewing": "Viewing booked",
    "reserved": "Reserved",
    "closed": "Closed",
}


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def funnel(crm: CRM) -> list[dict[str, Any]]:
    """Conversation funnel: what happens between the ad click and the handoff."""
    events = crm.events()
    by_name: dict[str, set[str]] = defaultdict(set)
    for event in events:
        by_name[event["event"]].add(event["conversation_id"])

    started = len(by_name["conversation_started"])
    steps = [
        ("Conversations started", started),
        ("Opted in", len(by_name["opt_in"])),
        ("Qualified (flow completed)", len(by_name["qualified"])),
        ("Recommendations shown", len(by_name["recommended"] | by_name["recommended_widened"])),
        ("Action taken", len(by_name["lead_written"])),
        ("Handed to consultant", len(by_name["handoff"])),
    ]
    return [
        {"step": name, "count": count, "pct_of_started": _pct(count, started)}
        for name, count in steps
    ]


def band_split(crm: CRM) -> dict[str, int]:
    counts = Counter()
    for lead in crm.leads():
        counts[lead["profile"].get("band", "cold")] += 1
    return {band: counts.get(band, 0) for band in ("hot", "warm", "cold")}


def source_breakdown(crm: CRM) -> list[dict[str, Any]]:
    started: Counter = Counter()
    qualified: Counter = Counter()
    by_conversation: dict[str, str] = {}
    for event in crm.events():
        if event["event"] == "conversation_started":
            ref = event["payload"].get("source_ref") or event["payload"].get("source") or "direct"
            by_conversation[event["conversation_id"]] = ref
            started[ref] += 1
        elif event["event"] == "qualified":
            ref = by_conversation.get(event["conversation_id"], "direct")
            qualified[ref] += 1
    return sorted(
        (
            {
                "source": ref,
                "started": count,
                "qualified": qualified.get(ref, 0),
                "qualified_rate": _pct(qualified.get(ref, 0), count),
            }
            for ref, count in started.items()
        ),
        key=lambda row: -row["started"],
    )


def demand(crm: CRM) -> dict[str, list[dict[str, Any]]]:
    """What buyers are actually asking for - the data layer the brokerage lacked."""
    areas: Counter = Counter()
    budgets: Counter = Counter()
    types: Counter = Counter()
    timelines: Counter = Counter()
    for lead in crm.leads():
        profile = lead["profile"]
        for area in profile.get("preferred_areas") or []:
            areas[AREA_LABELS.get(area, area)] += 1
        if profile.get("budget_band"):
            budgets[profile["budget_band"]] += 1
        if profile.get("unit_type"):
            types[profile["unit_type"]] += 1
        if profile.get("timeline"):
            timelines[profile["timeline"].replace("_", " ")] += 1

    def rows(counter: Counter) -> list[dict[str, Any]]:
        return [{"label": k, "count": v} for k, v in counter.most_common()]

    return {
        "areas": rows(areas),
        "budgets": rows(budgets),
        "unit_types": rows(types),
        "timelines": rows(timelines),
    }


def ab_test(crm: CRM) -> dict[str, Any]:
    """Profiled vs control cohort on the brokerage's own conversion metric."""
    reached: dict[str, Counter] = {"profiled": Counter(), "control": Counter()}
    for outcome in crm.outcomes():
        cohort = outcome.get("cohort", "profiled")
        if cohort not in reached:
            continue
        stage = outcome.get("stage", "lead")
        if stage not in STAGES:
            continue
        # A lead that reached a stage also reached every stage before it.
        for earlier in STAGES[: STAGES.index(stage) + 1]:
            reached[cohort][earlier] += 1

    def cohort_rows(cohort: str) -> list[dict[str, Any]]:
        total = reached[cohort]["lead"]
        return [
            {
                "stage": stage,
                "label": STAGE_LABELS[stage],
                "count": reached[cohort][stage],
                "rate": _pct(reached[cohort][stage], total),
            }
            for stage in STAGES
        ]

    profiled_rate = _pct(reached["profiled"]["closed"], reached["profiled"]["lead"])
    control_rate = _pct(reached["control"]["closed"], reached["control"]["lead"])
    uplift_pp = round(profiled_rate - control_rate, 1)
    uplift_rel = round((profiled_rate / control_rate - 1) * 100, 1) if control_rate else 0.0

    return {
        "profiled": cohort_rows("profiled"),
        "control": cohort_rows("control"),
        "profiled_close_rate": profiled_rate,
        "control_close_rate": control_rate,
        "uplift_pp": uplift_pp,
        "uplift_relative": uplift_rel,
        "sample": {
            "profiled": reached["profiled"]["lead"],
            "control": reached["control"]["lead"],
        },
    }


def headline(crm: CRM) -> dict[str, Any]:
    steps = {row["step"]: row for row in funnel(crm)}
    started = steps["Conversations started"]["count"]
    qualified = steps["Qualified (flow completed)"]["count"]
    actions = steps["Action taken"]["count"]
    handoffs = steps["Handed to consultant"]["count"]
    bands = band_split(crm)
    ab = ab_test(crm)
    return {
        "conversations": started,
        "qualified": qualified,
        "qualified_rate": _pct(qualified, started),
        "actions": actions,
        "action_rate": _pct(actions, started),
        "handoffs": handoffs,
        "hot_leads": bands["hot"],
        "bands": bands,
        "uplift_pp": ab["uplift_pp"],
        "uplift_relative": ab["uplift_relative"],
        "profiled_close_rate": ab["profiled_close_rate"],
        "control_close_rate": ab["control_close_rate"],
    }


def lead_rows(crm: CRM) -> list[dict[str, Any]]:
    """Flat rows for the dashboard lead table."""
    rows = []
    for lead in crm.leads():
        profile = lead["profile"]
        rows.append(
            {
                "lead_id": lead["lead_id"],
                "conversation_id": lead["conversation_id"],
                "name": profile.get("name") or "Unnamed buyer",
                "wa_id": profile.get("wa_id", ""),
                "buyer_type": profile.get("buyer_type", ""),
                "areas": [AREA_LABELS.get(a, a) for a in profile.get("preferred_areas") or []],
                "unit_type": profile.get("unit_type", ""),
                "bedrooms": profile.get("bedrooms", ""),
                "budget": profile.get("budget_band", ""),
                "payment": profile.get("payment_type", ""),
                "timeline": profile.get("timeline", ""),
                "intent": profile.get("intent_action", ""),
                "score": profile.get("score", 0),
                "band": profile.get("band", "cold"),
                "consultant": profile.get("consultant", ""),
                "source": profile.get("source_ref") or profile.get("source", ""),
                "sessions": profile.get("sessions", 1),
                "created_at": lead.get("created_at", ""),
                "updated_at": lead.get("updated_at", lead.get("created_at", "")),
            }
        )
    rows.sort(key=lambda r: (-r["score"], r["name"]))
    return rows


def snapshot(crm: CRM) -> dict[str, Any]:
    """Everything the dashboard needs, in one payload."""
    return {
        "headline": headline(crm),
        "funnel": funnel(crm),
        "sources": source_breakdown(crm),
        "demand": demand(crm),
        "ab_test": ab_test(crm),
        "leads": lead_rows(crm),
    }
