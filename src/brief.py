"""
Buyer brief - what the agent gets before the first call.

This is the deliverable the strategy hangs on: the consultant opens WhatsApp or
the dashboard already knowing budget, zones, use case, payment preference and
concerns, so the buyer is never asked the same question twice.
"""

from __future__ import annotations

from typing import Any, Optional

from .models import AREA_LABELS, region_label
from .inventory import unit_card_lines
from .models import BuyerProfile, Unit
from .scoring import routing_for

TIMELINE_LABELS = {
    "asap": "ASAP - within a month",
    "1_3_months": "1-3 months",
    "3_6_months": "3-6 months",
    "exploring": "Just exploring",
}
INTENT_LABELS = {
    "viewing": "Book a viewing",
    "consultant": "Talk to a consultant",
    "brochure": "Send full brochure",
    "reserve": "Express interest / reserve",
}
BUYER_TYPE_LABELS = {"end_user": "End user", "investor": "Investor", "broker": "Broker"}
DELIVERY_LABELS = {"ready": "Ready to move", "off_plan": "Off-plan", "either": "Either"}
STATUS_LABELS = {"primary": "Primary", "resale": "Resale", "both": "Primary or resale"}
GOAL_LABELS = {
    "yield": "Rental yield", "appreciation": "Capital appreciation", "both": "Yield & appreciation",
}
UNITS_TARGET_LABELS = {"1": "1 unit", "2_3": "2-3 units", "portfolio": "Portfolio (4+)"}


