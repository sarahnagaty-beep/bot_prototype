"""
Lightweight NLU for the prototype.

The script (Part A, §6) specifies Egyptian Arabic, Franco-Arabic and
code-switching as *next-iteration* capabilities. This prototype is English-first
but the matcher is built so a dialect lexicon can be dropped in without
touching the flow: add entries to SYNONYMS and the same code path resolves them.
"""

from __future__ import annotations

import re
from typing import Optional

# value -> phrases that resolve to it. Arabic / Franco-Arabic rows are stubs
# showing where the next iteration plugs in.
SYNONYMS: dict[str, list[str]] = {
    "yes": ["yes", "yeah", "yep", "sure", "ok", "okay", "go", "lets go", "let's go", "aywa", "tamam"],
    "browsing": ["browsing", "just browsing", "just looking", "not now", "later"],
    "end_user": ["live", "buy to live", "live in", "for me", "family", "end user", "residence", "home"],
    "investor": ["invest", "investment", "investor", "roi", "yield", "return"],
    "broker": ["broker", "agent", "brokerage", "i'm a broker", "im a broker"],
    "new_cairo": ["new cairo", "newcairo", "tagamoa", "tagamo3", "fifth settlement"],
    "sheikh_zayed": ["sheikh zayed", "zayed", "el sheikh zayed"],
    "north_coast": ["north coast", "sahel", "el sahel", "coast", "sidi abdel rahman"],
    "6th_october": ["6th october", "6 october", "october", "sixth october"],
    "not_sure": ["not sure", "dont know", "don't know", "no idea", "unsure", "help me"],
    "apartment": ["apartment", "flat", "apt", "sha2a"],
    "duplex": ["duplex"],
    "townhouse": ["townhouse", "town house"],
    "twinhouse": ["twinhouse", "twin house", "twin"],
    "villa": ["villa", "standalone"],
    "chalet": ["chalet", "chalets"],
    "primary": ["primary", "developer", "from the developer", "new"],
    "resale": ["resale", "secondary", "used", "second hand"],
    "both": ["both", "either", "any", "no preference", "doesnt matter", "doesn't matter"],
    "ready": ["ready", "ready to move", "rtm", "immediate", "delivered", "finished"],
    "off_plan": ["off plan", "off-plan", "offplan", "under construction", "future"],
    "cash": ["cash", "full payment", "one payment", "outright"],
    "installments": ["installment", "installments", "instalments", "plan", "payment plan", "monthly"],
    "monthly": ["monthly", "per month", "month"],
    "quarterly": ["quarterly", "per quarter", "quarter", "3 months"],
    "equal": ["equal", "same", "flat", "even"],
    "backloaded": ["backloaded", "back loaded", "back-loaded", "smaller now", "later"],
    "appreciation": ["appreciation", "capital", "capital appreciation", "resell", "value"],
    "yield": ["yield", "rental", "rent", "rental yield", "income"],
    "asap": ["asap", "now", "immediately", "within a month", "this month", "urgent", "1 month"],
    "1_3_months": ["1-3", "1 to 3", "two months", "three months", "couple of months", "next quarter"],
    "3_6_months": ["3-6", "3 to 6", "six months", "half a year", "end of year"],
    "exploring": ["exploring", "just exploring", "no rush", "researching", "looking around"],
    "viewing": ["viewing", "visit", "see it", "book a viewing", "tour", "site visit"],
    "consultant": ["consultant", "agent", "human", "person", "call me", "talk to"],
    "brochure": ["brochure", "pdf", "details", "catalogue", "catalog"],
    "reserve": ["reserve", "reservation", "express interest", "interested", "book it", "hold it"],
    "stop": ["stop", "unsubscribe", "opt out", "remove me"],
    "pick_up": ["pick up", "continue", "resume", "where we left off", "same"],
    "2_3": ["2-3", "2 or 3", "two or three", "a couple", "couple", "two", "three"],
    "portfolio": ["portfolio", "4+", "four or more", "more than 3", "several", "many"],
    "dp_10": ["10", "10%", "ten"],
    "dp_15": ["15", "15%", "fifteen"],
    "dp_20": ["20", "20%", "20%+", "twenty", "more than 20"],
    "dp_unsure": ["not sure", "no idea", "dont know", "don't know", "unsure"],
    "widen": ["widen", "wider", "expand", "more options", "stretch", "open it up"],
    "alert": ["alert", "notify", "let me know", "tell me when", "keep me posted"],
    "keep_going": ["keep going", "carry on", "continue with you", "with you", "bot"],
    "no": ["no", "nope", "no thanks", "not now", "skip"],
    "start_fresh": ["start fresh", "fresh", "restart", "new search", "start over"],
}

