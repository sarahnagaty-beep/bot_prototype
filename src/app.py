"""
Service layer: Meta WhatsApp webhook + the dashboard's read API.

    uvicorn src.app:app --reload

Endpoints
    GET  /                      the dashboard
    GET  /webhook               Meta verification handshake
    POST /webhook               inbound WhatsApp messages
    GET  /api/snapshot          every metric the dashboard renders
    GET  /api/leads             one filtered, paginated page of the lead queue
    GET  /api/leads/{lead_id}   full buyer brief + transcript
    GET  /api/floor             team and agent rollups
    GET  /api/inventory         the sample inventory feed
    POST /api/simulate          run a scripted conversation (demo button)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

try:  # optional: makes a local .env work without exporting by hand
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a convenience, not a requirement
    pass

from . import metrics
from .brief import build_brief
from .crm import CRM
from .flow import Engine
from .inventory import load_units
from .dashboard_build import wrap
from .whatsapp import WhatsAppClient, inbound_text

VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "buyer-bot-prototype")
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")
DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"

log = logging.getLogger("buyer-bot")
app = FastAPI(title="Buyer Bot Prototype", version="0.3.0")
crm = CRM()
units = load_units()
client = WhatsAppClient()
sessions: dict[str, Engine] = {}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    if not DASHBOARD.exists():
        raise HTTPException(404, "dashboard/index.html not found")
    # The file is an artifact-style fragment; serving needs a real document.
    return HTMLResponse(wrap(DASHBOARD.read_text()))


@app.get("/health")
def health() -> JSONResponse:
    """Readiness probe: is the bot wired up, and is it really sending?"""
    return JSONResponse({
        "status": "ok",
        "whatsapp": "live" if client.live else "dry-run",
        "signature_check": bool(APP_SECRET),
        "inventory_units": len(units),
        "leads": len(crm.leads()),
    })


@app.get("/api/snapshot")
def snapshot() -> JSONResponse:
    crm.load()
    return JSONResponse(metrics.snapshot(crm))


@app.get("/api/leads")
def leads(
    query: str = "", band: str = "", region: str = "", team: str = "",
    buyer_type: str = "", sort: str = "score", page: int = 1, page_size: int = 25,
) -> JSONResponse:
    """The lead queue is paginated: a floor of hundreds of agents produces
    thousands of leads, and no browser wants them in one response."""
    crm.load()
    return JSONResponse(metrics.lead_page(
        crm, query=query, band=band, region=region, team=team,
        buyer_type=buyer_type, sort=sort, page=page, page_size=page_size,
    ))


@app.get("/api/floor")
def floor() -> JSONResponse:
    crm.load()
    return JSONResponse(metrics.floor(crm))


@app.get("/api/leads/{lead_id}")
def lead_detail(lead_id: str) -> JSONResponse:
    crm.load()
    for lead in crm.leads():
        if lead["lead_id"] != lead_id:
            continue
        from .models import BuyerProfile

        profile = BuyerProfile.from_dict(lead["profile"])
        brief = build_brief(profile, units, crm.transcript(lead["conversation_id"]))
        return JSONResponse(brief)
    raise HTTPException(404, f"unknown lead {lead_id}")


@app.get("/api/inventory")
def inventory() -> JSONResponse:
    return JSONResponse([unit.to_dict() for unit in units])


@app.post("/api/simulate")
async def simulate(request: Request) -> JSONResponse:
    """Drive one conversation from a list of buyer replies - powers the demo."""
    body = await request.json()
    from .simulator import run_script

    engine = run_script(
        crm,
        wa_id=body.get("wa_id", "+201000000099"),
        name=body.get("name", "Demo buyer"),
        replies=body.get("replies", []),
        source=body.get("source", "meta_ad"),
        source_ref=body.get("source_ref", "DEMO"),
        echo=False,
        units=units,
    )
    return JSONResponse(
        {
            "conversation_id": engine.conversation_id,
            "band": engine.profile.band,
            "score": engine.profile.score,
            "transcript": crm.transcript(engine.conversation_id),
        }
    )


# ---------------------------------------------------------------------------
# WhatsApp webhook
# ---------------------------------------------------------------------------

@app.get("/webhook", response_class=PlainTextResponse)
def verify(request: Request) -> PlainTextResponse:
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(403, "verification failed")


def verify_signature(body: bytes, header: str) -> bool:
    """Meta signs every webhook with the app secret (X-Hub-Signature-256).

    Without APP_SECRET set the check is skipped, which is fine locally but must
    not be how the UAT host runs - anyone could post fake leads at it.
    """
    if not APP_SECRET:
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


@app.post("/webhook")
async def receive(request: Request) -> JSONResponse:
    body = await request.body()
    if not verify_signature(body, request.headers.get("x-hub-signature-256", "")):
        log.warning("rejected webhook with a bad signature")
        raise HTTPException(403, "bad signature")

    payload = await request.json()
    handled = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            message = inbound_text(change.get("value", {}))
            if not message:
                continue
            if crm.seen_message(message.get("message_id", "")):
                handled.append({"wa_id": message["wa_id"], "skipped": "duplicate"})
                continue
            handled.append(_handle(message))
    # Always 200 once the payload is understood; a non-200 makes Meta retry.
    return JSONResponse({"status": "ok", "handled": handled})


def _session_for(message: dict[str, Any]) -> tuple[Engine, bool]:
    """The buyer's live conversation, resumed across restarts. Returns
    (engine, is_new)."""
    wa_id = message["wa_id"]

    engine = sessions.get(wa_id)
    if engine is None:
        state = crm.load_session(wa_id)
        if state and not state.get("ended"):
            engine = Engine.restore(crm, state, units)
    if engine is not None and not engine.ended:
        sessions[wa_id] = engine
        return engine, False

    engine = Engine.for_buyer(
        crm,
        wa_id=wa_id,
        name=message.get("name", ""),
        source=message.get("source", "direct"),
        source_ref=message.get("source_ref", ""),
        units=units,
    )
    sessions[wa_id] = engine
    return engine, True


def _handle(message: dict[str, Any]) -> dict[str, Any]:
    """One inbound message -> engine turn -> outbound sends."""
    wa_id = message["wa_id"]
    engine, is_new = _session_for(message)

    if is_new:
        outbound = engine.start()
        # The opening message answers the greeting only when it really is an
        # answer - a click-to-chat button payload, or "yes". A plain "hi" is
        # left alone so the buyer isn't greeted and corrected in one breath.
        text = message.get("text", "")
        if text and engine.can_answer(text):
            outbound = outbound + engine.handle(text)
    else:
        outbound = engine.handle(message["text"])

    for msg in outbound:
        try:
            client.send(msg, wa_id)
        except Exception:  # a failed send must not lose the buyer's place
            log.exception("send failed for %s at node %s", wa_id, engine.awaiting)

    crm.save_session(wa_id, engine.state_dict())
    if engine.ended:
        sessions.pop(wa_id, None)
        crm.clear_session(wa_id)

    return {
        "wa_id": wa_id,
        "conversation_id": engine.conversation_id,
        "messages_sent": len(outbound),
        "band": engine.profile.band,
        "awaiting": engine.awaiting,
    }
