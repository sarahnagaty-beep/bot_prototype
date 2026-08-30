"""
Qualification scoring - Part A, §3 of the conversation prototype.

Internal only: the score and band are never shown to the buyer. The band
governs routing (nurture / recommend+follow-up / immediate handoff).
"""

from __future__ import annotations

from typing import Any, Optional

from .models import BuyerProfile

BANDS = [
    ("cold", 0, 2, "Nurture: featured projects, re-engage later. No handoff."),
    ("warm", 3, 5, "Recommend units, push to viewing/brochure. Consultant follow-up."),
    ("hot", 6, 99, "Immediate handoff to consultant via rotation with full transcript."),
]

TIMELINE_POINTS = {"asap": 3, "1_3_months": 2, "3_6_months": 1, "exploring": 0}
COMFORTABLE_DOWN_PAYMENT_PCT = 15


def band_for(score: int) -> str:
    for name, low, high, _ in BANDS:
        if low <= score <= high:
            return name
    return "cold"


def routing_for(band: str) -> str:
    for name, _, _, routing in BANDS:
        if name == band:
            return routing
    return ""


def score_profile(
    profile: BuyerProfile,
    budget_is_realistic: Optional[bool] = None,
) -> tuple[int, str, list[dict[str, Any]]]:
    """Return (score, band, breakdown).

    `budget_is_realistic` comes from the inventory layer: a defined budget that
    cannot buy anything in the chosen area/type is not a qualified budget.
    """
    breakdown: list[dict[str, Any]] = []

    def add(signal: str, points: int, note: str) -> None:
        breakdown.append({"signal": signal, "points": points, "note": note})

    # 1. Budget defined & realistic for chosen area/type (+2), vague/absent 0.
    if profile.budget_band and budget_is_realistic is not False:
        add("Budget defined & realistic", 2, f"{profile.budget_band} fits available stock")
    elif profile.budget_band:
        add("Budget defined but below entry price", 0, "Offer plan or next-best alternative")
    else:
        add("Budget vague or absent", 0, "No budget captured")

    # 2. Payment readiness: cash or comfortable down payment (+2), financed (+1).
    if profile.payment_type == "cash":
        add("Cash buyer", 2, "Flag discount eligibility")
    elif profile.payment_type == "installments":
        dp = profile.down_payment_pct
        if dp is not None and dp >= COMFORTABLE_DOWN_PAYMENT_PCT:
            add("Comfortable down payment", 2, f"{dp}% down")
        else:
            add("Financing-dependent", 1, f"{dp}% down" if dp is not None else "down payment unclear")

    # 3. Timeline.
    if profile.timeline:
        pts = TIMELINE_POINTS.get(profile.timeline, 0)
        add("Timeline", pts, profile.timeline.replace("_", " "))

    # 4. Completed the qualification flow.
    if profile.completed_flow:
        add("Completed qualification flow", 1, "Engagement signal")

    # 5. Investor with a defined budget converts faster.
    if profile.buyer_type == "investor" and profile.budget_band:
        add("Investor with defined budget", 1, "Tends to convert faster")

    score = sum(item["points"] for item in breakdown)
    return score, band_for(score), breakdown


def apply_score(profile: BuyerProfile, budget_is_realistic: Optional[bool] = None) -> BuyerProfile:
    profile.score, profile.band, profile.score_breakdown = score_profile(profile, budget_is_realistic)
    profile.touch()
    return profile