_MULTIPLIERS = {"m": 1_000_000, "million": 1_000_000, "mn": 1_000_000, "k": 1_000, "thousand": 1_000}


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s'\-+]", " ", (text or "").lower()).strip()


def resolve(text: str, allowed: list[str]) -> tuple[Optional[str], float]:
    """Resolve free text to one of `allowed` values.

    Returns (value, confidence). Confidence 1.0 = exact/synonym hit,
    0.6 = partial token overlap, 0.0 = no match.
    """
    norm = normalize(text)
    if not norm:
        return None, 0.0

    # Numeric shortcut: "2" picks the second option.
    if norm.isdigit():
        idx = int(norm) - 1
        if 0 <= idx < len(allowed):
            return allowed[idx], 1.0

    # Hyphen-insensitive pass: "2-3" and "2 3" must both hit option value "2_3".
    norm_alt = norm.replace("-", " ")
    for value in allowed:
        phrases = SYNONYMS.get(value, []) + [
            value.replace("_", " "), value.replace("_", "-"), value.replace("_", "")
        ]
        for phrase in phrases:
            if not phrase:
                continue
            if phrase in norm or phrase.replace("-", " ") in norm_alt:
                return value, 1.0

    # Partial overlap fallback.
    best, best_score = None, 0.0
    tokens = set(norm_alt.split())
    for value in allowed:
        vocab = set()
        for phrase in SYNONYMS.get(value, []) + [value.replace("_", " ")]:
            vocab |= set(phrase.split())
        overlap = len(tokens & vocab)
        if overlap and overlap / max(len(vocab), 1) > best_score:
            best, best_score = value, overlap / max(len(vocab), 1)
    if best and best_score >= 0.3:
        return best, 0.6
    return None, 0.0


def parse_money(text: str) -> Optional[int]:
    """'around 10 million' / '9.5m' / '7,000,000' -> 10000000."""
    norm = normalize(text)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(m|mn|million|k|thousand)?", norm)
    if not m:
        return None
    raw = float(m.group(1).replace(",", ""))
    unit = m.group(2)
    if unit:
        return int(raw * _MULTIPLIERS[unit])
    return int(raw) if raw > 100_000 else int(raw * 1_000_000)


def parse_budget_range(text: str) -> Optional[tuple[Optional[int], Optional[int]]]:
    """'7-12m' / 'under 4m' / '20m+' -> (min, max)."""
    norm = normalize(text)
    if "under" in norm or "less" in norm or "below" in norm:
        top = parse_money(norm)
        return (0, top) if top else None
    if "+" in norm or "above" in norm or "more" in norm or "over" in norm:
        low = parse_money(norm)
        return (low, None) if low else None
    parts = re.split(r"\s*(?:-|to|–|—)\s*", norm)
    if len(parts) == 2:
        low, high = parse_money(parts[0]), parse_money(parts[1])
        # "7-12m": the unit trails the pair, so scale the bare first number.
        if low and high and low < 1_000_000 <= high:
            low *= 1_000_000
        if low and high:
            return (low, high)
    single = parse_money(norm)
    if single:
        return (int(single * 0.85), int(single * 1.15))
    return None


def parse_bedrooms(text: str) -> Optional[str]:
    norm = normalize(text)
    m = re.search(r"(\d)\s*(?:\+)?\s*(?:bed|bedroom|br)?", norm)
    if not m:
        return None
    n = int(m.group(1))
    if n <= 0 or n > 9:
        return None
    return "4+" if n >= 4 else str(n)


def is_opt_out(text: str) -> bool:
    return resolve(text, ["stop"])[0] == "stop"
