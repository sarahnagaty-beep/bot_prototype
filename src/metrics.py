"""
Funnel and pilot metrics for the dashboard.

Everything here is derived from the event log and the outcome table, so the
numbers on the dashboard trace back to something the bot actually did.

The A/B section is the strategy's core claim (§6.3.3): profiled leads against a
control cohort of unprofiled leads, on the metric the brokerage already tracks.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

from .crm import CRM
from .models import AREA_LABELS, REGIONS, region_label

STAGES = ["lead", "contacted", "viewing", "reserved", "closed"]
STAGE_LABELS = {
    "lead": "Leads",
    "contacted": "Contacted",
    "viewing": "Viewing booked",
    "reserved": "Reserved",
    "closed": "Closed",
}


def _pct(part: float, whole: float) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def funnel(crm: CRM) -> list[dict[str, Any]]:
    """Conversation funnel: what happens between the ad click and the handoff."""
    events = crm.events()
    by_name: dict[str, set[str]] = defaultdict(set)
    for event in events:
        by_name[event["event"]].add(event["conversation_id"])

    started = len(by_name["conversation_started"])
    steps = [
        ("Conversations started", started),
        ("Opted in", len(by_name["opt_in"])),
        ("Qualified (flow completed)", len(by_name["qualified"])),
        ("Recommendations shown", len(by_name["recommended"] | by_name["recommended_widened"])),
        ("Action taken", len(by_name["lead_written"])),
        ("Handed to consultant", len(by_name["handoff"])),
    ]
    return [
        {"step": name, "count": count, "pct_of_started": _pct(count, started)}
        for name, count in steps
    ]


def band_split(crm: CRM) -> dict[str, int]:
    counts = Counter()
    for lead in crm.leads():
        counts[lead["profile"].get("band", "cold")] += 1
    return {band: counts.get(band, 0) for band in ("hot", "warm", "cold")}


def source_breakdown(crm: CRM) -> list[dict[str, Any]]:
    started: Counter = Counter()
    qualified: Counter = Counter()
    by_conversation: dict[str, str] = {}
    for event in crm.events():
        if event["event"] == "conversation_started":
            ref = event["payload"].get("source_ref") or event["payload"].get("source") or "direct"
            by_conversation[event["conversation_id"]] = ref
            started[ref] += 1
        elif event["event"] == "qualified":
            ref = by_conversation.get(event["conversation_id"], "direct")
            qualified[ref] += 1
    return sorted(
        (
            {
                "source": ref,
                "started": count,
                "qualified": qualified.get(ref, 0),
                "qualified_rate": _pct(qualified.get(ref, 0), count),
            }
            for ref, count in started.items()
        ),
        key=lambda row: -row["started"],
    )


def demand(crm: CRM) -> dict[str, list[dict[str, Any]]]:
    """What buyers are actually asking for - the data layer the brokerage lacked."""
    budgets: Counter = Counter()
    types: Counter = Counter()
    timelines: Counter = Counter()
    for lead in crm.leads():
        profile = lead["profile"]
        if profile.get("budget_band"):
            budgets[profile["budget_band"]] += 1
        if profile.get("unit_type"):
            types[profile["unit_type"]] += 1
        if profile.get("timeline"):
            timelines[profile["timeline"].replace("_", " ")] += 1

    def rows(counter: Counter) -> list[dict[str, Any]]:
        return [{"label": k, "count": v} for k, v in counter.most_common()]

    return {
        "regions": regions(crm),
        "budgets": rows(budgets),
        "unit_types": rows(types),
        "timelines": rows(timelines),
    }


def regions(crm: CRM) -> list[dict[str, Any]]:
    """Demand by region - Cairo East, Cairo West, North Coast - with the
    compounds inside each, so a director can see both levels at once."""
    totals: Counter = Counter()
    hot: Counter = Counter()
    areas: dict[str, Counter] = defaultdict(Counter)

    for lead in crm.leads():
        profile = lead["profile"]
        region = profile.get("region")
        if not region:
            continue
        totals[region] += 1
        if profile.get("band") == "hot":
            hot[region] += 1
        named = profile.get("preferred_areas") or []
        if named:
            for area in named:
                areas[region][AREA_LABELS.get(area, area)] += 1
        else:
            areas[region]["No compound named"] += 1

    rows = []
    open_briefs = sum(
        1 for lead in crm.leads() if not lead["profile"].get("region")
    )
    for region in REGIONS:
        count = totals.get(region, 0)
        rows.append({
            "region": region,
            "label": region_label(region),
            "count": count,
            "hot": hot.get(region, 0),
            "hot_rate": _pct(hot.get(region, 0), count),
            "share": _pct(count, sum(totals.values())),
            "areas": [
                {"label": label, "count": n} for label, n in areas[region].most_common()
            ],
        })
    rows.sort(key=lambda row: -row["count"])
    if open_briefs:
        # Investors are asked for returns, not a region (Node 2i), so their
        # leads belong in the breakdown as their own row rather than nowhere.
        rows.append({
            "region": "", "label": "No region stated (investors)",
            "count": open_briefs, "hot": 0, "hot_rate": 0.0,
            "share": _pct(open_briefs, sum(totals.values()) + open_briefs),
            "areas": [],
        })
    return rows


def weekly(crm: CRM, weeks: int = 12) -> list[dict[str, Any]]:
    """Lead volume per week, split hot vs the rest - the shape leadership reads
    first at floor scale."""
    buckets: dict[date, Counter] = defaultdict(Counter)
    for lead in crm.leads():
        stamp = lead.get("created_at") or ""
        try:
            when = datetime.fromisoformat(stamp).date()
        except ValueError:
            continue
        monday = when - timedelta(days=when.weekday())
        buckets[monday]["total"] += 1
        if lead["profile"].get("band") == "hot":
            buckets[monday]["hot"] += 1

    this_week = date.today() - timedelta(days=date.today().weekday())
    ordered = [week for week in sorted(buckets) if week < this_week][-weeks:]
    return [
        {
            "week": monday.isoformat(),
            "label": monday.strftime("%d %b"),
            "total": buckets[monday]["total"],
            "hot": buckets[monday]["hot"],
            "hot_rate": _pct(buckets[monday]["hot"], buckets[monday]["total"]),
        }
        for monday in ordered
    ]


def floor(crm: CRM, top: int = 8) -> dict[str, Any]:
    """The sales floor at scale: team rollups, plus the busiest agents.

    A thousand-agent list is not a view. Teams are the unit a director works
    with; individual agents are reachable through search on the lead queue.
    """
    teams: dict[str, dict[str, Any]] = {}
    for team in crm.teams:
        teams[team["name"]] = {
            "team": team["name"],
            "region": team["region"],
            "region_label": region_label(team["region"]),
            "leader": team.get("leader", ""),
            "headcount": team.get("headcount", 0),
            "leads": 0, "hot": 0, "viewings": 0, "score_total": 0,
        }

    agents: dict[str, dict[str, Any]] = {}
    for lead in crm.leads():
        profile = lead["profile"]
        name = profile.get("consultant")
        if not name:
            continue
        team_name = profile.get("consultant_team", "")
        row = teams.setdefault(team_name, {
            "team": team_name or "Unassigned", "region": "", "region_label": "",
            "leader": "", "headcount": 0, "leads": 0, "hot": 0, "viewings": 0,
            "score_total": 0,
        })
        agent = agents.setdefault(profile.get("consultant_id") or name, {
            "agent_id": profile.get("consultant_id", ""), "name": name,
            "team": team_name, "leads": 0, "hot": 0, "viewings": 0, "score_total": 0,
        })
        for bucket in (row, agent):
            bucket["leads"] += 1
            bucket["score_total"] += profile.get("score", 0)
            if profile.get("band") == "hot":
                bucket["hot"] += 1
            if profile.get("intent_action") == "viewing":
                bucket["viewings"] += 1

    def finish(row: dict[str, Any]) -> dict[str, Any]:
        leads = row.pop("score_total"), row["leads"]
        row["avg_score"] = round(leads[0] / leads[1], 1) if leads[1] else 0.0
        row["hot_rate"] = _pct(row["hot"], row["leads"])
        return row

    team_rows = sorted((finish(r) for r in teams.values()), key=lambda r: -r["leads"])
    agent_rows = sorted((finish(a) for a in agents.values()), key=lambda a: -a["leads"])

    working = [row for row in team_rows if row["leads"]]
    return {
        "headcount": len(crm.agents),
        "teams_total": len(crm.teams),
        "teams": team_rows,
        "agents_with_leads": len(agent_rows),
        "top_agents": agent_rows[:top],
        "busiest_team": working[0]["team"] if working else "",
        "leads_per_agent": round(
            sum(a["leads"] for a in agent_rows) / len(agent_rows), 1
        ) if agent_rows else 0.0,
    }


def ab_test(crm: CRM) -> dict[str, Any]:
    """Profiled vs control cohort on the brokerage's own conversion metric."""
    reached: dict[str, Counter] = {"profiled": Counter(), "control": Counter()}
    for outcome in crm.outcomes():
        cohort = outcome.get("cohort", "profiled")
        if cohort not in reached:
            continue
        stage = outcome.get("stage", "lead")
        if stage not in STAGES:
            continue
        # A lead that reached a stage also reached every stage before it.
        for earlier in STAGES[: STAGES.index(stage) + 1]:
            reached[cohort][earlier] += 1

    def cohort_rows(cohort: str) -> list[dict[str, Any]]:
        total = reached[cohort]["lead"]
        return [
            {
                "stage": stage,
                "label": STAGE_LABELS[stage],
                "count": reached[cohort][stage],
                "rate": _pct(reached[cohort][stage], total),
            }
            for stage in STAGES
        ]

    profiled_rate = _pct(reached["profiled"]["closed"], reached["profiled"]["lead"])
    control_rate = _pct(reached["control"]["closed"], reached["control"]["lead"])
    uplift_pp = round(profiled_rate - control_rate, 1)
    uplift_rel = round((profiled_rate / control_rate - 1) * 100, 1) if control_rate else 0.0

    return {
        "profiled": cohort_rows("profiled"),
        "control": cohort_rows("control"),
        "profiled_close_rate": profiled_rate,
        "control_close_rate": control_rate,
        "uplift_pp": uplift_pp,
        "uplift_relative": uplift_rel,
        "sample": {
            "profiled": reached["profiled"]["lead"],
            "control": reached["control"]["lead"],
        },
    }


