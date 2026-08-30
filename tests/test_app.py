"""The service layer: Meta verification, webhook turns, and the read API."""

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from src import app as app_module  # noqa: E402


@pytest.fixture()
def client(crm, monkeypatch):
    monkeypatch.setattr(app_module, "crm", crm)
    monkeypatch.setattr(app_module, "sessions", {})
    return TestClient(app_module.app)


def _inbound(text: str, wa_id: str = "201001234567") -> dict:
    return {"entry": [{"changes": [{"value": {
        "contacts": [{"profile": {"name": "Hana"}, "wa_id": wa_id}],
        "messages": [{"from": wa_id, "type": "text", "text": {"body": text},
                      "referral": {"source_id": "AD_TEST"}}],
    }}]}]}


def test_verification_echoes_the_challenge(client):
    response = client.get("/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "buyer-bot-prototype",
        "hub.challenge": "12345",
    })
    assert response.status_code == 200 and response.text == "12345"


def test_verification_rejects_a_wrong_token(client):
    response = client.get("/webhook", params={
        "hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1",
    })
    assert response.status_code == 403


def test_webhook_greets_then_continues_the_same_conversation(client, crm):
    first = client.post("/webhook", json=_inbound("hi")).json()
    assert first["handled"][0]["messages_sent"] >= 1
    conversation = first["handled"][0]["conversation_id"]

    client.post("/webhook", json=_inbound("yes"))
    second = client.post("/webhook", json=_inbound("buy to live in")).json()
    assert second["handled"][0]["conversation_id"] == conversation

    profile = crm.get_profile("201001234567")
    assert profile.buyer_type == "end_user"
    assert profile.source_ref == "AD_TEST"


def test_snapshot_and_brief_endpoints(client, crm):
    from src.demo import build_demo, seed_pilot_outcomes

    build_demo(crm, write_samples=False)
    seed_pilot_outcomes(crm, profiled=20, control=20)

    snapshot = client.get("/api/snapshot").json()
    assert snapshot["leads"], "expected leads in the snapshot"

    lead_id = snapshot["leads"][0]["lead_id"]
    brief = client.get(f"/api/leads/{lead_id}").json()
    assert brief["buyer"]["name"]
    assert brief["scoring"]["band"] in ("hot", "warm", "cold")
    assert brief["transcript"], "the consultant must receive the transcript"

    assert client.get("/api/leads/L-nope").status_code == 404


def test_dashboard_is_served_as_a_document(client):
    html = client.get("/").text
    assert html.startswith("<!doctype html>")
    assert "Buyer Profiler Desk" in html
