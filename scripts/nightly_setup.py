#!/usr/bin/env python3
"""Pick the setup an integration run tests on.

Every run covers both graph databases. The other parts a customer can swap
rotate across the nightly runs by weekday, so each supported choice runs at
least once a week while the default setup still runs on most nights. Pull
requests always use the default; a manual run uses what it asks for.

A part joins ROTATION once the integration stack can run it. etcd and the
OpenSearch / Redis search indexes need containers that stack does not have
yet; backend-matrix.yml tests them against real servers in the meantime.

Run: python3 scripts/nightly_setup.py --event schedule
Test: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timezone

DEFAULT: dict[str, str] = {"message_broker": "redis"}

CHOICES: dict[str, tuple[str, ...]] = {"message_broker": ("redis", "kafka")}

# ISO weekday (1 = Monday) -> the parts that differ from DEFAULT that night.
ROTATION: dict[int, dict[str, str]] = {
    3: {"message_broker": "kafka"},
    6: {"message_broker": "kafka"},
}

NAMES: dict[tuple[str, str], str] = {
    ("message_broker", "redis"): "Redis Streams message queue",
    ("message_broker", "kafka"): "Kafka message queue",
}


def setup_for(day: date) -> dict[str, str]:
    return {**DEFAULT, **ROTATION.get(day.isoweekday(), {})}


def resolve(event: str, day: date, requested: dict[str, str]) -> dict[str, str]:
    if event == "schedule":
        return setup_for(day)
    if event == "workflow_dispatch":
        chosen = {**DEFAULT, **{part: value for part, value in requested.items() if value}}
        for part, value in chosen.items():
            if value not in CHOICES[part]:
                raise ValueError(
                    f"'{value}' is not a {part.replace('_', ' ')} the integration stack can run. "
                    f"Pick one of: {', '.join(CHOICES[part])}."
                )
        return chosen
    return dict(DEFAULT)


def describe(setup: dict[str, str]) -> str:
    return ", ".join(NAMES[(part, value)] for part, value in sorted(setup.items()))


def _append(path_var: str, text: str) -> None:
    path = os.environ.get(path_var)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", required=True, help="the GitHub event that started the run")
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today in UTC, the cron's timezone")
    parser.add_argument("--message-broker", default="", help="what a manual run asked for")
    args = parser.parse_args(argv)

    day = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
    try:
        setup = resolve(args.event, day, {"message_broker": args.message_broker})
    except ValueError as exc:
        print(f"Could not choose a setup: {exc}", file=sys.stderr)
        return 1

    label = describe(setup)
    is_default = setup == DEFAULT
    outputs = "".join(f"{part}={value}\n" for part, value in setup.items())
    _append("GITHUB_OUTPUT", outputs + f"label={label}\nis_default={str(is_default).lower()}\n")
    note = (
        "the default setup."
        if is_default
        else "a rotated setup. A failure that doesn't show up on a default night "
        "most likely comes from this part; rerun the workflow by hand with the same "
        "choice to reproduce it."
    )
    _append("GITHUB_STEP_SUMMARY", f"### Setup under test\n\n{label}, on both Neo4j and ArangoDB. This is {note}\n")
    print(f"Setup: {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
