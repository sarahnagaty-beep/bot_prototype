"""
Service layer: Meta WhatsApp webhook + the dashboard's read API.

    uvicorn src.app:app --reload

Endpoints
    GET  /                      the dashboard
    GET  /webhook               Meta verification handshake
    POST /webhook               inbound WhatsApp messages
    GET  /api/snapshot          every metric the dashboard renders
    GET  /api/leads/{lead_id}   full buyer brief + transcript
    GET  /api/inventory         the sample inventory feed
    POST /api/simulate          run a scripted conversation (demo button)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import metrics
from .brief import build_brief
from .crm import CRM
from .flow import Engine
from .inventory import load_units
from .dashboard_build import wrap
from .whatsapp import WhatsAppClient, inbound_text

VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "buyer-bot-prototype")
DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"

app = FastAPI(title="Buyer Bot Prototype", version="0.2.0")
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


@app.get("/api/snapshot")
def snapshot() -> JSONResponse:
    crm.load()
    return JSONResponse(metrics.snapshot(crm))


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


@app.post("/webhook")
async def receive(request: Request) -> JSONResponse:
    payload = await request.json()
    handled = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            message = inbound_text(change.get("value", {}))
            if not message:
                continue
            handled.append(_handle(message))
    return JSONResponse({"status": "ok", "handled": handled})


def _handle(message: dict[str, Any]) -> dict[str, Any]:
    """One inbound message -> engine turn -> outbound sends."""
    wa_id = message["wa_id"]
    engine = sessions.get(wa_id)

    if engine is None or engine.ended:
        engine = Engine.for_buyer(
            crm,
            wa_id=wa_id,
            name=message.get("name", ""),
            source=message.get("source", "direct"),
            source_ref=message.get("source_ref", ""),
            units=units,
        )
        sessions[wa_id] = engine
        outbound = engine.start()
        # The buyer's first message also answers the greeting when it is a
        # button reply from the ad's click-to-chat.
        if message.get("text"):
            outbound = outbound + engine.handle(message["text"])
    else:
        outbound = engine.handle(message["text"])

    for msg in outbound:
        client.send(msg, wa_id)
    if engine.ended:
        sessions.pop(wa_id, None)

    return {
        "wa_id": wa_id,
        "conversation_id": engine.conversation_id,
        "messages_sent": len(outbound),
        "band": engine.profile.band,
    }
