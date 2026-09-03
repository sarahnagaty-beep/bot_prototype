"""
Embed a data snapshot into dashboard/index.html.

The dashboard prefers the live API (`/api/snapshot`) and falls back to the JSON
embedded here, so the same file works served by the app and opened as a static
page - which is also what makes it publishable as a standalone artifact.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import metrics
from .brief import build_brief
from .crm import CRM
from .inventory import load_units
from .models import BuyerProfile

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
BLOCK = re.compile(
    r'(<script id="bootstrap-data" type="application/json">)(.*?)(</script>)', re.S
)


# A published page carries its own data, so the queue it embeds is capped:
# aggregates cover every lead, the table shows the top slice.
EMBEDDED_LEADS = 150


def bootstrap_payload(crm: CRM, lead_limit: int = EMBEDDED_LEADS) -> dict:
    units = load_units()
    snapshot = metrics.snapshot(crm, lead_limit=lead_limit)
    shown = {row["lead_id"] for row in snapshot["leads"]}

    briefs = {}
    for lead in crm.leads():
        if lead["lead_id"] not in shown:
            continue
        profile = BuyerProfile.from_dict(lead["profile"])
        briefs[lead["lead_id"]] = build_brief(
            profile, units, crm.transcript(lead["conversation_id"])
        )
    return {"snapshot": snapshot, "briefs": briefs}


def embed(crm: CRM, path: Path = DASHBOARD, lead_limit: int = EMBEDDED_LEADS) -> Path:
    payload = json.dumps(bootstrap_payload(crm, lead_limit), ensure_ascii=False)
    html = path.read_text()
    if not BLOCK.search(html):
        raise RuntimeError("bootstrap-data block not found in dashboard/index.html")
    path.write_text(BLOCK.sub(lambda m: m.group(1) + payload + m.group(3), html, count=1))
    return path


def standalone_html(crm: CRM, path: Path = DASHBOARD) -> str:
    """The dashboard as a complete document (for opening from disk)."""
    embed(crm, path)
    return wrap(path.read_text())


def wrap(fragment: str) -> str:
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "</head>\n<body>\n" + fragment + "\n</body>\n</html>\n"
    )
