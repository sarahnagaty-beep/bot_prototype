"""
Buyer Bot prototype - command line entry point.

    python main.py demo          run every scripted scenario, refresh samples + dashboard
    python main.py chat          talk to the bot in the terminal
    python main.py serve         run the webhook + dashboard (http://localhost:8000)
    python main.py brief L-...   print the brief a consultant receives
    python main.py dashboard     re-embed the current data into the dashboard
"""

from __future__ import annotations

import argparse
import sys

from src.brief import build_brief, render_markdown
from src.crm import CRM
from src.dashboard_build import DASHBOARD, embed
from src.demo import build_demo, seed_pilot_outcomes
from src.inventory import load_units
from src.models import BuyerProfile
from src.simulator import chat


def cmd_demo(args: argparse.Namespace) -> int:
    crm = CRM()
    crm.reset()
    engines = build_demo(crm, echo=args.verbose)
    seed_pilot_outcomes(crm)
    embed(crm)
    print(f"Ran {len(engines)} conversations.")
    for engine in engines:
        print(
            f"  {engine.profile.name:<16} {engine.profile.band:<5} "
            f"score {engine.profile.score:<2} -> {engine.profile.intent_action or 'no action'}"
        )
    print("\nSamples written to samples/ · dashboard data refreshed.")
    print("Next: python main.py serve   (dashboard at http://localhost:8000)")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    chat(CRM(), wa_id=args.number, name=args.name)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("src.app:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    crm = CRM()
    for lead in crm.leads():
        if lead["lead_id"] == args.lead_id:
            profile = BuyerProfile.from_dict(lead["profile"])
            brief = build_brief(profile, load_units(), crm.transcript(lead["conversation_id"]))
            print(render_markdown(brief))
            return 0
    print(f"No lead {args.lead_id}. Known: {', '.join(l['lead_id'] for l in crm.leads())}")
    return 1


def cmd_dashboard(args: argparse.Namespace) -> int:
    embed(CRM())
    print(f"Embedded current data into {DASHBOARD}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Buyer Bot prototype")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="run scripted scenarios and refresh samples")
    demo.add_argument("-v", "--verbose", action="store_true", help="print each conversation")
    demo.set_defaults(func=cmd_demo)

    talk = sub.add_parser("chat", help="interactive conversation in the terminal")
    talk.add_argument("--number", default="+201000000001")
    talk.add_argument("--name", default="")
    talk.set_defaults(func=cmd_chat)

    serve = sub.add_parser("serve", help="run the webhook + dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    brief = sub.add_parser("brief", help="print a buyer brief")
    brief.add_argument("lead_id")
    brief.set_defaults(func=cmd_brief)

    dash = sub.add_parser("dashboard", help="re-embed current data into the dashboard")
    dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
