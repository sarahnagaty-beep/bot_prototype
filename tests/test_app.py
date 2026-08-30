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


def test_a_restart_resumes_the_buyer_where_they_stopped(client, crm):
    """The failure a UAT tester reports as 'the bot forgot me'."""
    client.post("/webhook", json=_inbound("hi"))
    client.post("/webhook", json=_inbound("yes"))
    client.post("/webhook", json=_inbound("buy to live in"))
    before = crm.load_session("201001234567")
    assert before["awaiting"] == "N2"  # asking for the area

    app_module.sessions.clear()  # simulate a process restart / second worker

    resumed = client.post("/webhook", json=_inbound("New Cairo")).json()["handled"][0]
    assert resumed["conversation_id"] == before["conversation_id"]
    assert crm.get_profile("201001234567").preferred_areas == ["new_cairo"]
    assert resumed["awaiting"] == "N2_TYPE"  # moved on, did not start over


def test_a_retried_webhook_is_not_answered_twice(client, crm):
    payload = _inbound("hi")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"] = "wamid.TEST1"

    first = client.post("/webhook", json=payload).json()["handled"][0]
    second = client.post("/webhook", json=payload).json()["handled"][0]

    assert first["messages_sent"] >= 1
    assert second == {"wa_id": "201001234567", "skipped": "duplicate"}


def test_a_forged_webhook_is_rejected_when_the_app_secret_is_set(client, monkeypatch):
    monkeypatch.setattr(app_module, "APP_SECRET", "top-secret")
    assert client.post("/webhook", json=_inbound("hi")).status_code == 403


def test_a_correctly_signed_webhook_is_accepted(client, monkeypatch):
    import hashlib
    import hmac
    import json

    monkeypatch.setattr(app_module, "APP_SECRET", "top-secret")
    body = json.dumps(_inbound("hi")).encode()
    signature = hmac.new(b"top-secret", body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhook", content=body,
        headers={"content-type": "application/json", "x-hub-signature-256": f"sha256={signature}"},
    )
    assert response.status_code == 200


def test_health_reports_how_the_bot_is_wired(client):
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["whatsapp"] in ("live", "dry-run")
    assert health["inventory_units"] > 0


def test_a_plain_greeting_is_not_treated_as_an_answer(client, crm):
    """"hi" should get the greeting, not the greeting plus 'I didn't catch that'."""
    result = client.post("/webhook", json=_inbound("hi")).json()["handled"][0]
    assert result["messages_sent"] == 1
    assert result["awaiting"] == "N0"
    assert not any(e["event"] == "low_confidence" for e in crm.events())


def test_a_click_to_chat_button_answers_the_greeting_immediately(client):
    payload = {"entry": [{"changes": [{"value": {
        "contacts": [{"profile": {"name": "Direct"}, "wa_id": "201000000888"}],
        "messages": [{
            "from": "201000000888", "id": "wamid.CTC1", "type": "interactive",
            "interactive": {"button_reply": {"id": "yes", "title": "Yes"}},
            "referral": {"source_id": "AD_X"},
        }],
    }}]}]}
    result = client.post("/webhook", json=payload).json()["handled"][0]
    assert result["awaiting"] == "N1"  # already past the opt-in