def headline(crm: CRM) -> dict[str, Any]:
    steps = {row["step"]: row for row in funnel(crm)}
    started = steps["Conversations started"]["count"]
    qualified = steps["Qualified (flow completed)"]["count"]
    actions = steps["Action taken"]["count"]
    handoffs = steps["Handed to consultant"]["count"]
    bands = band_split(crm)
    ab = ab_test(crm)
    floor_stats = floor(crm)
    return {
        "agents": floor_stats["headcount"],
        "teams": floor_stats["teams_total"],
        "leads_per_agent": floor_stats["leads_per_agent"],
        "conversations": started,
        "qualified": qualified,
        "qualified_rate": _pct(qualified, started),
        "actions": actions,
        "action_rate": _pct(actions, started),
        "handoffs": handoffs,
        "hot_leads": bands["hot"],
        "bands": bands,
        "uplift_pp": ab["uplift_pp"],
        "uplift_relative": ab["uplift_relative"],
        "profiled_close_rate": ab["profiled_close_rate"],
        "control_close_rate": ab["control_close_rate"],
    }


def lead_rows(crm: CRM) -> list[dict[str, Any]]:
    """Flat rows for the dashboard lead table."""
    rows = []
    for lead in crm.leads():
        profile = lead["profile"]
        rows.append(
            {
                "lead_id": lead["lead_id"],
                "conversation_id": lead["conversation_id"],
                "name": profile.get("name") or "Unnamed buyer",
                "wa_id": profile.get("wa_id", ""),
                "buyer_type": profile.get("buyer_type", ""),
                "region": profile.get("region", ""),
                "region_label": region_label(profile.get("region", "")),
                "team": profile.get("consultant_team", ""),
                "areas": [AREA_LABELS.get(a, a) for a in profile.get("preferred_areas") or []],
                "unit_type": profile.get("unit_type", ""),
                "bedrooms": profile.get("bedrooms", ""),
                "budget": profile.get("budget_band", ""),
                "payment": profile.get("payment_type", ""),
                "timeline": profile.get("timeline", ""),
                "intent": profile.get("intent_action", ""),
                "score": profile.get("score", 0),
                "band": profile.get("band", "cold"),
                "consultant": profile.get("consultant", ""),
                "source": profile.get("source_ref") or profile.get("source", ""),
                "sessions": profile.get("sessions", 1),
                "created_at": lead.get("created_at", ""),
                "updated_at": lead.get("updated_at", lead.get("created_at", "")),
            }
        )
    rows.sort(key=lambda r: (-r["score"], r.get("created_at", ""), r["name"]))
    return rows


