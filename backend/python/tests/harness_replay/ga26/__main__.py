"""Record the GA-26 cassettes from their scripted models, or check that the committed ones are current."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

TEST_SECRET_KEY = "78aa78e734a5383b62cd145ce0e7037fa5496d3b899200385dd1cc44b2f70421"


def ensure_test_secret_key(env: dict[str, str] | os._Environ[str]) -> None:
    """The model gateway keys the prompt cache with a key derived from SECRET_KEY; outside pytest no conftest sets it, so
    the recorder sets the test value tests/conftest.py does when no secret is given: SECRET_KEY unset or empty, and no
    SECRET_KEY_FILE (a file names the secret, as the key ring reads it)."""
    if not env.get("SECRET_KEY") and not (env.get("SECRET_KEY_FILE") or "").strip():
        env["SECRET_KEY"] = TEST_SECRET_KEY


ensure_test_secret_key(os.environ)
_BACKEND_PYTHON = Path(__file__).resolve().parents[3]
if str(_BACKEND_PYTHON) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PYTHON))

from harness_core.testing.cassette import Cassette  # noqa: E402
from tests.harness_replay.ga26.journeys import (  # noqa: E402
    JOURNEYS,
    cassette_path,
    record,
)


async def _main(args: argparse.Namespace) -> int:
    chosen = args.journey or sorted(JOURNEYS)
    drift: list[str] = []
    for journey_id in chosen:
        cassette = await record(JOURNEYS[journey_id])
        path = cassette_path(journey_id)
        if args.check:
            if not path.exists() or Cassette.load(path) != cassette:
                drift.append(journey_id)
            continue
        cassette.save(path)
        print(f"recorded {journey_id}: {len(cassette.interactions)} model calls -> {path}")
    if drift:
        print(f"cassettes out of date: {', '.join(drift)}; re-record with --record", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--journey", action="append", choices=sorted(JOURNEYS))
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
