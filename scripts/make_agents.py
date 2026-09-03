"""
Generate the sales floor roster: data/agents.json.

Tier 2 brokerages run 300-1,500 agents in pods under team leaders, split by
region. The dashboard has to stay readable at that size, so the demo data is
generated at it rather than pretending four consultants is a floor.

    python scripts/make_agents.py --agents 640
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "agents.json"

FIRST = [
    "Ahmed", "Mohamed", "Mahmoud", "Omar", "Youssef", "Karim", "Tarek", "Hassan", "Amr",
    "Khaled", "Sherif", "Mostafa", "Ali", "Hany", "Sameh", "Wael", "Ziad", "Adel",
    "Mariam", "Nour", "Salma", "Yara", "Dina", "Heba", "Rana", "Menna", "Aya", "Sara",
    "Nada", "Farida", "Hana", "Injy", "Reem", "Lamia", "Nihal", "Passant", "Shaimaa",
]
LAST = [
    "Fahmy", "Adel", "Ibrahim", "Saleh", "Zaki", "Mostafa", "Sobhy", "Hassan", "Kamal",
    "Elwy", "Fathy", "Selim", "Nabil", "Refaat", "Shawky", "Gaber", "Aziz", "Rashad",
    "Halim", "Farouk", "Sadek", "Mansour", "Tawfik", "Bahgat", "Roshdy", "Khalil",
]

# Where the floor actually sits. Cairo East carries the most stock and headcount.
REGION_SHARE = {"cairo_east": 0.42, "cairo_west": 0.36, "north_coast": 0.22}
REGION_LABELS = {"cairo_east": "Cairo East", "cairo_west": "Cairo West", "north_coast": "North Coast"}
POD_SIZE = 22
SENIORITY = [("junior", 0.46), ("mid", 0.36), ("senior", 0.18)]


def pick_seniority(rng: random.Random) -> str:
    roll = rng.random()
    running = 0.0
    for level, share in SENIORITY:
        running += share
        if roll <= running:
            return level
    return "junior"


def build(total: int, seed: int = 11) -> dict:
    rng = random.Random(seed)
    used: set[str] = set()
    agents: list[dict] = []
    teams: list[dict] = []

    for region, share in REGION_SHARE.items():
        headcount = round(total * share)
        pods = max(1, round(headcount / POD_SIZE))
        for pod in range(1, pods + 1):
            team_id = f"{region}-pod-{pod}"
            team_name = f"{REGION_LABELS[region]} · Pod {pod}"
            teams.append({"team_id": team_id, "name": team_name, "region": region})

            for _ in range(headcount // pods):
                for _attempt in range(50):
                    name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                    if name not in used:
                        break
                used.add(name)
                agents.append({
                    "agent_id": f"A-{len(agents) + 1:05d}",
                    "name": name,
                    "team_id": team_id,
                    "team": team_name,
                    "region": region,
                    "seniority": pick_seniority(rng),
                })

    for team in teams:
        members = [a for a in agents if a["team_id"] == team["team_id"]]
        if members:
            lead = max(members, key=lambda a: SENIORITY.index(
                next(s for s in SENIORITY if s[0] == a["seniority"])
            ))
            team["leader"] = lead["name"]
        team["headcount"] = len(members)

    return {
        "_note": "Generated sales floor for the prototype. Names are synthetic.",
        "teams": teams,
        "agents": agents,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agents", type=int, default=640)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    roster = build(args.agents, args.seed)
    OUT.write_text(json.dumps(roster, indent=1, ensure_ascii=False))
    print(f"{len(roster['agents'])} agents across {len(roster['teams'])} teams -> {OUT}")