def build_brief(
    profile: BuyerProfile,
    units: Optional[list[Unit]] = None,
    transcript: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    units = units or []
    by_id = {u.unit_id: u for u in units}
    shortlist = []
    for unit_id in profile.shortlisted_unit_ids:
        unit = by_id.get(unit_id)
        if not unit:
            continue
        lines = unit_card_lines(unit)
        shortlist.append(
            {
                "unit_id": unit.unit_id,
                "project": unit.project,
                "headline": f"{unit.bedrooms}-bed {unit.unit_type}, {unit.size_sqm} m²",
                "area": AREA_LABELS.get(unit.area, unit.area),
                "price": lines["price"],
                "payment": lines["payment"],
                "delivery": lines["delivery"],
            }
        )

    return {
        "buyer": {
            "name": profile.name or "Unnamed buyer",
            "whatsapp": profile.wa_id,
            "language": profile.language,
            "source": profile.source,
            "source_ref": profile.source_ref,
            "first_seen": profile.entry_at,
            "sessions": profile.sessions,
        },
        "qualification": {
            "buyer_type": BUYER_TYPE_LABELS.get(profile.buyer_type, profile.buyer_type),
            "region": region_label(profile.region) or "Open",
            "areas": [AREA_LABELS.get(a, a) for a in profile.preferred_areas]
            or ([f"Anywhere in {region_label(profile.region)}"] if profile.region else ["Open"]),
            "unit_type": profile.unit_type or "Open",
            "bedrooms": profile.bedrooms or "Open",
            "property_status": STATUS_LABELS.get(
                profile.property_status, profile.property_status or "Open"
            ),
            "delivery": DELIVERY_LABELS.get(
                profile.delivery_preference, profile.delivery_preference or "Open"
            ),
            "investor_goal": GOAL_LABELS.get(profile.investor_goal, profile.investor_goal),
            "units_target": UNITS_TARGET_LABELS.get(profile.units_target, profile.units_target),
            "budget": profile.budget_band or "Not stated",
            "payment": _payment_line(profile),
            "timeline": TIMELINE_LABELS.get(profile.timeline, profile.timeline or "Not stated"),
            "intent": INTENT_LABELS.get(profile.intent_action, profile.intent_action or "None yet"),
            "looking_for": _looking_for(profile),
        },
        "scoring": {
            "score": profile.score,
            "band": profile.band,
            "routing": routing_for(profile.band),
            "breakdown": profile.score_breakdown,
        },
        "shortlist": shortlist,
        "selected_unit_id": profile.selected_unit_id,
        "concerns": profile.concerns,
        "consultant": profile.consultant,
        "talking_points": talking_points(profile, by_id),
        "transcript": transcript or [],
    }


def _looking_for(profile: BuyerProfile) -> str:
    """One line for the top of the brief. Investors skip layout questions in the
    script, so an open brief says so rather than printing 'Open-bed Open'."""
    bits = []
    if profile.bedrooms:
        bits.append(f"{profile.bedrooms}-bed")
    if profile.unit_type:
        bits.append(profile.unit_type)
    where = ", ".join(AREA_LABELS.get(a, a) for a in profile.preferred_areas) or region_label(
        profile.region
    )
    if not bits:
        if profile.buyer_type == "investor":
            return f"Open brief - matched on returns{', ' + where if where else ''}"
        return f"Not specified{' · ' + where if where else ''}"
    return " ".join(bits) + (f" in {where}" if where else " · area open")


def _payment_line(profile: BuyerProfile) -> str:
    if profile.payment_type == "cash":
        return "Cash - flag discount eligibility"
    if profile.payment_type == "installments":
        bits = []
        if profile.down_payment_pct:
            bits.append(f"{profile.down_payment_pct}% down")
        if profile.tenor_years:
            bits.append(f"over {profile.tenor_years} years")
        if profile.payment_frequency:
            bits.append(profile.payment_frequency)
        if profile.payment_structure:
            bits.append(profile.payment_structure)
        return "Installments - " + ", ".join(bits) if bits else "Installments"
    return "Not stated"


def talking_points(profile: BuyerProfile, by_id: dict[str, Unit]) -> list[str]:
    """The three or four things the agent should lead the call with."""
    points: list[str] = []
    if profile.sessions > 1:
        points.append(
            f"Returning buyer - {profile.sessions} sessions. Do not restart discovery; "
            "open by confirming what changed."
        )
    if profile.timeline == "asap":
        points.append("Timeline is inside a month - lead with availability, not options.")
    elif profile.timeline == "exploring":
        points.append("Still exploring - educate, do not push to reserve.")
    if profile.payment_type == "cash":
        points.append("Cash buyer - open with the cash discount and ready/resale stock.")
    elif profile.payment_type == "installments" and profile.down_payment_pct:
        points.append(
            f"Comfortable at {profile.down_payment_pct}% down - anchor on "
            f"{profile.payment_frequency or 'the'} instalment, not headline price."
        )
    if profile.buyer_type == "investor":
        goal = {"yield": "rental yield", "appreciation": "capital appreciation"}.get(
            profile.investor_goal, "yield and appreciation"
        )  # noqa: E501
        points.append(f"Investor led by {goal} - bring the numbers, skip the lifestyle pitch.")
    if profile.selected_unit_id and profile.selected_unit_id in by_id:
        unit = by_id[profile.selected_unit_id]
        points.append(f"Engaged most with {unit.project} {unit.unit_id} - start there.")
    for concern in profile.concerns:
        points.append(f"Raised: {concern}")
    return points


def render_markdown(brief: dict[str, Any]) -> str:
    """Mobile-readable brief - what the consultant sees before dialling."""
    b, q, s = brief["buyer"], brief["qualification"], brief["scoring"]
    out = [
        f"# Buyer brief - {b['name']}",
        "",
        f"**{s['band'].upper()} · score {s['score']}** · {s['routing']}",
        "",
        f"- WhatsApp: {b['whatsapp']}",
        f"- Source: {b['source']} ({b['source_ref'] or 'n/a'}) · sessions: {b['sessions']}",
        f"- Buyer type: {q['buyer_type']}",
        f"- Looking for: {q['looking_for']}",
        f"- Status / delivery: {q['property_status']} · {q['delivery']}",
        f"- Budget: {q['budget']} · Payment: {q['payment']}",
        f"- Timeline: {q['timeline']} · Asked for: {q['intent']}",
    ]
    if q["investor_goal"]:
        out.append(f"- Investor goal: {q['investor_goal']} · units: {q['units_target'] or '1'}")
    if brief["consultant"]:
        out.append(f"- Assigned to: {brief['consultant']}")

    if brief["talking_points"]:
        out += ["", "## Lead the call with", ""]
        out += [f"{i}. {p}" for i, p in enumerate(brief["talking_points"], 1)]

    if brief["shortlist"]:
        out += ["", "## Shortlist shown in chat", ""]
        for unit in brief["shortlist"]:
            marker = " ← engaged" if unit["unit_id"] == brief["selected_unit_id"] else ""
            out.append(
                f"- **{unit['project']}** {unit['unit_id']}{marker} · {unit['headline']} · "
                f"{unit['area']} · {unit['price']} · {unit['payment']} · {unit['delivery']}"
            )

    out += ["", "## Score breakdown (internal)", ""]
    for item in s["breakdown"]:
        out.append(f"- {item['signal']}: +{item['points']} ({item['note']})")

    if brief["transcript"]:
        out += ["", "## Transcript", ""]
        for entry in brief["transcript"]:
            who = "Bot" if entry["role"] == "bot" else "Buyer"
            text = entry["text"].replace("\n", "  \n  ")
            out.append(f"**{who}:** {text}")
            out.append("")
    return "\n".join(out)
