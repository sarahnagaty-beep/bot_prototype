"""
CRM / profile store - the integration touchpoints table (Part A, §5).

A JSON-file store stands in for the brokerage's real CRM. Everything the
adapter layer would do lives behind this interface: recognise a returning
number, retrieve the saved profile, write the lead with score and source,
assign a consultant by rotation, and emit the funnel events the dashboard
reads. Swapping in a real CRM means reimplementing this class, not the flow.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import BuyerProfile, TranscriptEntry

DEFAULT_STORE = Path(__file__).resolve().parent.parent / "data" / "store.json"

CONSULTANTS = [
    {"name": "Mariam Fahmy", "team": "New Cairo pod A"},
    {"name": "Youssef Adel", "team": "New Cairo pod B"},
    {"name": "Nour Ibrahim", "team": "West Cairo pod"},
    {"name": "Karim Saleh", "team": "Coast pod"},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CRM:
    """Profile store + lead engine + event log."""

    def __init__(self, path: Path | str = DEFAULT_STORE):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "profiles": {},
            "leads": [],
            "transcripts": {},
            "events": [],
            "outcomes": [],
            "rotation_index": 0,
        }
        self.load()

    # -- persistence --------------------------------------------------------

    def load(self) -> None:
        if self.path.exists():
            try:
                self._data.update(json.loads(self.path.read_text()))
            except json.JSONDecodeError:
                pass

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))

    def reset(self) -> None:
        self._data = {
            "profiles": {},
            "leads": [],
            "transcripts": {},
            "events": [],
            "outcomes": [],
            "rotation_index": 0,
        }
        self.save()

    # -- profiles (context retrieval, Node 0.5) -----------------------------

    def get_profile(self, wa_id: str) -> Optional[BuyerProfile]:
        raw = self._data["profiles"].get(wa_id)
        return BuyerProfile.from_dict(raw) if raw else None

    def upsert_profile(self, profile: BuyerProfile) -> BuyerProfile:
        profile.touch()
        self._data["profiles"][profile.wa_id] = profile.to_dict()
        self.save()
        return profile

    def all_profiles(self) -> list[BuyerProfile]:
        return [BuyerProfile.from_dict(p) for p in self._data["profiles"].values()]

    # -- transcripts --------------------------------------------------------

    def append_transcript(self, conversation_id: str, entry: TranscriptEntry) -> None:
        self._data["transcripts"].setdefault(conversation_id, []).append(
            {"role": entry.role, "text": entry.text, "node": entry.node, "at": entry.at}
        )

    def transcript(self, conversation_id: str) -> list[dict[str, Any]]:
        return self._data["transcripts"].get(conversation_id, [])

    # -- leads --------------------------------------------------------------

    def write_lead(self, profile: BuyerProfile, conversation_id: str) -> dict[str, Any]:
        """Create or update the CRM lead with every field + score + source."""
        lead = {
            "lead_id": f"L-{profile.wa_id[-6:]}",
            "conversation_id": conversation_id,
            "profile": profile.to_dict(),
            "created_at": _now(),
        }
        for i, existing in enumerate(self._data["leads"]):
            if existing["lead_id"] == lead["lead_id"]:
                lead["created_at"] = existing["created_at"]
                lead["updated_at"] = _now()
                self._data["leads"][i] = lead
                self.save()
                return lead
        self._data["leads"].append(lead)
        self.save()
        return lead

    def leads(self) -> list[dict[str, Any]]:
        return list(self._data["leads"])

    # -- lead engine --------------------------------------------------------

    def assign_consultant(self, profile: BuyerProfile) -> dict[str, Any]:
        """Round-robin rotation; a real deployment would weight by area and load."""
        idx = self._data.get("rotation_index", 0) % len(CONSULTANTS)
        self._data["rotation_index"] = idx + 1
        consultant = CONSULTANTS[idx]
        self.save()
        return consultant

    # -- events (funnel telemetry for the dashboard) ------------------------

    def log_event(
        self,
        name: str,
        conversation_id: str,
        wa_id: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> None:
        self._data["events"].append(
            {
                "event": name,
                "conversation_id": conversation_id,
                "wa_id": wa_id,
                "at": _now(),
                "payload": payload or {},
            }
        )
        self.save()

    def events(self) -> list[dict[str, Any]]:
        return list(self._data["events"])

    # -- pilot outcomes (A/B measurement, strategy §6.3.3) ------------------

    def record_outcome(
        self, lead_id: str, cohort: str, stage: str, extra: Optional[dict[str, Any]] = None
    ) -> None:
        """Downstream result of a lead: contacted / viewing / reserved / closed.

        In a live pilot these arrive from the brokerage CRM; the profiled and
        control cohorts are compared on the same stages.
        """
        self._data.setdefault("outcomes", [])
        for row in self._data["outcomes"]:
            if row["lead_id"] == lead_id:
                row.update({"stage": stage, "cohort": cohort, "at": _now(), **(extra or {})})
                self.save()
                return
        self._data["outcomes"].append(
            {"lead_id": lead_id, "cohort": cohort, "stage": stage, "at": _now(), **(extra or {})}
        )
        self.save()

    def outcomes(self) -> list[dict[str, Any]]:
        return list(self._data.get("outcomes", []))
