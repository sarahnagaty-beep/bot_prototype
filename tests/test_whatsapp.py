"""Cloud API payload mapping and inbound parsing."""

from src.models import Card, Message, QuickReply
from src.whatsapp import inbound_text, to_payloads


def test_three_options_become_reply_buttons():
    message = Message.choice("Pick one", [QuickReply(f"Option {i}", f"o{i}") for i in range(3)])
    payload = to_payloads(message, "+201000000001")[0]
    assert payload["interactive"]["type"] == "button"
    assert len(payload["interactive"]["action"]["buttons"]) == 3


def test_more_than_three_options_become_a_list_message():
    message = Message.choice("Pick one", [QuickReply(f"Option {i}", f"o{i}") for i in range(5)])
    payload = to_payloads(message, "+201000000001")[0]
    assert payload["interactive"]["type"] == "list"
    assert len(payload["interactive"]["action"]["sections"][0]["rows"]) == 5


def test_button_titles_stay_within_the_twenty_character_limit():
    message = Message.choice("Pick", [QuickReply("An extremely long button label", "x")])
    button = to_payloads(message, "+2010")[0]["interactive"]["action"]["buttons"][0]
    assert len(button["reply"]["title"]) <= 20


def test_carousel_sends_an_image_and_actions_per_card():
    card = Card(
        unit_id="NC-APT-301", title="Palm Ridge · 3-bed apartment", subtitle="165 m²",
        price_line="9.2M EGP", payment_line="10% down", delivery_line="delivery 2027",
        image="units/a.jpg", floor_plan="plans/a.pdf",
        actions=[QuickReply("Book a viewing", "viewing:NC-APT-301")],
    )
    payloads = to_payloads(Message(kind="carousel", text="Matches", cards=[card]), "+2010")
    kinds = [p["type"] for p in payloads]
    assert kinds == ["text", "image", "interactive"]
    assert "9.2M EGP" in payloads[1]["image"]["caption"]


def test_inbound_button_reply_carries_the_ad_referral():
    parsed = inbound_text({
        "contacts": [{"profile": {"name": "Hana"}, "wa_id": "201001234567"}],
        "messages": [{
            "from": "201001234567", "type": "interactive",
            "interactive": {"button_reply": {"id": "cash", "title": "Cash"}},
            "referral": {"source_id": "AD_123"},
        }],
    })
    assert parsed == {
        "wa_id": "201001234567", "name": "Hana", "text": "cash",
        "source": "meta_ad", "source_ref": "AD_123", "message_id": "",
    }


def test_inbound_ignores_status_only_payloads():
    assert inbound_text({"statuses": [{"status": "delivered"}]}) is None
