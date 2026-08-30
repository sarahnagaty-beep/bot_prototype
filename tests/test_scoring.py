"""Scoring bands - Part A, §3 of the conversation prototype."""

from src.models import BuyerProfile
from src.scoring import band_for, score_profile


def test_bands_match_the_script():
    assert band_for(0) == "cold"
    assert band_for(2) == "cold"
    assert band_for(3) == "warm"
    assert band_for(5) == "warm"
    assert band_for(6) == "hot"
    assert band_for(11) == "hot"


def test_hot_lead_scores_budget_payment_timeline_and_completion():
    profile = BuyerProfile(
        buyer_type="end_user", budget_band="7-12M", budget_max=12_000_000,
        payment_type="installments", down_payment_pct=15, timeline="asap",
        completed_flow=True,
    )
    score, band, breakdown = score_profile(profile, budget_is_realistic=True)
    assert score == 8  # 2 budget + 2 down payment + 3 timeline + 1 completion
    assert band == "hot"
    assert [item["points"] for item in breakdown] == [2, 2, 3, 1]


def test_financing_dependent_scores_one_not_two():
    profile = BuyerProfile(
        budget_band="4-7M", budget_max=7_000_000, payment_type="installments",
        down_payment_pct=10, timeline="3_6_months", completed_flow=True,
    )
    score, band, _ = score_profile(profile, budget_is_realistic=True)
    assert score == 5 and band == "warm"


def test_unrealistic_budget_scores_zero_for_that_signal():
    profile = BuyerProfile(budget_band="Under 4M", budget_max=4_000_000, timeline="exploring")
    score, band, breakdown = score_profile(profile, budget_is_realistic=False)
    assert score == 0 and band == "cold"
    assert "below entry price" in breakdown[0]["signal"]


def test_investor_with_budget_gets_the_extra_point():
    kwargs = dict(
        budget_band="4-7M", budget_max=7_000_000, payment_type="cash",
        timeline="1_3_months", completed_flow=True,
    )
    end_user, _, _ = score_profile(BuyerProfile(buyer_type="end_user", **kwargs), True)
    investor, _, _ = score_profile(BuyerProfile(buyer_type="investor", **kwargs), True)
    assert investor == end_user + 1
