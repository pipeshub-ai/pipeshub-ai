"""C-9: a RunSpec v3 with ``executor.kind = loop`` runs as its v2 spec does.

F2 (ARCHITECTURE §4.8) adds ``executor`` and schema version 3. Every durable
golden scenario goes admission -> claim -> ``PipesHubRuntimeAssembler`` ->
kernel twice, pinned at v2 and at v3 on the loop: the journals agree event
for event (but for the pin itself) and project to the same transcript.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.contracts.test_durable_goldens import (
    DURABLE_SCENARIOS,
    durable_transcript,
    run_durably,
)

if TYPE_CHECKING:
    from harness_core.journal import JournalEvent
    from tests.contracts.golden_support import Scenario

_PIN = {"spec", "specDigest"}


def _shape(events: list[JournalEvent]) -> list[tuple[str, dict[str, Any]]]:
    return [(e.type, {k: v for k, v in e.data.items() if k not in _PIN}) for e in events]


def _pinned_version(events: list[JournalEvent]) -> int:
    (pinned,) = [e for e in events if e.type == "run.spec_pinned"]
    return pinned.data["spec"]["schemaVersion"]


@pytest.mark.parametrize("scenario", DURABLE_SCENARIOS, ids=lambda s: s.name)
async def test_c9_a_v3_loop_spec_runs_exactly_as_its_v2_spec(scenario: Scenario) -> None:
    v2_events, v2_content = await run_durably(scenario)
    v3_events, v3_content = await run_durably(scenario, executor={"kind": "loop"})

    assert (_pinned_version(v2_events), _pinned_version(v3_events)) == (2, 3)
    assert _shape(v3_events) == _shape(v2_events)
    assert await durable_transcript(v3_events, v3_content) == await durable_transcript(v2_events, v2_content)
