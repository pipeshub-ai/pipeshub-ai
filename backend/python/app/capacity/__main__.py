"""``python -m app.capacity facts [--export]`` and ``python -m app.capacity plan [--json]``."""

from __future__ import annotations

import argparse
import json
import os
import sys

from app.capacity.facts import FACTS_ENV
from app.capacity.policy import plan_capacity
from app.capacity.sources import Cgroup, resolve_facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.capacity")
    sub = parser.add_subparsers(dest="command", required=True)
    facts_cmd = sub.add_parser("facts", help="read this container's facts")
    facts_cmd.add_argument("--export", action="store_true", help="print one KEY=value line for a shell to export")
    plan_cmd = sub.add_parser("plan", help="print the plan")
    plan_cmd.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "facts":
        facts = Cgroup().read()
        if args.export:
            sys.stdout.write(f"{FACTS_ENV}={facts.to_json()}\n")
        else:
            sys.stdout.write(facts.to_json() + "\n")
        return 0

    plan = plan_capacity(resolve_facts(), os.environ)
    if args.json:
        sys.stdout.write(json.dumps(plan.as_dict(), indent=2, sort_keys=True, default=str) + "\n")
    else:
        sys.stdout.write(f"profile={plan.profile} cpus={plan.budget.cpus:g} cells={plan.cells}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
