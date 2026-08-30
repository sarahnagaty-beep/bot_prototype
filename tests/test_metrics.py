"""Dashboard metrics derive from the event log, not from hand-set numbers."""

from src import metrics
from src.demo import build_demo, seed_pilot_outcomes


def test_snapshot_reports_the_conversations_that_actually_ran(crm):
    engines = build_demo(crm, write_samples=False)
    seed_pilot_outcomes(crm, profiled=50, control=50)
    snap = metrics.snapshot(crm)

    assert snap["headline"]["conversations"] == len(engines)
    assert snap["headline"]["qualified"] <= snap["headline"]["conversations"]
    assert snap["funnel"][0]["step"] == "Conversations started"
    assert snap["funnel"][0]["pct_of_started"] == 100.0
    assert len(snap["leads"]) == len(crm.leads())


def test_funnel_never_widens_downstream_of_qualification(crm):
    build_demo(crm, write_samples=False)
    steps = {row["step"]: row["count"] for row in metrics.funnel(crm)}
    assert steps["Opted in"] <= steps["Conversations started"]
    assert steps["Qualified (flow completed)"] <= steps["Opted in"]
    assert steps["Handed to consultant"] <= steps["Action taken"]


def test_lead_rows_are_ranked_by_score(crm):
    build_demo(crm, write_samples=False)
    scores = [row["score"] for row in metrics.lead_rows(crm)]
    assert scores == sorted(scores, reverse=True)


def test_ab_test_cohorts_are_monotonic_and_uplift_is_the_difference(crm):
    build_demo(crm, write_samples=False)
    seed_pilot_outcomes(crm, profiled=200, control=200)
    ab = metrics.ab_test(crm)

    for cohort in ("profiled", "control"):
        counts = [row["count"] for row in ab[cohort]]
        assert counts == sorted(counts, reverse=True), f"{cohort} funnel widens downstream"
    assert ab["uplift_pp"] == round(ab["profiled_close_rate"] - ab["control_close_rate"], 1)
    assert ab["sample"]["control"] == 200
