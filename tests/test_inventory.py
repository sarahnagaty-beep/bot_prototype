"""Matching, widening and the Node 7 upsell."""

from src.inventory import (
    budget_is_realistic, load_units, next_best, recommend, satisfies_hard_filters, widen,
)
from src.models import BuyerProfile

UNITS = load_units()


def _end_user() -> BuyerProfile:
    return BuyerProfile(
        buyer_type="end_user", region="cairo_east", preferred_areas=["new_cairo"],
        unit_type="apartment",
        bedrooms="3", property_status="primary", delivery_preference="off_plan",
        budget_band="7-12M", budget_min=7_000_000, budget_max=12_000_000,
        payment_type="installments", down_payment_pct=10,
    )


def test_stated_preferences_rank_above_near_misses():
    matches = recommend(_end_user(), UNITS)
    assert matches, "expected matches for a mainstream brief"
    strict = [unit for unit, _ in matches if satisfies_hard_filters(unit, _end_user())]
    assert matches[0][0] in strict
    # Every exact match outranks the first near-miss.
    first_loose = next(
        (i for i, (u, _) in enumerate(matches) if not satisfies_hard_filters(u, _end_user())),
        len(matches),
    )
    assert all(satisfies_hard_filters(u, _end_user()) for u, _ in matches[:first_loose])


def test_investor_yield_goal_favours_ready_stock():
    profile = BuyerProfile(
        buyer_type="investor", investor_goal="yield", delivery_preference="ready",
        property_status="both", budget_band="4-7M", budget_min=4_000_000,
        budget_max=7_000_000, payment_type="cash",
    )
    top = recommend(profile, UNITS)[0][0]
    assert top.delivery == "ready"
    assert top.rental_yield_pct >= 7


def test_widening_keeps_the_home_type_the_buyer_asked_for():
    profile = BuyerProfile(
        region="north_coast", unit_type="villa", bedrooms="4+",
        property_status="resale", delivery_preference="ready",
        budget_band="Under 4M", budget_min=0, budget_max=4_000_000,
    )
    assert not [u for u, _ in recommend(profile, UNITS) if satisfies_hard_filters(u, profile)]
    assert all(unit.unit_type == "villa" for unit, _ in widen(profile, UNITS))


def test_upsell_is_priced_just_above_the_shortlist():
    profile = _end_user()
    shortlist = [unit.unit_id for unit, _ in recommend(profile, UNITS)]
    unit, reason = next_best(profile, shortlist, UNITS)
    assert unit.unit_id not in shortlist[:1]
    assert "EGP" in reason
    assert unit.area in profile.preferred_areas


def test_budget_below_entry_price_is_not_realistic():
    profile = BuyerProfile(
        region="cairo_east", preferred_areas=["new_cairo"], unit_type="villa",
        budget_band="Under 4M", budget_max=4_000_000,
    )
    assert budget_is_realistic(profile, UNITS) is False
    profile.budget_max = 40_000_000
    assert budget_is_realistic(profile, UNITS) is True
