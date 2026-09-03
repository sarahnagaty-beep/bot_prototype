"""
Conversation engine - Part B of the Buyer Bot conversation prototype.

The node graph follows the script one-to-one (N0 entry, N0.5 returning buyer,
N1 buyer type, N2/N2i qualification, N3 budget & payment, N4 timeline,
N5 scoring checkpoint, N6 recommendation, N7 upsell, N8 CTA, N9 handoff,
N10 re-engagement, N11 fallbacks) so the flow stays auditable against the doc.

Two kinds of node:
  Ask - sends a question, waits for the buyer.
  Do  - runs logic, emits messages, falls through to the next node.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from . import inventory as inv
from . import nlu
from .crm import CRM
from .models import (
    AREA_LABELS, REGIONS, BuyerProfile, Card, Message, QuickReply, TranscriptEntry, Unit,
    region_label,
)
from .scoring import apply_score, routing_for

ENDED = "__end__"
BUSINESS_HOURS = range(9, 21)  # Africa/Cairo working window

BUDGET_BANDS = {
    "under_4m": ("Under 4M", 0, 4_000_000),
    "4_7m": ("4-7M", 4_000_000, 7_000_000),
    "7_12m": ("7-12M", 7_000_000, 12_000_000),
    "12_20m": ("12-20M", 12_000_000, 20_000_000),
    "20m_plus": ("20M+", 20_000_000, None),
}


Options = list[tuple[str, str]]


@dataclass
class Ask:
    node: str
    prompt: Callable[["Engine"], str]
    # A list, or a function of the conversation so far (the compounds offered
    # depend on the region the buyer just picked).
    options: Options | Callable[["Engine"], Options]
    next: Callable[["Engine", str], str]
    field: Optional[str] = None
    capture: Optional[Callable[["Engine", str], None]] = None
    parse: Optional[Callable[["Engine", str], tuple[Optional[str], float]]] = None


@dataclass
class Engine:
    """One buyer conversation."""

    crm: CRM
    profile: BuyerProfile
    units: list[Unit] = field(default_factory=list)
    conversation_id: str = field(default_factory=lambda: f"C-{uuid.uuid4().hex[:8]}")
    cohort: str = "profiled"
    now_hour: int = 12
    awaiting: Optional[str] = None
    low_confidence_turns: int = 0
    ended: bool = False
    shortlist: list[tuple[Unit, float]] = field(default_factory=list)
    upsell: Optional[tuple[Unit, str]] = None
    outbox: list[Message] = field(default_factory=list)

    # -- construction -------------------------------------------------------

    @classmethod
    def for_buyer(
        cls,
        crm: CRM,
        wa_id: str,
        name: str = "",
        source: str = "meta_ad",
        source_ref: str = "",
        now_hour: int = 12,
        cohort: str = "profiled",
        units: Optional[list[Unit]] = None,
    ) -> "Engine":
        profile = crm.get_profile(wa_id)
        if profile:
            profile.sessions += 1
        else:
            profile = BuyerProfile(wa_id=wa_id, name=name)
        profile.name = name or profile.name
        profile.source = source or profile.source
        profile.source_ref = source_ref or profile.source_ref
        return cls(
            crm=crm,
            profile=profile,
            units=units if units is not None else inv.load_units(),
            now_hour=now_hour,
            cohort=cohort,
        )

    # -- persistence --------------------------------------------------------

    def state_dict(self) -> dict[str, Any]:
        """Everything needed to resume this conversation after a restart."""
        return {
            "conversation_id": self.conversation_id,
            "wa_id": self.profile.wa_id,
            "awaiting": self.awaiting,
            "low_confidence_turns": self.low_confidence_turns,
            "ended": self.ended,
            "cohort": self.cohort,
            "upsell_unit_id": self.upsell[0].unit_id if self.upsell else "",
            "upsell_reason": self.upsell[1] if self.upsell else "",
        }

    @classmethod
    def restore(
        cls, crm: CRM, state: dict[str, Any], units: Optional[list[Unit]] = None
    ) -> Optional["Engine"]:
        """Rebuild an engine from saved state. The profile is the source of
        truth; only turn-level state lives in the session record."""
        profile = crm.get_profile(state.get("wa_id", ""))
        if not profile:
            return None
        units = units if units is not None else inv.load_units()
        engine = cls(
            crm=crm,
            profile=profile,
            units=units,
            conversation_id=state.get("conversation_id", f"C-{uuid.uuid4().hex[:8]}"),
            cohort=state.get("cohort", "profiled"),
            awaiting=state.get("awaiting"),
            low_confidence_turns=state.get("low_confidence_turns", 0),
            ended=state.get("ended", False),
        )
        by_id = {unit.unit_id: unit for unit in units}
        engine.shortlist = [
            (by_id[uid], 1.0) for uid in profile.shortlisted_unit_ids if uid in by_id
        ]
        upsell_id = state.get("upsell_unit_id")
        if upsell_id and upsell_id in by_id:
            engine.upsell = (by_id[upsell_id], state.get("upsell_reason", ""))
        return engine

    # -- public API ---------------------------------------------------------

    def start(self) -> list[Message]:
        self.crm.log_event(
            "conversation_started",
            self.conversation_id,
            self.profile.wa_id,
            {"source": self.profile.source, "source_ref": self.profile.source_ref,
             "cohort": self.cohort, "returning": self.profile.is_known},
        )
        first = "N0_5" if self.profile.is_known else "N0"
        return self._advance(first)

    def can_answer(self, text: str) -> bool:
        """Does this text actually answer the question on the table?

        An opening "hi" is a greeting, not an answer - feeding it to the flow
        would greet the buyer and immediately tell them it didn't understand.
        """
        if self.ended or not self.awaiting:
            return False
        value, confidence = self._interpret(ASKS[self.awaiting], text)
        return value is not None and confidence >= 0.5

    def handle(self, text: str) -> list[Message]:
        """Feed one buyer message into the flow."""
        self._log_buyer(text)

        if nlu.is_opt_out(text):
            return self._opt_out()

        if self.ended or not self.awaiting:
            self.outbox = []
            return self._emit(Message.text_msg(
                "Thanks! I've saved everything - your consultant will pick it up from here.",
                node="N9",
            ))

        ask = ASKS[self.awaiting]
        value, confidence = self._interpret(ask, text)

        if value is None or confidence < 0.5:
            return self._fallback(ask, text)

        self.low_confidence_turns = 0
        if ask.capture:
            ask.capture(self, value)
        elif ask.field:
            setattr(self.profile, ask.field, value)
        self.profile.last_node = ask.node
        self.crm.upsert_profile(self.profile)

        return self._advance(ask.next(self, value))

    # -- internals ----------------------------------------------------------

    def options_for(self, ask: Ask) -> Options:
        return ask.options(self) if callable(ask.options) else ask.options

    def _interpret(self, ask: Ask, text: str) -> tuple[Optional[str], float]:
        if ask.parse:
            value, confidence = ask.parse(self, text)
            if value is not None:
                return value, confidence
        return nlu.resolve(text, [value for _, value in self.options_for(ask)])

    def _fallback(self, ask: Ask, text: str) -> list[Message]:
        """Node 11 - two low-confidence turns, then offer a human."""
        self.outbox = []
        self.low_confidence_turns += 1
        self.crm.log_event(
            "low_confidence", self.conversation_id, self.profile.wa_id,
            {"node": ask.node, "text": text, "turn": self.low_confidence_turns},
        )
        if self.low_confidence_turns >= 2:
            self.low_confidence_turns = 0
            return self._advance("N11_HUMAN")
        return self._emit(
            Message.choice(
                "Sorry - I didn't catch that. Pick one of these and we'll keep moving:",
                [QuickReply(label, value) for label, value in self.options_for(ask)],
                node="N11",
            ),
        )

    def _opt_out(self) -> list[Message]:
        self.outbox = []
        self.profile.opted_out = True
        self.ended = True
        self.awaiting = None
        self.crm.upsert_profile(self.profile)
        self.crm.log_event("opt_out", self.conversation_id, self.profile.wa_id)
        return self._emit(Message.text_msg(
            "You're opted out - I won't message you again. If you change your mind, "
            "just send us a message any time.",
            node="N0",
        ))

    def _advance(self, node: str) -> list[Message]:
        """Run Do-nodes until the next question (or the end)."""
        self.outbox = []
        guard = 0
        while node and node != ENDED:
            guard += 1
            if guard > 40:
                raise RuntimeError(f"flow loop at {node}")
            if node in ASKS:
                ask = ASKS[node]
                self.awaiting = node
                self.profile.last_node = node
                options = self.options_for(ask)
                self._emit(
                    Message.choice(
                        ask.prompt(self),
                        [QuickReply(label, value) for label, value in options],
                        node=node,
                    )
                    if options
                    else Message.text_msg(ask.prompt(self), node=node)
                )
                self.crm.upsert_profile(self.profile)
                return self.outbox
            node = DOS[node](self)
        self.awaiting = None
        self.ended = True
        self.crm.upsert_profile(self.profile)
        return self.outbox

    def _emit(self, message: Message) -> list[Message]:
        self.outbox.append(message)
        self.crm.append_transcript(
            self.conversation_id, TranscriptEntry("bot", _render(message), message.node)
        )
        return self.outbox

    def _log_buyer(self, text: str) -> None:
        self.crm.append_transcript(
            self.conversation_id, TranscriptEntry("buyer", text, self.awaiting or "")
        )

    # -- helpers used by node logic ----------------------------------------

    def rescore(self) -> None:
        realistic = inv.budget_is_realistic(self.profile, self.units)
        apply_score(self.profile, realistic)

    @property
    def after_hours(self) -> bool:
        return self.now_hour not in BUSINESS_HOURS


def _render(message: Message) -> str:
    """Flat text of an outbound message, for the transcript."""
    parts = [message.text] if message.text else []
    if message.quick_replies:
        parts.append("[" + "] [".join(q.label for q in message.quick_replies) + "]")
    for card in message.cards:
        parts.append(
            f"- {card.title} | {card.subtitle} | {card.price_line} | "
            f"{card.payment_line} | {card.delivery_line}"
        )
    return "\n".join(parts)


# ==========================================================================
# Capture helpers
# ==========================================================================

def _capture_region(engine: Engine, value: str) -> None:
    if value == "not_sure":
        return
    engine.profile.region = value
    engine.profile.preferred_areas = []


def _capture_area(engine: Engine, value: str) -> None:
    """"Anywhere in <region>" leaves the compounds open and matches region-wide."""
    if value in ("any", "not_sure"):
        return
    if value not in engine.profile.preferred_areas:
        engine.profile.preferred_areas.append(value)


def _area_options(engine: Engine) -> Options:
    region = engine.profile.region
    areas = REGIONS.get(region, {}).get("areas", [])
    return [(f"Anywhere in {region_label(region)}", "any")] + [
        (AREA_LABELS.get(area, area), area) for area in areas
    ]


def _capture_budget(engine: Engine, value: str) -> None:
    label, low, high = BUDGET_BANDS[value]
    engine.profile.budget_band = label
    engine.profile.budget_min = low
    engine.profile.budget_max = high


def _parse_budget(engine: Engine, text: str) -> tuple[Optional[str], float]:
    """Accept 'around 10 million' as well as a band button."""
    band, confidence = nlu.resolve(text, list(BUDGET_BANDS))
    if band:
        return band, confidence
    parsed = nlu.parse_budget_range(text)
    if not parsed:
        return None, 0.0
    low, high = parsed
    midpoint = (low or 0) if high is None else ((low or 0) + high) / 2
    for key, (_, band_low, band_high) in BUDGET_BANDS.items():
        if band_low <= midpoint < (band_high or float("inf")):
            return key, 0.8
    return None, 0.0


def _parse_bedrooms(engine: Engine, text: str) -> tuple[Optional[str], float]:
    beds = nlu.parse_bedrooms(text)
    return (beds, 0.9) if beds else (None, 0.0)


def _capture_down_payment(engine: Engine, value: str) -> None:
    engine.profile.down_payment_pct = {"dp_10": 10, "dp_15": 15, "dp_20": 20}.get(value)
    engine.profile.tenor_years = 8


def _capture_priority(engine: Engine, value: str) -> None:
    """Node 2 'Not sure' branch - infer areas from what matters most."""
    engine.profile.area_priority = value
    engine.profile.region = {
        "work": "cairo_east",
        "schools": "cairo_east",
        "coast": "north_coast",
        "quiet": "cairo_west",
        "value": "cairo_west",
    }.get(value, "")
    engine.profile.preferred_areas = []


# ==========================================================================
# Ask nodes
# ==========================================================================

ASKS: dict[str, Ask] = {}


def _ask(ask: Ask) -> Ask:
    ASKS[ask.node] = ask
    return ask


_ask(Ask(
    node="N0",
    prompt=lambda e: (
        f"Hi{' ' + e.profile.name.split()[0] if e.profile.name else ''}! Welcome to Palm Ridge Living. "
        "I can help you find the right home or investment in Cairo. "
        "Mind if I ask a few quick questions so I can shortlist the best options for you?"
    ),
    options=[("Yes, let's go", "yes"), ("I'm just browsing", "browsing")],
    next=lambda e, v: "N1_CONSENT" if v == "yes" else "N_BROWSE",
))

_ask(Ask(
    node="N0_5",
    prompt=lambda e: (
        f"Welcome back{', ' + e.profile.name.split()[0] if e.profile.name else ''}! "
        f"Last time you were looking at {e.profile.summary_line()}. "
        "Want to pick up there, or start fresh?"
    ),
    options=[("Pick up", "pick_up"), ("Start fresh", "start_fresh")],
    next=lambda e, v: "N6_RESUME" if v == "pick_up" else "N1_CONSENT",
))

_ask(Ask(
    node="N1",
    prompt=lambda e: "Great! Are you looking to...",
    options=[
        ("Buy to live in", "end_user"),
        ("Buy as an investment", "investor"),
        ("I'm a broker", "broker"),
    ],
    field="buyer_type",
    next=lambda e, v: {"end_user": "N2", "investor": "N2I", "broker": "N_BROKER"}[v],
))

_ask(Ask(
    node="N2",
    prompt=lambda e: "Which part of the market are you looking at?",
    options=[
        ("Cairo East", "cairo_east"),
        ("Cairo West", "cairo_west"),
        ("North Coast", "north_coast"),
        ("Not sure yet", "not_sure"),
    ],
    capture=_capture_region,
    next=lambda e, v: "N2_PRIORITY" if v == "not_sure" else "N2_AREA",
))

_ask(Ask(
    node="N2_AREA",
    prompt=lambda e: f"Anywhere in particular in {region_label(e.profile.region)}?",
    options=_area_options,
    capture=_capture_area,
    next=lambda e, v: "N2_TYPE",
))

_ask(Ask(
    node="N2_PRIORITY",
    prompt=lambda e: "No problem - what matters most to you?",
    options=[
        ("Close to work", "work"),
        ("Good schools", "schools"),
        ("Near the coast", "coast"),
        ("Quiet & spacious", "quiet"),
        ("Best value", "value"),
    ],
    capture=_capture_priority,
    next=lambda e, v: "N2_AREA" if e.profile.region else "N2_TYPE",
))

_ask(Ask(
    node="N2_TYPE",
    prompt=lambda e: "And what type of home?",
    options=[
        ("Apartment", "apartment"),
        ("Duplex", "duplex"),
        ("Townhouse", "townhouse"),
        ("Twinhouse", "twinhouse"),
        ("Villa", "villa"),
        ("Chalet", "chalet"),
    ],
    field="unit_type",
    next=lambda e, v: "N2_BEDS",
))

_ask(Ask(
    node="N2_BEDS",
    prompt=lambda e: "How many bedrooms?",
    options=[("1", "1"), ("2", "2"), ("3", "3"), ("4+", "4+")],
    field="bedrooms",
    parse=_parse_bedrooms,
    next=lambda e, v: "N2_STATUS",
))

_ask(Ask(
    node="N2_STATUS",
    prompt=lambda e: "Are you open to primary (from the developer) or resale units - or both?",
    options=[("Primary", "primary"), ("Resale", "resale"), ("Both", "both")],
    field="property_status",
    next=lambda e, v: "N2_DELIVERY",
))

_ask(Ask(
    node="N2_DELIVERY",
    prompt=lambda e: "And would you prefer something ready to move, or off-plan?",
    options=[("Ready to move", "ready"), ("Off-plan", "off_plan"), ("Either", "either")],
    field="delivery_preference",
    next=lambda e, v: "N3",
))

_ask(Ask(
    node="N2I",
    prompt=lambda e: "Smart - are you after...",
    options=[
        ("Capital appreciation", "appreciation"),
        ("Rental yield", "yield"),
        ("Both", "both"),
    ],
    field="investor_goal",
    next=lambda e, v: "N2I_UNITS",
))

_ask(Ask(
    node="N2I_UNITS",
    prompt=lambda e: "How many units are you considering?",
    options=[("1", "1"), ("2-3", "2_3"), ("Portfolio / 4+", "portfolio")],
    field="units_target",
    next=lambda e, v: "N2I_LOGIC",
))

_ask(Ask(
    node="N3",
    prompt=lambda e: "What's your budget range?",
    options=[
        ("Under 4M", "under_4m"),
        ("4-7M", "4_7m"),
        ("7-12M", "7_12m"),
        ("12-20M", "12_20m"),
        ("20M+ EGP", "20m_plus"),
    ],
    capture=_capture_budget,
    parse=_parse_budget,
    next=lambda e, v: "N3_PAYMENT",
))

_ask(Ask(
    node="N3_PAYMENT",
    prompt=lambda e: "Would you pay cash or in installments?",
    options=[("Cash", "cash"), ("Installments", "installments")],
    field="payment_type",
    next=lambda e, v: "N4" if v == "cash" else "N3_DOWN",
))

_ask(Ask(
    node="N3_DOWN",
    prompt=lambda e: (
        "Great - our plans start around 10% down over up to 8 years. "
        "What down payment feels comfortable?"
    ),
    options=[("10%", "dp_10"), ("15%", "dp_15"), ("20%+", "dp_20"), ("Not sure", "dp_unsure")],
    capture=_capture_down_payment,
    next=lambda e, v: "N3_FREQUENCY",
))

_ask(Ask(
    node="N3_FREQUENCY",
    prompt=lambda e: "How would you like to structure the installments?",
    options=[("Monthly", "monthly"), ("Quarterly", "quarterly")],
    field="payment_frequency",
    next=lambda e, v: "N3_STRUCTURE",
))

_ask(Ask(
    node="N3_STRUCTURE",
    prompt=lambda e: "And equal installments, or backloaded (smaller now, larger later)?",
    options=[("Equal", "equal"), ("Backloaded", "backloaded")],
    field="payment_structure",
    next=lambda e, v: "N4",
))

_ask(Ask(
    node="N4",
    prompt=lambda e: "When are you hoping to buy?",
    options=[
        ("ASAP / within a month", "asap"),
        ("1-3 months", "1_3_months"),
        ("3-6 months", "3_6_months"),
        ("Just exploring", "exploring"),
    ],
    field="timeline",
    next=lambda e, v: "N5",
))

_ask(Ask(
    node="N6_NOMATCH",
    prompt=lambda e: (
        "I don't have an exact fit right now - want me to widen the budget or area, "
        "or alert you the moment something matches?"
    ),
    options=[("Widen the search", "widen"), ("Alert me", "alert")],
    next=lambda e, v: "N6_WIDEN" if v == "widen" else "N6_ALERT",
))

_ask(Ask(
    node="N7",
    prompt=lambda e: e.upsell_prompt(),
    options=[("Yes, show me", "yes"), ("No thanks", "no")],
    next=lambda e, v: "N7_SHOW" if v == "yes" else "N8",
))

_ask(Ask(
    node="N8",
    prompt=lambda e: "What would you like to do next?",
    options=[
        ("Book a viewing", "viewing"),
        ("Talk to a consultant", "consultant"),
        ("Get full brochure", "brochure"),
        ("Express interest / reserve", "reserve"),
    ],
    field="intent_action",
    next=lambda e, v: "N8_SAVE",
))

_ask(Ask(
    node="N11_HUMAN",
    prompt=lambda e: (
        "I want to get this right - would you rather carry on with me, "
        "or have one of our consultants call you?"
    ),
    options=[("Talk to a consultant", "consultant"), ("Keep going here", "keep_going")],
    next=lambda e, v: "N9_HANDOFF" if v == "consultant" else "N11_RESUME",
))

_ask(Ask(
    node="N_BROWSE_SAVE",
    prompt=lambda e: "Want me to save your preferences so I can alert you when something fits?",
    options=[("Yes, save them", "yes"), ("No thanks", "no")],
    next=lambda e, v: "N10_NURTURE",
))


# ==========================================================================
# Do nodes
# ==========================================================================

DOS: dict[str, Callable[[Engine], str]] = {}


def _do(name: str) -> Callable[[Callable[[Engine], str]], Callable[[Engine], str]]:
    def wrap(fn: Callable[[Engine], str]) -> Callable[[Engine], str]:
        DOS[name] = fn
        return fn

    return wrap


@_do("N1_CONSENT")
def _consent(engine: Engine) -> str:
    engine.profile.consent = True
    engine.crm.log_event("opt_in", engine.conversation_id, engine.profile.wa_id)
    return "N1"


@_do("N_BROWSE")
def _browse(engine: Engine) -> str:
    """Node 0 'just browsing' branch - 3 featured projects, then nurture."""
    featured = sorted(engine.units, key=lambda u: -u.appreciation_pct)[:3]
    engine._emit(
        Message(
            kind="carousel",
            text="No problem - here are three projects buyers are moving on this month:",
            cards=[_card(u) for u in featured],
            node="N0",
        ),
    )
    engine.crm.log_event("browsing_exit", engine.conversation_id, engine.profile.wa_id)
    return "N_BROWSE_SAVE"


@_do("N_BROKER")
def _broker(engine: Engine) -> str:
    engine.crm.log_event("routed_broker_bot", engine.conversation_id, engine.profile.wa_id)
    engine._emit(Message.text_msg(
        "Welcome! Brokers get their own self-serve view - I'm handing you to our Broker Bot "
        "for live inventory, availability and commission terms.",
        node="N1",
    ))
    return ENDED


@_do("N2I_LOGIC")
def _investor_logic(engine: Engine) -> str:
    """Node 2i: yield favours ready stock, appreciation favours off-plan."""
    goal = engine.profile.investor_goal
    if goal == "yield":
        engine.profile.delivery_preference = "ready"
    elif goal == "appreciation":
        engine.profile.delivery_preference = "off_plan"
    else:
        engine.profile.delivery_preference = "either"
    engine.profile.property_status = "both"
    return "N3"


@_do("N5")
def _qualification_checkpoint(engine: Engine) -> str:
    """Internal only - never shown to the buyer."""
    engine.profile.completed_flow = True
    engine.rescore()
    engine.crm.log_event(
        "qualified", engine.conversation_id, engine.profile.wa_id,
        {"score": engine.profile.score, "band": engine.profile.band,
         "routing": routing_for(engine.profile.band)},
    )
    return "N6"


@_do("N6")
def _recommend(engine: Engine) -> str:
    matches = inv.recommend(engine.profile, engine.units, limit=5)
    exact = [
        (u, f) for u, f in matches if inv.satisfies_hard_filters(u, engine.profile)
    ]
    if not exact:
        return "N6_NOMATCH"

    engine.shortlist = matches
    engine.profile.shortlisted_unit_ids = [u.unit_id for u, _ in matches]
    engine._emit(
        Message(
            kind="carousel",
            text="Based on what you told me, here are your best matches - swipe through:",
            cards=[_card(u) for u, _ in matches],
            node="N6",
        ),
    )
    engine.crm.log_event(
        "recommended", engine.conversation_id, engine.profile.wa_id,
        {"unit_ids": engine.profile.shortlisted_unit_ids, "count": len(matches)},
    )
    return "N7_CHECK"


@_do("N6_RESUME")
def _resume(engine: Engine) -> str:
    """Node 0.5 'pick up' - straight to recommendations on saved filters."""
    engine.crm.log_event("resumed_profile", engine.conversation_id, engine.profile.wa_id)
    engine.rescore()
    return "N6"


@_do("N6_WIDEN")
def _widen(engine: Engine) -> str:
    matches = inv.widen(engine.profile, engine.units, limit=3)
    if not matches:
        return "N6_ALERT"
    engine.shortlist = matches
    engine.profile.shortlisted_unit_ids = [u.unit_id for u, _ in matches]
    engine._emit(
        Message(
            kind="carousel",
            text="Here's the closest I have if we stretch the search slightly:",
            cards=[_card(u) for u, _ in matches],
            node="N6",
        ),
    )
    engine.crm.log_event(
        "recommended_widened", engine.conversation_id, engine.profile.wa_id,
        {"unit_ids": engine.profile.shortlisted_unit_ids},
    )
    return "N8"


@_do("N6_ALERT")
def _alert(engine: Engine) -> str:
    engine.profile.band = "warm" if engine.profile.band == "cold" else engine.profile.band
    engine.crm.log_event("alert_registered", engine.conversation_id, engine.profile.wa_id)
    engine._emit(Message.text_msg(
        "Done - I've saved your criteria and I'll message you the moment a matching unit "
        "is released.",
        node="N6",
    ))
    return "N8"


@_do("N7_CHECK")
def _upsell_check(engine: Engine) -> str:
    engine.upsell = inv.next_best(
        engine.profile, engine.profile.shortlisted_unit_ids, engine.units
    )
    return "N7" if engine.upsell else "N8"


@_do("N7_SHOW")
def _upsell_show(engine: Engine) -> str:
    unit, _ = engine.upsell  # type: ignore[misc]
    engine._emit(
        Message(kind="carousel", text="Here it is:", cards=[_card(unit)], node="N7"),
    )
    if unit.unit_id not in engine.profile.shortlisted_unit_ids:
        engine.profile.shortlisted_unit_ids.append(unit.unit_id)
    engine.profile.selected_unit_id = unit.unit_id
    engine.crm.log_event(
        "upsell_accepted", engine.conversation_id, engine.profile.wa_id,
        {"unit_id": unit.unit_id},
    )
    return "N8"


@_do("N8_SAVE")
def _save_lead(engine: Engine) -> str:
    """Node 8 - write/update the CRM lead with every field + score + source."""
    engine.rescore()
    if not engine.profile.selected_unit_id and engine.profile.shortlisted_unit_ids:
        engine.profile.selected_unit_id = engine.profile.shortlisted_unit_ids[0]
    lead = engine.crm.write_lead(engine.profile, engine.conversation_id)
    engine.crm.log_event(
        "lead_written", engine.conversation_id, engine.profile.wa_id,
        {"lead_id": lead["lead_id"], "intent": engine.profile.intent_action,
         "band": engine.profile.band, "score": engine.profile.score,
         "cohort": engine.cohort},
    )
    engine._emit(Message.text_msg(
        "Perfect - I've saved your preferences and shared them with our team.", node="N8"
    ))

    hot = engine.profile.band == "hot"
    if hot or engine.profile.intent_action in ("consultant", "reserve", "viewing"):
        return "N9_HANDOFF"
    return "N10_NURTURE"


@_do("N9_HANDOFF")
def _handoff(engine: Engine) -> str:
    """Node 9 - rotation assignment, transcript passed with the lead."""
    consultant = engine.crm.assign_consultant(engine.profile)
    engine.profile.consultant = consultant["name"]
    engine.profile.consultant_id = consultant.get("agent_id", "")
    engine.profile.consultant_team = consultant.get("team", "")
    engine.crm.write_lead(engine.profile, engine.conversation_id)
    engine.crm.log_event(
        "handoff", engine.conversation_id, engine.profile.wa_id,
        {"consultant": consultant["name"], "team": consultant.get("team", ""),
         "agent_id": consultant.get("agent_id", ""), "region": consultant.get("region", ""),
         "band": engine.profile.band, "intent": engine.profile.intent_action},
    )
    if engine.after_hours:
        engine._emit(Message.text_msg(
            f"Our team is offline right now, but {consultant['name']} has your full brief "
            "and will call you first thing tomorrow. Anything else I can help with meanwhile?",
            node="N11",
        ))
    else:
        engine._emit(Message.text_msg(
            f"You're now with {consultant['name']}, your property consultant - they'll reach out "
            "within 2 hours with the full details. Anything else I can help with meanwhile?",
            node="N9",
        ))
    return ENDED


@_do("N10_NURTURE")
def _nurture(engine: Engine) -> str:
    """Node 10 - queue template follow-ups inside WhatsApp's rules."""
    engine.rescore()
    engine.crm.upsert_profile(engine.profile)
    engine.crm.log_event(
        "nurture_scheduled", engine.conversation_id, engine.profile.wa_id,
        {"band": engine.profile.band,
         "followups": ["24h: resume where we left off", "on new inventory: matching launch"]},
    )
    engine._emit(Message.text_msg(
        "I'll keep an eye out and send you anything new that fits. Reply STOP any time to opt out.",
        node="N10",
    ))
    return ENDED


