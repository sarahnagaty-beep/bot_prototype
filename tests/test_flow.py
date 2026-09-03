"""End-to-end runs through the node graph (Part B of the script)."""

from src.flow import Engine
from src.inventory import load_units
from src.simulator import run_script

UNITS = load_units()

HOT_END_USER = [
    "Yes, let's go", "Buy to live in", "Cairo East", "New Cairo", "Apartment", "3", "Primary",
    "Off-plan", "7-12M", "Installments", "15%", "Quarterly", "Backloaded", "ASAP",
    "No thanks", "Book a viewing",
]


def run(crm, replies, **kwargs):
    return run_script(
        crm, wa_id=kwargs.pop("wa_id", "+201000000001"), name=kwargs.pop("name", "Test Buyer"),
        replies=replies, echo=False, color=False, units=UNITS, **kwargs,
    )


def test_hot_end_user_is_scored_shortlisted_and_handed_off(crm):
    engine = run(crm, HOT_END_USER)
    profile = engine.profile
    assert profile.band == "hot" and profile.score >= 6
    assert profile.buyer_type == "end_user"
    assert profile.region == "cairo_east"
    assert profile.preferred_areas == ["new_cairo"]
    assert profile.payment_frequency == "quarterly" and profile.payment_structure == "backloaded"
    assert profile.intent_action == "viewing"
    assert profile.shortlisted_unit_ids
    assert profile.consultant, "a hot lead must be assigned a consultant"
    assert crm.leads()[0]["profile"]["score"] == profile.score


def test_free_text_answers_are_understood_without_buttons(crm):
    engine = run(crm, [
        "sure", "it's for me and my family", "somewhere near the coast", "anywhere", "chalet",
        "2 bedrooms", "resale is fine", "ready to move", "around 6 million", "cash",
        "this month", "book a viewing",
    ])
    profile = engine.profile
    assert profile.buyer_type == "end_user"
    assert profile.region == "north_coast"
    assert not profile.preferred_areas  # "anywhere" keeps the whole region in play
    assert profile.unit_type == "chalet" and profile.bedrooms == "2"
    assert profile.budget_band == "4-7M"
    assert profile.payment_type == "cash" and profile.timeline == "asap"
    assert profile.band == "hot"


def test_investor_branch_skips_layout_questions_and_infers_delivery(crm):
    engine = run(crm, ["yes", "I want to invest", "rental yield", "2-3", "4-7M", "cash",
                       "1-3 months", "no thanks", "talk to a consultant"])
    profile = engine.profile
    assert profile.buyer_type == "investor" and profile.investor_goal == "yield"
    assert profile.units_target == "2_3"
    assert profile.delivery_preference == "ready"  # yield favours ready stock
    assert not profile.unit_type  # the script does not ask investors for a layout
    assert profile.consultant


def test_browsing_exits_cold_without_a_handoff(crm):
    engine = run(crm, ["I'm just browsing", "Yes, save them"])
    assert engine.profile.band == "cold"
    assert not engine.profile.consultant
    assert {e["event"] for e in crm.events()} >= {"browsing_exit", "nurture_scheduled"}


def test_broker_is_routed_out_of_the_buyer_flow(crm):
    engine = run(crm, ["yes", "I'm a broker"])
    assert engine.profile.buyer_type == "broker"
    assert engine.ended
    assert any(e["event"] == "routed_broker_bot" for e in crm.events())


def test_two_low_confidence_turns_offer_a_human(crm):
    engine = run(crm, ["yes", "hmm", "no idea what you're asking", "talk to a consultant"])
    assert engine.profile.consultant
    assert sum(1 for e in crm.events() if e["event"] == "low_confidence") == 2


def test_returning_buyer_resumes_without_repeating_questions(crm):
    run(crm, HOT_END_USER, wa_id="+201555000111", name="Hana")
    second = run(crm, ["Pick up", "no thanks", "express interest"],
                 wa_id="+201555000111", name="Hana")
    assert second.profile.sessions == 2
    bot_turns = [t for t in crm.transcript(second.conversation_id) if t["role"] == "bot"]
    assert "Welcome back" in bot_turns[0]["text"]
    assert not any("Which area" in t["text"] for t in bot_turns)
    assert second.profile.intent_action == "reserve"


def test_stop_opts_the_buyer_out_at_any_point(crm):
    engine = run(crm, ["yes", "Buy to live in", "STOP"])
    assert engine.profile.opted_out and engine.ended
    assert any(e["event"] == "opt_out" for e in crm.events())


def test_no_match_offers_widening_then_an_alert(crm):
    engine = run(crm, ["yes", "buy to live in", "North Coast", "anywhere", "villa", "4+", "resale",
                       "ready to move", "under 4M", "cash", "just exploring",
                       "widen the search", "get full brochure"])
    assert any(e["event"] == "alert_registered" for e in crm.events())
    assert engine.profile.band in ("cold", "warm")


def test_after_hours_handoff_promises_a_morning_call(crm):
    engine = run(crm, ["yes", "buy to live in", "Cairo West", "Sheikh Zayed", "townhouse", "4+", "both",
                       "either", "12-20M", "cash", "asap", "express interest"], now_hour=23)
    last = crm.transcript(engine.conversation_id)[-1]["text"]
    assert "offline right now" in last


def test_engine_never_asks_the_same_node_twice_in_one_run(crm):
    engine = run(crm, HOT_END_USER)
    asked = [t["node"] for t in crm.transcript(engine.conversation_id) if t["role"] == "buyer"]
    assert len(asked) == len(set(asked)), f"repeated question nodes: {asked}"
    assert isinstance(engine, Engine)
