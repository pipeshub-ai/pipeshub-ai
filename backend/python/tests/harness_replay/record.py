"""Record legacy runs into the replay corpus.

    python -m tests.harness_replay.record --all
    python -m tests.harness_replay.record --scenario quick-kb-lookup
    python -m tests.harness_replay.record --all --check   # exit 1 on behaviour drift

Each scenario is driven through the legacy route, scrubbed (the canary scan
refuses to write a recording that still holds a secret) and written to
``tests/fixtures/journals/legacy/<id>.json``. The durable-journal export
(``--run <runId>``) arrives with the P1 journal; legacy runs are recorded from
their scenario, which is what TEST-STRATEGY §4.6 calls the legacy capture.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_BACKEND_PYTHON = Path(__file__).resolve().parents[2]
if str(_BACKEND_PYTHON) not in sys.path:
    sys.path.insert(0, str(_BACKEND_PYTHON))

from harness_core.testing.replay import (  # noqa: E402
    DiffReport,
    Recording,
    Scrubber,
    diff_recordings,
    scrub_recording,
)
from tests.harness_replay.legacy_driver import drive  # noqa: E402
from tests.harness_replay.scenario import Scenario, load_scenarios  # noqa: E402

CORPUS_DIR = _BACKEND_PYTHON / "tests" / "fixtures" / "journals" / "legacy"

# A fixed pseudonym key keeps re-recording byte-stable. Acceptable only because
# these scenarios are synthetic; a production recording must use Scrubber()'s
# random key, or its pseudonyms can be reversed by hashing likely addresses.
SYNTHETIC_CORPUS_KEY = b"pipeshub-synthetic-scenarios"


def corpus_path(scenario_id: str) -> Path:
    return CORPUS_DIR / f"{scenario_id}.json"


async def record_scrubbed(scenario: Scenario) -> Recording:
    scrubber = Scrubber(key=SYNTHETIC_CORPUS_KEY)
    spec = scenario.drive_spec()
    spec.scrub = scrubber.text
    result = await drive(spec)
    return scrub_recording(result.recording, scrubber=scrubber)


def corpus_drift(committed: Recording, fresh: Recording) -> DiffReport | None:
    """How a fresh recording differs from the committed one, or ``None`` when it is current. A changed model request
    (system prompt, messages or offered tools) is drift too: the durable lane holds its requests to these, so a
    grant that changed here must be re-recorded with the change, not found later as a durable divergence."""
    report = diff_recordings(committed, fresh)
    return None if report.identical and not report.context_notes else report


async def _main(args: argparse.Namespace) -> int:
    scenarios = load_scenarios()
    if args.scenario:
        scenarios = [s for s in scenarios if s.id in args.scenario]
        missing = set(args.scenario) - {s.id for s in scenarios}
        if missing:
            print(f"unknown scenario(s): {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    drifted = 0
    for scenario in scenarios:
        recording = await record_scrubbed(scenario)
        path = corpus_path(scenario.id)
        if args.check:
            report = corpus_drift(Recording.load(path), recording)
            if report is not None:
                drifted += 1
                print(f"{scenario.id}:\n{report.render()}\n")
            continue
        recording.save(path)
        print(f"wrote {path.relative_to(_BACKEND_PYTHON)}")
    return 1 if drifted else 0


def main(argv: list[str] | None = None) -> int:
    # The corpus is recorded without the flow guard, as its tests replay it (the `flow_guard_off` fixture); with
    # the guard on, a write after a read is held and the recording no longer matches the tests' world.
    os.environ["HARNESS_FLOW_GUARD"] = "false"
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--scenario", action="append")
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare with the corpus (behaviour and model requests) instead of writing",
    )
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
