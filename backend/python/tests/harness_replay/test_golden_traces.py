"""The golden-trace corpus (EV-OPS-01, C17.07): every incident's captured journal replays as it did, on every build.

GTR-7 every committed case under ``tests/fixtures/journals/golden/`` is proven (clean on re-scan, idempotent) and its
      journal replays to the fold, cursor and provenance facts captured from the original run: a harness change that
      folds an incident's journal differently fails here first.
GTR-8 a real durable run whose tool results carry a bot token, a password in a URL and an address (the
      ``quick-slack-post-with-secrets`` scenario, unscrubbed) captures into a clean case that replays identically.

Add a case: export the run's journal (the run's events route, ``{"events": [...]}``) and
``python -m harness_core.testing.replay capture export.json tests/fixtures/journals/golden/<case>.json --case <case>
--incident <id>``; the capture refuses to write anything it cannot prove.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_core.testing.replay.trace_redactor import (
    GoldenCase,
    capture,
    replay_signature,
    verify,
)
from tests.harness_replay import durable_driver
from tests.harness_replay.scenario import load_scenarios

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "journals" / "golden"
CASES = sorted(GOLDEN_DIR.glob("*.json"))
SECRETS_SCENARIO = "quick-slack-post-with-secrets"
PLANTED = ("xoxb-2231", "hunter22", "dana.lee@corp.example", "Post a summary", "Handover")


def test_the_golden_corpus_has_cases() -> None:
    assert CASES, "EV-OPS-01: the corpus only grows"


@pytest.mark.parametrize("path", CASES, ids=[p.stem for p in CASES])
def test_gtr7_a_committed_case_is_proven_and_replays_as_captured(path: Path) -> None:
    case = GoldenCase.load(path)
    assert case.manifest.case == path.stem

    assert verify(case) == []
    assert replay_signature(case.journal(), case.manifest.run_id) == case.expected, (
        f"{path.name}: the harness folds this incident's journal differently from the run it was captured from"
    )


@pytest.mark.usefixtures("flow_guard_off")
async def test_gtr8_a_real_durable_run_with_secrets_captures_clean_and_replays() -> None:
    scenario = next(s for s in load_scenarios() if s.id == SECRETS_SCENARIO)
    result = await durable_driver.drive(scenario.drive_spec())
    assert result.events
    raw = [e.envelope() for e in result.events]

    case = capture(raw, case="slack-post-with-secrets", canaries=PLANTED)

    dumped = case.to_json()
    for planted in PLANTED:
        assert planted not in dumped
    assert case.expected["status"] == "completed"
    assert [i["toolId"] for i in case.expected["invocations"]] == [  # type: ignore[index, union-attr]
        "pipeshub@slack/read_channel",
        "pipeshub@slack/send_message",
    ]
    assert replay_signature(case.journal(), case.manifest.run_id) == case.expected
