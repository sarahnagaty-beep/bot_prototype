"""
WhatsApp Cloud API adapter.

Maps the engine's abstract Message objects onto Cloud API payloads:
  text            -> type: text
  buttons (<=3)   -> interactive.button
  list (>3)       -> interactive.list
  carousel        -> one image message per unit card (the Cloud API has no
                     native carousel outside template messages; a real
                     deployment sends an approved carousel template).

With no credentials configured the client logs payloads instead of sending, so
the whole flow is runnable offline.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from .models import Message

GRAPH_VERSION = "v21.0"
MEDIA_BASE = os.environ.get("MEDIA_BASE_URL", "https://cdn.example.com/inventory")


def to_payloads(message: Message, to: str) -> list[dict[str, Any]]:
    """One Message may become several Cloud API messages (carousel)."""
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to}

    if message.kind == "text":
        return [{**base, "type": "text", "text": {"preview_url": False, "body": message.text}}]

    if message.kind == "buttons":
        return [
            {
                **base,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": message.text},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {"id": q.value, "title": q.label[:20]},
                            }
                            for q in message.quick_replies[:3]
                        ]
                    },
                },
            }
        ]

    if message.kind == "list":
        return [
            {
                **base,
                "type": "interactive",
                "interactive": {
                    "type": "list",
                    "body": {"text": message.text},
                    "action": {
                        "button": "Choose",
                        "sections": [
                            {
                                "title": "Options",
                                "rows": [
                                    {"id": q.value, "title": q.label[:24]}
                                    for q in message.quick_replies[:10]
                                ],
                            }
                        ],
                    },
                },
            }
        ]

    if message.kind == "carousel":
        payloads: list[dict[str, Any]] = []
        if message.text:
            payloads.append(
                {**base, "type": "text", "text": {"preview_url": False, "body": message.text}}
            )
        for card in message.cards:
            caption = (
                f"*{card.title}*\n{card.subtitle}\n{card.price_line}\n"
                f"{card.payment_line}\n{card.delivery_line}"
            )
            payloads.append(
                {
                    **base,
                    "type": "image",
                    "image": {"link": f"{MEDIA_BASE}/{card.image}", "caption": caption},
                }
            )
            payloads.append(
                {
                    **base,
                    "type": "interactive",
                    "interactive": {
                        "type": "button",
                        "body": {"text": f"{card.title} - what next?"},
                        "action": {
                            "buttons": [
                                {"type": "reply", "reply": {"id": a.value, "title": a.label[:20]}}
                                for a in card.actions[:3]
                            ]
                        },
                    },
                }
            )
        return payloads

    return [{**base, "type": "text", "text": {"preview_url": False, "body": message.text}}]


def inbound_text(value: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Pull (wa_id, name, text, referral) out of a Cloud API webhook payload."""
    try:
        messages = value["messages"]
    except (KeyError, TypeError):
        return None
    if not messages:
        return None

    message = messages[0]
    contacts = value.get("contacts") or [{}]
    profile_name = (contacts[0].get("profile") or {}).get("name", "")
    wa_id = message.get("from") or contacts[0].get("wa_id", "")

    if message.get("type") == "interactive":
        interactive = message["interactive"]
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text = reply.get("id") or reply.get("title", "")
    elif message.get("type") == "button":
        text = message["button"].get("payload", "")
    else:
        text = (message.get("text") or {}).get("body", "")

    referral = message.get("referral") or {}
    return {
        "wa_id": wa_id,
        "name": profile_name,
        "text": text,
        "source": "meta_ad" if referral else "direct",
        "source_ref": referral.get("source_id", ""),
        "message_id": message.get("id", ""),
    }


class WhatsAppClient:
    """Sends via the Cloud API when configured; logs to stdout otherwise."""

    def __init__(self, token: Optional[str] = None, phone_number_id: Optional[str] = None):
        self.token = token or os.environ.get("WHATSAPP_TOKEN", "")
        self.phone_number_id = phone_number_id or os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
        self.sent: list[dict[str, Any]] = []

    @property
    def live(self) -> bool:
        return bool(self.token and self.phone_number_id)

    def send(self, message: Message, to: str) -> list[dict[str, Any]]:
        payloads = to_payloads(message, to)
        self.sent.extend(payloads)
        if not self.live:
            for payload in payloads:
                print("[whatsapp:dry-run] " + json.dumps(payload, ensure_ascii=False))
            return payloads

        import requests  # imported lazily so the offline path needs no dependency

        url = f"https://graph.facebook.com/{GRAPH_VERSION}/{self.phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        for payload in payloads:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            response.raise_for_status()
        return payloads
