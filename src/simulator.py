"""
Run the buyer flow without WhatsApp - scripted scenarios or an interactive REPL.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .crm import CRM
from .flow import Engine
from .inventory import load_units
from .models import Message

BOT = "\033[36m"
BUYER = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def render(message: Message, color: bool = True) -> str:
    c, r = (BOT, RESET) if color else ("", "")
    lines = []
    if message.text:
        lines.append(f"{c}BOT{r}  {message.text}")
    if message.quick_replies:
        chips = "  ".join(f"[{q.label}]" for q in message.quick_replies)
        lines.append(f"     {DIM if color else ''}{chips}{r}")
    for card in message.cards:
        lines.append(f"     ┌ {card.title}")
        lines.append(f"     │ {card.subtitle}")
        lines.append(f"     │ {card.price_line} · {card.payment_line}")
        lines.append(f"     │ {card.delivery_line}")
        lines.append(
            f"     └ {DIM if color else ''}[View details] [Floor plan] [Send brochure] "
            f"[Book a viewing]{r}"
        )
    return "\n".join(lines)


def run_script(
    crm: CRM,
    wa_id: str,
    name: str,
    replies: Iterable[str],
    source: str = "meta_ad",
    source_ref: str = "",
    now_hour: int = 12,
    echo: bool = True,
    color: bool = True,
    units: Optional[list] = None,
) -> Engine:
    """Drive one conversation with a canned list of buyer replies."""
    engine = Engine.for_buyer(
        crm, wa_id=wa_id, name=name, source=source, source_ref=source_ref,
        now_hour=now_hour, units=units if units is not None else load_units(),
    )
    for message in engine.start():
        if echo:
            print(render(message, color))
    for reply in replies:
        if engine.ended:
            break
        if echo:
            print(f"{BUYER if color else ''}YOU{RESET if color else ''}  {reply}")
        for message in engine.handle(reply):
            if echo:
                print(render(message, color))
    return engine


def transcript_markdown(crm: CRM, engine: Engine, title: str) -> str:
    """A readable turn-by-turn transcript for the samples/ folder."""
    out = [f"# {title}", "", f"`{engine.conversation_id}` · buyer `{engine.profile.wa_id}` · "
           f"source `{engine.profile.source}` · band **{engine.profile.band}** "
           f"(score {engine.profile.score})", ""]
    for entry in crm.transcript(engine.conversation_id):
        who = "**Bot**" if entry["role"] == "bot" else "**Buyer**"
        node = f" `{entry['node']}`" if entry["node"] else ""
        body = entry["text"].replace("\n", "  \n> ")
        out.append(f"{who}{node}  \n> {body}")
        out.append("")
    return "\n".join(out)


def chat(crm: CRM, wa_id: str = "+201000000001", name: str = "") -> None:
    """Interactive REPL against the same engine the webhook uses."""
    engine = Engine.for_buyer(crm, wa_id=wa_id, name=name)
    for message in engine.start():
        print(render(message))
    while not engine.ended:
        try:
            text = input(f"{BUYER}YOU{RESET}  ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        for message in engine.handle(text):
            print(render(message))
    print(f"\n{DIM}Conversation {engine.conversation_id} · "
          f"{engine.profile.band} ({engine.profile.score}){RESET}")