@_do("N11_RESUME")
def _resume_after_fallback(engine: Engine) -> str:
    last = engine.profile.last_node
    return last if last in ASKS else "N8"


def _card(unit: Unit) -> Card:
    lines = inv.unit_card_lines(unit)
    return Card(
        unit_id=unit.unit_id,
        title=f"{unit.project} · {unit.bedrooms}-bed {unit.unit_type}",
        subtitle=(
            f"{unit.size_sqm} m² · {AREA_LABELS.get(unit.area, unit.area)} · "
            f"{unit.property_status}"
        ),
        price_line=lines["price"],
        payment_line=lines["payment"],
        delivery_line=lines["delivery"],
        image=unit.image,
        floor_plan=unit.floor_plan,
        actions=[
            QuickReply("View details & more photos", f"details:{unit.unit_id}"),
            QuickReply("Floor plan", f"plan:{unit.unit_id}"),
            QuickReply("Send brochure", f"brochure:{unit.unit_id}"),
            QuickReply("Book a viewing", f"viewing:{unit.unit_id}"),
        ],
    )


def _upsell_prompt(engine: Engine) -> str:
    """Node 7 copy, tuned per buyer type."""
    unit, reason = engine.upsell  # type: ignore[misc]
    if engine.profile.buyer_type == "investor":
        return f"One more worth a look - {unit.project} offers {reason}. Want to see it?"
    return (
        f"For {reason} you could move up to a {unit.bedrooms}-bed {unit.unit_type} "
        f"({unit.size_sqm} m²) at {unit.project} - want to see it?"
    )


Engine.upsell_prompt = _upsell_prompt  # type: ignore[attr-defined]
