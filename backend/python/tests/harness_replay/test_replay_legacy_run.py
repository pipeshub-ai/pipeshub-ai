"""Record, scrub and replay real legacy runs (P0.4: J1-J3).

J1  record -> scrub -> replay reproduces the tool-call sequence and the answer,
    for every scenario and for every recording committed to the corpus.
J2  (end to end) a run whose tool results carry secrets is scrubbed, the
    canary scan passes, and the scrubbed recording still replays.
J3  a harness change is reported at the first step where behaviour differs.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.core.types import ToolResult
from app.agent_loop_lib.tools.executor import ToolExecutor
from harness_core.testing.replay import (
    Recording,
    ToolStep,
    diff_recordings,
    find_secrets,
)
from tests.harness_replay.legacy_driver import drive, replay
from tests.harness_replay.record import (
    CORPUS_DIR,
    corpus_drift,
    corpus_path,
    record_scrubbed,
)
from tests.harness_replay.scenario import load_scenarios

# Recorded on today's loop before UX3's flow guard; the guard is tested on its own (test_ux3_legacy_flow_guard.py).
pytestmark = pytest.mark.usefixtures("flow_guard_off")

SCENARIOS = load_scenarios()
CORPUS = sorted(CORPUS_DIR.glob("*.json"))


def _tool_sequence(recording: Recording) -> list[tuple[str, str]]:
    return [(s.name, s.status) for s in recording.tool_steps()]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
async def test_record_scrub_replay_reproduces_the_run(scenario) -> None:
    recorded = await record_scrubbed(scenario)
    assert recorded.model_steps(), "nothing was recorded"

    replayed = await replay(recorded)

    assert _tool_sequence(replayed.recording) == _tool_sequence(recorded)
    assert replayed.recording.final == recorded.final
    assert replayed.frozen is not None and replayed.frozen.divergences == []
    report = diff_recordings(recorded, replayed.recording, compare_effects=False)
    assert report.identical, report.render()
    assert report.context_notes == [], "scrubbed digests must match on replay"


def test_every_scenario_has_a_committed_recording() -> None:
    assert {p.stem for p in CORPUS} == {s.id for s in SCENARIOS}


@pytest.mark.parametrize("path", CORPUS, ids=[p.stem for p in CORPUS])
async def test_committed_recording_replays_strictly(path) -> None:
    recording = Recording.load(path)
    assert recording.manifest.scrub_version, "only scrubbed recordings may be committed"
    assert find_secrets(recording.model_dump(mode="json")) == []

    replayed = await replay(recording)

    report = diff_recordings(recording, replayed.recording, compare_effects=False)
    assert report.identical, (
        f"{path.name}: the harness no longer behaves as recorded.\n{report.render()}\n"
        "If the change is intended, re-record with `python -m tests.harness_replay.record --all`."
    )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
async def test_committed_recording_is_current(scenario) -> None:
    fresh = await record_scrubbed(scenario)
    report = corpus_drift(Recording.load(corpus_path(scenario.id)), fresh)
    assert report is None, (
        f"{report.render()}\nRe-record with `python -m tests.harness_replay.record --all` if the change is intended."
    )


async def test_secrets_in_tool_results_are_scrubbed_and_the_run_still_replays() -> None:
    scenario = next(s for s in SCENARIOS if s.id == "quick-slack-post-with-secrets")
    raw = (await drive(scenario.drive_spec())).recording
    assert find_secrets(raw.model_dump(mode="json")), (
        "fixture should carry secrets to scrub"
    )

    scrubbed = await record_scrubbed(scenario)
    dumped = scrubbed.to_json()
    for secret in ("xoxb-2231", "hunter22", "dana.lee@corp.example"):
        assert secret in raw.to_json() and secret not in dumped
    posted = next(s for s in scrubbed.tool_steps() if s.name == "slack__send_message")
    pseudonym = str(posted.args["text"]).split("contact ")[1].rstrip(".")
    assert pseudonym in scrubbed.final.answer, "one person, one pseudonym, everywhere"


@pytest.fixture
def deny_slack_posts(monkeypatch) -> None:
    """A harness change: PRE_TOOL_USE-style refusal of Slack posts."""
    original = ToolExecutor.call_tool

    async def call_tool(self, call, **kwargs) -> ToolResult:
        if call.name == "slack__send_message":
            return ToolResult(
                tool_call_id=call.id,
                name=call.name,
                content="denied by policy",
                is_error=True,
            )
        return await original(self, call, **kwargs)

    monkeypatch.setattr(ToolExecutor, "call_tool", call_tool)


async def test_a_harness_change_is_reported_at_the_first_divergent_step(
    deny_slack_posts,
) -> None:
    recording = Recording.load(corpus_path("quick-slack-post-with-secrets"))
    replayed = await replay(recording)

    report = diff_recordings(recording, replayed.recording, compare_effects=False)

    assert report.divergence is not None
    post_index = next(
        i
        for i, s in enumerate(recording.steps, 1)
        if isinstance(s, ToolStep) and s.name == "slack__send_message"
    )
    assert (report.divergence.step, report.divergence.field) == (post_index, "status")
    assert "expected: tool slack__send_message" in report.render()
    assert "-> denied" in report.render()


async def test_a_changed_tool_outcome_is_reported_where_it_happens() -> None:
    recording = Recording.load(corpus_path("react-ticket-error-then-success"))
    steps = list(recording.steps)
    index, failed = next(
        (i, s)
        for i, s in enumerate(steps)
        if isinstance(s, ToolStep) and s.status == "failed"
    )
    steps[index] = failed.model_copy(
        update={"status": "succeeded", "error": None, "result": "{}"}
    )
    tampered = recording.model_copy(update={"steps": steps})

    replayed = await replay(tampered)
    report = diff_recordings(recording, replayed.recording, compare_effects=False)

    assert report.divergence is not None
    assert report.divergence.step == index + 1
    assert report.divergence.field == "status"
