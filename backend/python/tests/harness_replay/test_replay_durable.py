"""The replay lane, legacy against durable: every committed legacy recording
the durable P1a path can run replays through it strictly (the recording's
model responses, frozen tool outcomes) with the same behaviour and the same
model requests (no context-digest note: system prompt, messages and tools
are what legacy sent).
"""

from __future__ import annotations

import pytest

from harness_core.testing.replay import Recording, diff_recordings
from tests.harness_replay import durable_driver
from tests.harness_replay.record import CORPUS_DIR, record_scrubbed
from tests.harness_replay.scenario import load_scenarios

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

CORPUS = [
    p
    for p in sorted(CORPUS_DIR.glob("*.json"))
    if durable_driver.supports(
        Recording.load(p).inputs.route, Recording.load(p).inputs.chat_mode
    )
]


def test_the_corpus_has_durable_runs_to_replay() -> None:
    assert {p.stem for p in CORPUS} >= {
        "quick-kb-lookup",
        "quick-ask-user-question",
        "react-ticket-error-then-success",
    }


@pytest.mark.parametrize("path", CORPUS, ids=[p.stem for p in CORPUS])
async def test_a_legacy_recording_replays_on_the_durable_path(path) -> None:
    recording = Recording.load(path)
    replayed = await durable_driver.replay(recording)
    assert replayed.frozen is not None and replayed.frozen.divergences == []
    report = diff_recordings(recording, replayed.recording, compare_effects=False)
    assert report.identical, report.render()
    assert report.context_notes == [], "the durable requests are the legacy requests"
    assert replayed.recording.final == recording.final


@pytest.mark.parametrize("scenario_id", ["quick-kb-lookup", "react-ticket-error-then-success"])
async def test_a_lazy_grant_replays_on_the_durable_path(scenario_id, monkeypatch) -> None:
    """The committed scenarios stay under the lazy threshold, so the corpus replays eager disclosure only; with the
    threshold lowered the same scenarios go lazy, and the durable requests still equal legacy's."""
    monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "5")
    scenario = next(s for s in load_scenarios() if s.id == scenario_id)
    recording = await record_scrubbed(scenario)
    assert any("list_toolsets" in step.request.tools for step in recording.model_steps())
    replayed = await durable_driver.replay(recording)
    report = diff_recordings(recording, replayed.recording, compare_effects=False)
    assert report.identical, report.render()
    assert report.context_notes == [], "the durable requests are the legacy requests"


def test_the_corpus_is_eager() -> None:
    """D-DISC-1: the harness's own tools (the question cards among them) do not count toward the threshold, so these
    scenarios, 15 of the person's tools each, list every schema."""
    for path in CORPUS:
        offered = [step.request.tools for step in Recording.load(path).model_steps()]
        assert not any("list_toolsets" in tools for tools in offered), path.stem
        assert any("internaltools__ask_user_question" in tools for tools in offered), path.stem
