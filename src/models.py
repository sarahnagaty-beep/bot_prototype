"""
Domain models for the Buyer Bot prototype.

Mirrors the capture table in "Buyer Bot - Conversation Prototype" (Part A, §2)
and the outbound message shapes WhatsApp supports (text, buttons, list,
carousel of unit cards).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


AREA_LABELS = {
    "new_cairo": "New Cairo",
    "sheikh_zayed": "Sheikh Zayed",
    "north_coast": "North Coast",
    "6th_october": "6th October",
}


# --------------------------------------------------------------------------
# Outbound message shapes
# --------------------------------------------------------------------------

@dataclass
class QuickReply:
    """A button / list row. `label` is shown, `value` is stored."""

    label: str
    value: str


@dataclass
class Card:
    """One unit card in a recommendation carousel."""

    unit_id: str
    title: str
    subtitle: str
    price_line: str
    payment_line: str
    delivery_line: str
    image: str
    floor_plan: str
    actions: list[QuickReply] = field(default_factory=list)


@dataclass
class Message:
    """An outbound WhatsApp message."""

    kind: str  # text | buttons | list | carousel
    text: str = ""
    quick_replies: list[QuickReply] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    node: str = ""

    @classmethod
    def text_msg(cls, text: str, node: str = "") -> "Message":
        return cls(kind="text", text=text, node=node)

    @classmethod
    def choice(cls, text: str, options: list[QuickReply], node: str = "") -> "Message":
        # WhatsApp allows 3 reply buttons; more options must go in a list message.
        kind = "buttons" if len(options) <= 3 else "list"
        return cls(kind=kind, text=text, quick_replies=options, node=node)


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------

@dataclass
class Unit:
    unit_id: str
    project: str
    developer: str
    area: str
    unit_type: str
    bedrooms: int
    size_sqm: int
    price_egp: int
    property_status: str      # primary | resale
    delivery: str             # ready | off_plan
    delivery_year: Optional[int]
    down_payment_pct: int
    tenor_years: int
    payment_frequency: str    # monthly | quarterly
    payment_structure: str    # equal | backloaded
    installment_egp: int      # per payment_frequency period
    rental_yield_pct: float
    appreciation_pct: float
    image: str
    floor_plan: str
    highlights: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Unit":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# Buyer profile - the product's actual output
# --------------------------------------------------------------------------

@dataclass
class BuyerProfile:
    """Every field the conversation must capture (Part A, §2)."""

    # Identity & attribution
    wa_id: str = ""
    name: str = ""
    language: str = "en"
    source: str = ""              # meta_ad | website | qr | direct
    source_ref: str = ""          # ad id / campaign / page
    entry_at: str = field(default_factory=_now)
    consent: bool = False
    opted_out: bool = False

    # Qualification
    buyer_type: str = ""          # end_user | investor | broker
    preferred_areas: list[str] = field(default_factory=list)
    area_priority: str = ""       # work | schools | coast | quiet
    unit_type: str = ""
    bedrooms: str = ""
    property_status: str = ""     # primary | resale | both
    delivery_preference: str = "" # ready | off_plan | either

    # Investor branch
    investor_goal: str = ""       # appreciation | yield | both
    units_target: str = ""        # 1 | 2-3 | portfolio

    # Budget & payment
    budget_band: str = ""
    budget_min: Optional[int] = None
    budget_max: Optional[int] = None
    payment_type: str = ""        # cash | installments
    down_payment_pct: Optional[int] = None
    tenor_years: Optional[int] = None
    payment_frequency: str = ""   # monthly | quarterly
    payment_structure: str = ""   # equal | backloaded

    # Intent
    timeline: str = ""            # asap | 1_3_months | 3_6_months | exploring
    intent_action: str = ""       # viewing | consultant | brochure | reserve

    # Derived / operational
    score: int = 0
    band: str = "cold"
    score_breakdown: list[dict[str, Any]] = field(default_factory=list)
    completed_flow: bool = False
    shortlisted_unit_ids: list[str] = field(default_factory=list)
    selected_unit_id: str = ""
    concerns: list[str] = field(default_factory=list)
    consultant: str = ""
    last_node: str = "N0"
    sessions: int = 1
    updated_at: str = field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BuyerProfile":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    # -- convenience --------------------------------------------------------

    @property
    def is_known(self) -> bool:
        """Enough saved context to offer a 'pick up where we left off'."""
        return bool(self.budget_band and (self.preferred_areas or self.unit_type))

    def summary_line(self) -> str:
        bits = []
        if self.bedrooms:
            bits.append(f"{self.bedrooms}-bed")
        if self.unit_type:
            bits.append(self.unit_type.replace("_", " "))
        if self.preferred_areas:
            pretty = [AREA_LABELS.get(a, a.replace("_", " ")) for a in self.preferred_areas]
            bits.append("in " + ", ".join(pretty))
        if self.budget_band:
            bits.append(f"around {self.budget_band}")
        return " ".join(bits)


@dataclass
class TranscriptEntry:
    role: str      # bot | buyer | system
    text: str
    node: str = ""
    at: str = field(default_factory=_now)
