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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import BuyerProfile, TranscriptEntry

DEFAULT_STORE = Path(__file__).resolve().parent.parent / "data" / "store.json"
AGENTS_FILE = Path(__file__).resolve().parent.parent / "data" / "agents.json"

# Used only when there is no roster file - a floor of four is not a floor.
FALLBACK_AGENTS = [
    {"agent_id": "A-00001", "name": "Mariam Fahmy", "team": "Cairo East · Pod 1",
     "team_id": "cairo_east-pod-1", "region": "cairo_east", "seniority": "senior"},
    {"agent_id": "A-00002", "name": "Nour Ibrahim", "team": "Cairo West · Pod 1",
     "team_id": "cairo_west-pod-1", "region": "cairo_west", "seniority": "mid"},
    {"agent_id": "A-00003", "name": "Karim Saleh", "team": "North Coast · Pod 1",
     "team_id": "north_coast-pod-1", "region": "north_coast", "seniority": "mid"},
]


def load_roster(path: Path = AGENTS_FILE) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(agents, teams) - the sales floor the leads are assigned across."""
    if not path.exists():
        return list(FALLBACK_AGENTS), []
    raw = json.loads(path.read_text())
    return raw.get("agents", []), raw.get("teams", [])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CRM:
    """Profile store + lead engine + event log."""

    def __init__(self, path: Path | str = DEFAULT_STORE):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._defer_saves = False
        self._outcome_index: Optional[dict[str, int]] = None
        self.agents, self.teams = load_roster()
        self._agents_by_region: dict[str, list[dict[str, Any]]] = {}
        for agent in self.agents:
            self._agents_by_region.setdefault(agent["region"], []).append(agent)
        self._data: dict[str, Any] = {
            "profiles": {},
            "leads": [],
            "transcripts": {},
            "events": [],
            "outcomes": [],
            "sessions": {},
            "processed_messages": {},
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

    @contextmanager
    def bulk(self):
        """Suspend writes for the duration - one save at the end.

        Generating thousands of leads writes the whole store per event
        otherwise, which turns a seconds-long job into a minutes-long one.
        """
        self._defer_saves = True
        try:
            yield self
        finally:
            self._defer_saves = False
            self.save()

    def save(self) -> None:
        if self._defer_saves:
            return
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
            "sessions": {},
            "processed_messages": {},
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
        """Assign within the buyer's region, to whoever is carrying least.

        Round-robin across the whole floor would hand a North Coast buyer to a
        Cairo West agent, and would pile onto whoever happens to be next in a
        list of hundreds. Load is tracked per agent so the queue stays even.
        """
        pool = self._agents_by_region.get(profile.region) or self.agents
        if not pool:
            return {"name": "", "team": "", "agent_id": "", "region": ""}

        load = self._data.setdefault("assignment_load", {})
        # Least-loaded first; the rotation index breaks ties so equal-load
        # agents are still taken in turn rather than always the first one.
        offset = self._data.get("rotation_index", 0)
        ordered = sorted(
            range(len(pool)),
            key=lambda i: (load.get(pool[i]["agent_id"], 0), (i + offset) % len(pool)),
        )
        agent = pool[ordered[0]]
        load[agent["agent_id"]] = load.get(agent["agent_id"], 0) + 1
        self._data["rotation_index"] = offset + 1
        self.save()
        return agent

    def agent_load(self) -> dict[str, int]:
        return dict(self._data.get("assignment_load", {}))

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
        rows = self._data.setdefault("outcomes", [])
        # Index by lead so seeding thousands of rows stays linear.
        if self._outcome_index is None or len(self._outcome_index) != len(rows):
            self._outcome_index = {row["lead_id"]: i for i, row in enumerate(rows)}

        record = {"lead_id": lead_id, "cohort": cohort, "stage": stage,
                  "at": _now(), **(extra or {})}
        existing = self._outcome_index.get(lead_id)
        if existing is not None:
            rows[existing].update(record)
        else:
            self._outcome_index[lead_id] = len(rows)
            rows.append(record)
        self.save()

    def outcomes(self) -> list[dict[str, Any]]:
        return list(self._data.get("outcomes", []))

    # -- live conversation state -------------------------------------------

    def save_session(self, wa_id: str, state: dict[str, Any]) -> None:
        """Persist where a buyer is in the flow.

        Without this a restart (or a second web worker) drops every buyer
        mid-conversation and starts them over - the exact failure UAT testers
        would report as "the bot forgot me".
        """
        self._data.setdefault("sessions", {})[wa_id] = {**state, "updated_at": _now()}
        self.save()

    def load_session(self, wa_id: str, max_age_hours: int = 24) -> Optional[dict[str, Any]]:
        state = self._data.get("sessions", {}).get(wa_id)
        if not state:
            return None
        updated = datetime.fromisoformat(state.get("updated_at", _now()))
        age = (datetime.now(timezone.utc) - updated).total_seconds() / 3600
        if age > max_age_hours:
            # Past WhatsApp's 24-hour service window this is a new conversation.
            self.clear_session(wa_id)
            return None
        return state

    def clear_session(self, wa_id: str) -> None:
        if self._data.get("sessions", {}).pop(wa_id, None) is not None:
            self.save()

    # -- webhook de-duplication --------------------------------------------

    def seen_message(self, message_id: str, keep: int = 500) -> bool:
        """True if this Cloud API message id was already handled.

        Meta retries a webhook until it gets a 200, so without this a slow turn
        is replayed and the buyer is answered twice.
        """
        if not message_id:
            return False
        seen = self._data.setdefault("processed_messages", {})
        if message_id in seen:
            return True
        seen[message_id] = _now()
        if len(seen) > keep:
            for old in sorted(seen, key=seen.get)[: len(seen) - keep]:
                seen.pop(old, None)
        self.save()
        return False
