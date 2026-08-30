"""
Post-deploy smoke test: drive a full conversation through a running instance.

    python scripts/smoke.py http://localhost:8000

Checks the service is healthy, walks a buyer from the ad click to the handoff
through the real webhook, and confirms the lead and brief come back out. Run it
against the UAT host before handing the number to testers.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

REPLIES = [
    "hi", "yes", "buy to live in", "New Cairo", "apartment", "3", "primary",
    "off-plan", "7-12M", "installments", "15%", "quarterly", "backloaded",
    "asap", "no thanks", "book a viewing",
]
WA_ID = "201000000777"


def call(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def inbound(text: str, index: int) -> dict:
    return {"entry": [{"changes": [{"value": {
        "contacts": [{"profile": {"name": "Smoke Test"}, "wa_id": WA_ID}],
        "messages": [{
            "from": WA_ID, "id": f"wamid.SMOKE{index}", "type": "text",
            "text": {"body": text}, "referral": {"source_id": "SMOKE_TEST"},
        }],
    }}]}]}


def main(base: str) -> int:
    print(f"→ {base}")
    health = call(f"{base}/health")
    print(f"  health: {health['status']} · whatsapp {health['whatsapp']} · "
          f"signature check {'on' if health['signature_check'] else 'OFF'}")
    if health["status"] != "ok":
        return 1

    for index, reply in enumerate(REPLIES):
        result = call(f"{base}/webhook", inbound(reply, index))["handled"][0]
        if result.get("skipped"):
            print(f"  ! turn {index} skipped as duplicate")
            continue
        print(f"  {reply:<16} -> {result['messages_sent']} msg · "
              f"band {result['band']} · next {result.get('awaiting') or 'done'}")

    snapshot = call(f"{base}/api/snapshot")
    lead = next(
        (row for row in snapshot["leads"] if row["wa_id"].endswith("000777")), None
    )
    if not lead:
        print("  FAIL: no lead written for the smoke buyer")
        return 1

    brief = call(f"{base}/api/leads/{lead['lead_id']}")
    print(f"\n  lead {lead['lead_id']}: {lead['band']} score {lead['score']} "
          f"-> {lead['consultant'] or 'unassigned'}")
    print(f"  brief: {len(brief['talking_points'])} talking points, "
          f"{len(brief['shortlist'])} units, {len(brief['transcript'])} turns")

    ok = lead["band"] == "hot" and brief["shortlist"] and lead["consultant"]
    print("\nSMOKE TEST PASSED" if ok else "\nSMOKE TEST FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    base_url = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
    try:
        sys.exit(main(base_url))
    except urllib.error.URLError as error:
        print(f"could not reach {base_url}: {error}")
        sys.exit(1)