def lead_page(
    crm: CRM,
    query: str = "",
    band: str = "",
    region: str = "",
    team: str = "",
    buyer_type: str = "",
    sort: str = "score",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """One screen of the lead queue. At thousands of leads the table is a
    window onto the data, not the data."""
    rows = lead_rows(crm)
    needle = query.strip().lower()

    def keep(row: dict[str, Any]) -> bool:
        if band and row["band"] != band:
            return False
        if region and row["region"] != region:
            return False
        if team and row["team"] != team:
            return False
        if buyer_type and row["buyer_type"] != buyer_type:
            return False
        if needle:
            haystack = " ".join(
                [str(row.get(field, "")) for field in
                 ("name", "wa_id", "source", "consultant", "team", "lead_id", "region_label")]
                + list(row.get("areas") or [])
            )
            if needle not in haystack.lower():
                return False
        return True

    filtered = [row for row in rows if keep(row)]
    if sort == "recent":
        filtered.sort(key=lambda r: r.get("created_at", ""), reverse=True)

    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    start = (page - 1) * page_size
    window = filtered[start:start + page_size]

    return {
        "rows": window,
        "page": page,
        "page_size": page_size,
        "total": len(filtered),
        "total_unfiltered": len(rows),
        "pages": max(1, -(-len(filtered) // page_size)),
        "bands": {
            band_name: sum(1 for row in filtered if row["band"] == band_name)
            for band_name in ("hot", "warm", "cold")
        },
    }


def snapshot(crm: CRM, lead_limit: Optional[int] = None) -> dict[str, Any]:
    """Everything the dashboard needs, in one payload.

    `lead_limit` caps the embedded queue for the standalone/published build.
    The cap takes the most recent leads, not the highest-scoring: score order
    puts every investor first (they carry a bonus point), which would make the
    embedded window unrepresentative of the floor. Aggregates always cover
    every lead - only the table is a window.
    """
    rows = lead_rows(crm)
    if lead_limit:
        recent = sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)
        keep = {row["lead_id"] for row in recent[:lead_limit]}
        rows = [row for row in rows if row["lead_id"] in keep]
    return {
        "headline": headline(crm),
        "funnel": funnel(crm),
        "sources": source_breakdown(crm),
        "demand": demand(crm),
        "weekly": weekly(crm),
        "floor": floor(crm),
        "ab_test": ab_test(crm),
        "leads": rows,
        "leads_total": len(lead_rows(crm)),
    }
