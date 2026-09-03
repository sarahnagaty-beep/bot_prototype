"""
Inventory matching - Node 6 (recommendation), Node 7 (upsell / next best).

Hard filters come from the captured profile; ranking is a weighted fit score so
a near-miss is surfaced rather than dropped (Part A, §4: budget fit).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .models import BuyerProfile, Unit

DEFAULT_INVENTORY = Path(__file__).resolve().parent.parent / "data" / "inventory.json"


def load_units(path: Path | str = DEFAULT_INVENTORY) -> list[Unit]:
    raw = json.loads(Path(path).read_text())
    return [Unit.from_dict(u) for u in raw["units"]]


def _budget_fit(unit: Unit, profile: BuyerProfile) -> Optional[float]:
    """1.0 inside budget, tapering to 0 up to 15% over. None = out of range."""
    low, high = profile.budget_min, profile.budget_max
    if high is None and low is None:
        return 0.5
    if low is not None and unit.price_egp < low * 0.8:
        return 0.4  # under-budget stock still matches, just less well
    if high is None:
        return 1.0
    if unit.price_egp <= high:
        return 1.0
    stretch = (unit.price_egp - high) / high
    return max(0.0, 1.0 - stretch / 0.15) if stretch <= 0.15 else None


def fit_score(unit: Unit, profile: BuyerProfile) -> Optional[float]:
    """Weighted fit in [0, 1]; None if the unit is disqualified outright."""
    score, weight_total = 0.0, 0.0

    def weigh(value: float, weight: float) -> None:
        nonlocal score, weight_total
        score += value * weight
        weight_total += weight

    budget = _budget_fit(unit, profile)
    if budget is None:
        return None
    weigh(budget, 3.0)

    if profile.preferred_areas:
        weigh(1.0 if unit.area in profile.preferred_areas else 0.0, 2.5)
    elif profile.region:
        weigh(1.0 if unit.region == profile.region else 0.0, 2.5)
    if profile.unit_type:
        weigh(1.0 if unit.unit_type == profile.unit_type else 0.3, 2.0)
    if profile.bedrooms:
        want = 4 if profile.bedrooms == "4+" else int(profile.bedrooms)
        exact = unit.bedrooms >= 4 if profile.bedrooms == "4+" else unit.bedrooms == want
        weigh(1.0 if exact else max(0.0, 1 - abs(unit.bedrooms - want) * 0.4), 1.5)
    if profile.property_status and profile.property_status != "both":
        weigh(1.0 if unit.property_status == profile.property_status else 0.0, 1.5)
    if profile.delivery_preference and profile.delivery_preference != "either":
        weigh(1.0 if unit.delivery == profile.delivery_preference else 0.2, 1.2)

    # Investor lens (Node 2i logic).
    if profile.buyer_type == "investor":
        if profile.investor_goal in ("yield", "both"):
            weigh(min(unit.rental_yield_pct / 10.0, 1.0), 1.5)
            weigh(1.0 if unit.delivery == "ready" else 0.4, 0.8)
        if profile.investor_goal in ("appreciation", "both"):
            weigh(min(unit.appreciation_pct / 20.0, 1.0), 1.5)
            weigh(1.0 if unit.delivery == "off_plan" else 0.4, 0.8)

    # Cash buyers: resale/ready sellers are where the discount lives.
    if profile.payment_type == "cash":
        weigh(1.0 if unit.delivery == "ready" else 0.5, 0.6)

    return score / weight_total if weight_total else 0.0


def satisfies_hard_filters(unit: Unit, profile: BuyerProfile) -> bool:
    """Stated location / status / delivery preferences are filters, not tiebreakers.

    A buyer who named compounds is filtered on those; one who only picked a
    region is filtered on the whole region.
    """
    if profile.preferred_areas:
        if unit.area not in profile.preferred_areas:
            return False
    elif profile.region and unit.region != profile.region:
        return False
    if profile.property_status and profile.property_status != "both":
        if unit.property_status != profile.property_status:
            return False
    if profile.delivery_preference and profile.delivery_preference != "either":
        if unit.delivery != profile.delivery_preference:
            return False
    return True


def recommend(
    profile: BuyerProfile, units: Optional[list[Unit]] = None, limit: int = 5
) -> list[tuple[Unit, float]]:
    """Top matches: everything the buyer asked for first, near-misses only to fill."""
    units = units if units is not None else load_units()
    strict: list[tuple[Unit, float]] = []
    loose: list[tuple[Unit, float]] = []
    for unit in units:
        fit = fit_score(unit, profile)
        if fit is None:
            continue
        (strict if satisfies_hard_filters(unit, profile) else loose).append((unit, fit))

    for bucket in (strict, loose):
        bucket.sort(key=lambda pair: (-pair[1], pair[0].price_egp))
    return (strict + loose)[:limit]


def widen(profile: BuyerProfile, units: Optional[list[Unit]] = None, limit: int = 3) -> list[tuple[Unit, float]]:
    """Node 6 no-match branch: relax area, status, delivery and budget - but keep
    the home type the buyer asked for. Showing a 2-bed apartment to someone who
    asked for a 4-bed villa is worse than admitting there is no match, so an
    empty result here routes to the alert branch instead."""
    units = units if units is not None else load_units()
    relaxed = BuyerProfile.from_dict(profile.to_dict())
    relaxed.preferred_areas = []
    relaxed.region = ""
    relaxed.delivery_preference = "either"
    relaxed.property_status = "both"
    if relaxed.budget_max:
        relaxed.budget_max = int(relaxed.budget_max * 1.5)

    matches = [
        (unit, fit)
        for unit, fit in recommend(relaxed, units, limit=len(units))
        if not profile.unit_type or unit.unit_type == profile.unit_type
    ]
    return matches[:limit]


def next_best(
    profile: BuyerProfile, shortlist: list[str], units: Optional[list[Unit]] = None
) -> Optional[tuple[Unit, str]]:
    """Node 7: the one upsell worth making, with the reason to make it."""
    units = units if units is not None else load_units()
    top_price = max(
        (u.price_egp for u in units if u.unit_id in shortlist), default=profile.budget_max or 0
    )
    if not top_price:
        return None

    candidates = []
    for unit in units:
        if unit.unit_id in shortlist:
            continue
        if not (top_price < unit.price_egp <= top_price * 1.25):
            continue
        if profile.preferred_areas and unit.area not in profile.preferred_areas:
            continue
        if not profile.preferred_areas and profile.region and unit.region != profile.region:
            continue
        candidates.append(unit)
    if not candidates:
        return None

    if profile.buyer_type == "investor":
        pick = max(
            candidates,
            key=lambda u: u.rental_yield_pct
            if profile.investor_goal == "yield"
            else u.appreciation_pct,
        )
        metric = (
            f"{pick.rental_yield_pct}% gross yield"
            if profile.investor_goal == "yield"
            else f"~{pick.appreciation_pct}% projected appreciation"
        )
        reason = (
            f"a higher-tier unit at {metric}"
            + (" with off-plan launch pricing" if pick.delivery == "off_plan" else "")
        )
        return pick, reason

    pick = min(candidates, key=lambda u: u.price_egp)
    # An end user compares instalments, not headline price (Node 7 copy).
    if profile.payment_type == "installments" and pick.installment_egp:
        gap = _monthly(pick) - _shortlist_monthly(shortlist, units)
        if gap > 0:
            return pick, f"about {round(gap / 1000):,}K EGP/month more"
    price_gap = (pick.price_egp - top_price) / 1_000_000
    return pick, f"about {price_gap:.1f}M EGP more"


def _monthly(unit: Unit) -> float:
    """Instalment normalised to a monthly figure, whatever its frequency."""
    if not unit.installment_egp:
        return 0.0
    return unit.installment_egp / (3 if unit.payment_frequency == "quarterly" else 1)


def _shortlist_monthly(shortlist: list[str], units: list[Unit]) -> float:
    """Compare against the best-fit unit shown (rank 1), not the priciest."""
    by_id = {u.unit_id: u for u in units}
    for unit_id in shortlist:
        unit = by_id.get(unit_id)
        if unit and unit.installment_egp:
            return _monthly(unit)
    return 0.0


def budget_is_realistic(profile: BuyerProfile, units: Optional[list[Unit]] = None) -> Optional[bool]:
    """Feeds the +2 'budget defined & realistic' scoring signal."""
    if not profile.budget_band:
        return None
    units = units if units is not None else load_units()
    pool = [
        u
        for u in units
        if _in_scope(u, profile) and (not profile.unit_type or u.unit_type == profile.unit_type)
    ]
    if not pool:
        pool = units
    entry = min(u.price_egp for u in pool)
    ceiling = profile.budget_max if profile.budget_max is not None else float("inf")
    return ceiling >= entry


def _in_scope(unit: Unit, profile: BuyerProfile) -> bool:
    if profile.preferred_areas:
        return unit.area in profile.preferred_areas
    if profile.region:
        return unit.region == profile.region
    return True


def unit_card_lines(unit: Unit) -> dict[str, Any]:
    """Card copy per the Node 6 example line."""
    price = f"{unit.price_egp / 1_000_000:.1f}M EGP"
    if unit.installment_egp:
        per = "mo" if unit.payment_frequency == "monthly" else "qtr"
        payment = (
            f"{unit.down_payment_pct}% down, ~{unit.installment_egp / 1000:.0f}K/{per} "
            f"({unit.payment_frequency}, {unit.payment_structure})"
        )
    else:
        payment = "Cash / resale - full payment"
    delivery = (
        "ready to move" if unit.delivery == "ready" else f"delivery {unit.delivery_year}"
    )
    return {"price": price, "payment": payment, "delivery": delivery}
