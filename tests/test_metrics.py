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
    # The arms are sized alike - the real bot leads land in the profiled arm,
    # so the control cohort is topped up to match rather than left short.
    assert ab["sample"]["control"] == ab["sample"]["profiled"]
    assert ab["sample"]["profiled"] >= 200


def test_region_rollup_covers_every_lead(crm):
    """Cairo East / Cairo West / North Coast, plus the investors who never
    state one - the three buckets plus that row must reconcile to the total."""
    from src.scale import generate

    generate(crm, leads=120, days=30, progress=False)
    rows = metrics.regions(crm)

    labels = [row["label"] for row in rows]
    assert {"Cairo East", "Cairo West", "North Coast"} <= set(labels)
    assert sum(row["count"] for row in rows) == len(crm.leads())
    for row in rows:
        assert sum(area["count"] for area in row["areas"]) <= row["count"]


def test_lead_page_filters_and_pages(crm):
    from src.scale import generate

    generate(crm, leads=300, days=30, progress=False)

    everything = metrics.lead_page(crm, page_size=25)
    assert everything["total"] == len(crm.leads())
    assert len(everything["rows"]) == 25
    assert everything["pages"] == -(-everything["total"] // 25)

    page_two = metrics.lead_page(crm, page=2, page_size=25)
    assert not {r["lead_id"] for r in page_two["rows"]} & {
        r["lead_id"] for r in everything["rows"]
    }

    hot = metrics.lead_page(crm, band="hot", page_size=200)
    assert hot["total"] <= everything["total"]
    assert all(row["band"] == "hot" for row in hot["rows"])

    coast = metrics.lead_page(crm, region="north_coast", page_size=200)
    assert all(row["region"] == "north_coast" for row in coast["rows"])

    named = everything["rows"][0]["name"]
    found = metrics.lead_page(crm, query=named, page_size=200)
    assert any(row["name"] == named for row in found["rows"])
    assert metrics.lead_page(crm, query="zzzz-no-such-buyer")["total"] == 0


def test_floor_rollup_spreads_leads_across_the_whole_roster(crm):
    """A round-robin over four consultants is not a floor: every assigned lead
    must land inside the buyer's own region, and load must stay even."""
    from src.scale import generate

    generate(crm, leads=400, days=30, progress=False)
    floor = metrics.floor(crm)

    assert floor["headcount"] > 100
    assert floor["teams_total"] > 1
    assert floor["agents_with_leads"] > 20

    load = list(crm.agent_load().values())
    assert max(load) - min(load) <= 2, "assignment is lopsided"

    by_name = {agent["name"]: agent for agent in crm.agents}
    for lead in crm.leads():
        profile = lead["profile"]
        if profile.get("consultant") and profile.get("region"):
            assert by_name[profile["consultant"]]["region"] == profile["region"]
