"""GA-26 (G-49, C20.50): the P0 journeys pass offline, on the mock harness, the VCR gateway and the seeded tenant.

Each journey replays from its committed cassette (recorded from a scripted model, never a provider) under the
no-network guard, so a pass needs no model, no connector and no network. A journey whose model calls drift from the
cassette fails with the first call that differs (re-record with ``python -m tests.harness_replay.ga26 --record``).

| Journey | Path | What the offline variant shows | Left to other lanes |
|---|---|---|---|
| J-01 | loop | knowledge searches over Slack, Jira and Drive; every citation is a record the user reads; nothing outside the user's access reaches the model | brief saved as an artifact (J-03 shows the artifact path); TTFT from a warm pool (perf lane) |
| J-02 | loop | the same prompt for three users with different access: answers differ, each cites only its own readable records, a read of a record outside the user's access is refused, each run names its own principal | |
| J-03 | engine | the xlsx skill offered and loaded, the user's open P1 issues fetched, the file in the run's outputs published as the session's artifact, downloadable, with the run's publication record | a real .xlsx from a real engine (docker WS-05/06) |
| J-04 | loop | after reading Slack, the email waits for the person (Rule of Two) on a card naming the recipient and the sources; sent once after approval, never after a denial | edit-then-approve (the journal takes a decision only) |
| J-15 | engine | an engine version the admin did not allow is refused before any sandbox; a model outside the run's grant is refused at the gateway with no provider call | the builder hiding them (frontend) |
| J-25 | engine | the engine's question card; the run waits holding nothing; the answer two hours later continues the engine's conversation | |

The default gate runs these (the whole module replays in under a second); ``-m ga26`` selects them alone, as
.github/workflows/harness-ga26.yml does. They were an opt-in lane until R23-FU-GA26: a main sync that swapped a
knowledge tool in the default list (``find_records_by_value`` for ``find_records_by_person``) left four replays failing
in a lane nobody ran on that branch. Each cassette is also recorded again from its scripted model and compared whole,
so a change in what the harness offers or a script answers names the journey to re-record.
"""

from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.types import ENGINE_MODEL_ID
from harness_core.gateway.errors import ModelNotGranted
from harness_core.interactions.ports import RunBinding
from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.testing.cassette import Cassette, CassetteTransport
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)
from harness_core.testing.engines.fake_engine import FAKE_VERSION
from harness_core.testing.engines.gateway_model import GatewayModelEndpoint
from harness_core.testing.model_gateway import body_of
from tests.harness_replay.ga26.journeys import (
    BOARD,
    JOURNEYS,
    RECIPIENT,
    SEARCH,
    XLSX_FILE,
    EngineJourney,
    EngineRun,
    LoopJourney,
    cassette_path,
    record,
    replayer,
    tenant,
)
from tests.support.parity_cases.wave1_durable import finalized_citations

if TYPE_CHECKING:
    from tests.harness_replay.legacy_driver import DriveResult

pytestmark = pytest.mark.ga26


async def replay_loop(journey_id: str, decisions: list[dict[str, Any]] | None = None) -> DriveResult:
    journey = JOURNEYS[journey_id]
    assert isinstance(journey, LoopJourney)
    transport = replayer(journey_id)
    result = await journey.run(transport, decisions)
    transport.assert_played()
    return result


async def replay_engine(journey_id: str) -> tuple[EngineRun, CassetteTransport]:
    journey = JOURNEYS[journey_id]
    assert isinstance(journey, EngineJourney)
    transport = replayer(journey_id)
    run = await journey.run(transport)
    transport.assert_played()
    return run, transport


def cited(result: DriveResult) -> set[str]:
    assert result.events is not None
    return set(finalized_citations(result.events))


def principal(result: DriveResult) -> str:
    assert result.events is not None
    (admitted,) = [e for e in result.events if e.type == "run.admitted"]
    return str(admitted.data["principalRef"])


def test_ga26_every_journey_has_a_scripted_cassette_and_runs_under_the_network_guard() -> None:
    assert os.environ.get("TEST_NETWORK_GUARD", "fail") == "fail", "the lane runs with every dial refused"
    for journey_id in JOURNEYS:
        cassette = Cassette.load(cassette_path(journey_id))
        assert cassette.manifest.source == "scripted", f"{journey_id} was recorded from a provider"
        assert cassette.interactions, journey_id


@pytest.mark.parametrize("journey_id", sorted(JOURNEYS))
async def test_ga26_each_cassette_is_what_its_scripted_model_records_today(journey_id: str) -> None:
    committed = Cassette.load(cassette_path(journey_id))
    fresh = await record(JOURNEYS[journey_id])
    drift = [
        f"call {n}: recorded tools {old.request.tools}, offered now {new.request.tools}"
        for n, (old, new) in enumerate(zip(committed.interactions, fresh.interactions, strict=False), start=1)
        if old.request != new.request
    ]
    assert committed == fresh, (
        f"{journey_id}'s cassette is out of date ({'; '.join(drift) or 'the replies differ'}); "
        f"re-record with `python -m tests.harness_replay.ga26 --record --journey {journey_id}`"
    )


async def test_ga26_j01_the_brief_searches_three_sources_and_cites_only_what_the_user_reads() -> None:
    t = tenant()
    result = await replay_loop("j01-alice")
    assert result.recording.final.status == "completed"
    assert [s.name for s in result.recording.tool_steps()].count(SEARCH) == 3
    sources = cited(result)
    assert sources and sources <= t.readable("alice")
    assert {t.connector_of(doc) for doc in sources} >= {"SLACK", "JIRA", "DRIVE"}
    assert result.shown_records is not None and result.shown_records <= t.readable("alice")
    assert "atlas-design-v1" not in result.shown_records, "a record in the trash"


async def test_ga26_j02_the_same_prompt_answers_each_user_from_their_own_access() -> None:
    t = tenant()
    runs = {user: await replay_loop(journey) for user, journey in
            (("alice", "j01-alice"), ("bob", "j02-bob"), ("carol", "j02-carol"))}
    answers = {user: r.recording.final.answer for user, r in runs.items()}
    assert len(set(answers.values())) == 3, answers
    for user, result in runs.items():
        assert result.recording.final.status == "completed", user
        assert cited(result) <= t.readable(user), user
        assert result.shown_records is not None and result.shown_records <= t.readable(user), user
        assert principal(result) == f"p_{user}"
    # The board update is Alice's alone: the same read is answered for her and refused for the others.
    assert BOARD.removeprefix("rec-") in runs["alice"].shown_records  # type: ignore[operator]
    for user in ("bob", "carol"):
        assert BOARD.removeprefix("rec-") not in runs[user].shown_records  # type: ignore[operator]
        assert "1.2 million" not in answers[user]
    # Carol may use Slack only: no Jira or Drive record reaches her run.
    assert {t.connector_of(doc) for doc in runs["carol"].shown_records or ()} <= {"SLACK"}


async def test_ga26_j04_the_email_waits_for_the_person_and_is_sent_once_after_approval() -> None:
    decisions: list[dict[str, Any]] = []
    result = await replay_loop("j04-approved", decisions)
    assert result.events is not None
    types = [e.type for e in result.events]
    assert len(decisions) == 1, "one approval card"
    (explained,) = [e.data for e in result.events if e.type == "interaction.explained"]
    assert explained["rule"] == "flow_external_write"
    assert explained["destination"]["label"] == RECIPIENT  # type: ignore[index]
    assert "#atlas standup, 21 October 2025" in [s["title"] for s in explained["sources"]]  # type: ignore[index, union-attr]
    # Nothing was sent while the card waited: the send's intent follows the person's decision.
    sends = [i for i, e in enumerate(result.events) if e.type == "tool.intent" and e.data.get("toolId") == "pipeshub@gmail/send_email"]
    assert sends and sends[0] > types.index("interaction.resolved")
    effects = [e for e in result.world.saas.effects if e.op == "gmail.send_email"]
    assert len(effects) == 1 and effects[0].payload["to"] == RECIPIENT
    assert result.recording.final.answer == f"Sent the standup summary to {RECIPIENT}."


async def test_ga26_j04_a_declined_email_is_never_sent() -> None:
    result = await replay_loop("j04-denied")
    assert result.events is not None
    assert "tool.denied" in [e.type for e in result.events]
    assert [e for e in result.world.saas.effects if e.op == "gmail.send_email"] == []
    assert result.recording.final.answer == "I did not send the email."


async def test_ga26_j03_the_xlsx_skill_writes_the_file_the_session_can_download() -> None:
    run, transport = await replay_engine("j03-xlsx")
    w = run.world
    assert w.journal.status("run-j03") == "completed"
    asked = transport.cassette().interactions
    assert "skill" in asked[0].request.tools, "the pinned skill is offered to the engine"
    calls = [c for i in asked for e in i.events if e.type == "complete" for c in e.response.message.tool_calls or []]
    assert any(c.name == "skill" and "xlsx" in str(c.arguments) for c in calls), "the engine loaded the xlsx skill"
    assert [key[0] for key in w.effects] == ["jira_search"]
    binding = RunBinding(ORG, SESSION, "run-j03")
    (artifact,) = [a for a in w.artifacts.listing(binding) if a.name == XLSX_FILE]
    assert artifact.lifecycle == "final"
    assert await w.artifacts.content(binding, artifact.artifact_id, artifact.version)
    history = await w.publications.history(binding)
    assert len(history.published) == 1, "the run's publication links the file to the run and its session"


async def test_ga26_j25_the_question_waits_holding_nothing_and_the_answer_continues_the_engine() -> None:
    run, _ = await replay_engine("j25-ask")
    w = run.world
    outcomes = {
        r: [e.data.get("outcome") for e in w.events(r) if e.type == "run.completed"] for r in ("run-ask", "run-answer")
    }
    assert outcomes == {"run-ask": ["needs_input"], "run-answer": ["answered"]}
    assert ("question", "Which region should the report cover?") in w.presenter.calls
    assert w.journal.open_interactions("run-ask") == {}
    assert isinstance(w.adapter, ScriptedEngineAdapter)
    assert w.adapter.restored[-1] is not None, "the answer restored the engine's own conversation"


async def test_ga26_j15_what_the_admin_did_not_allow_is_refused_before_it_runs() -> None:
    other = dataclasses.replace(FAKE_VERSION, version="9.9.9")
    w = EngineWorld()
    w.admit("run-other", "say hi", spec=engine_run_spec(dataclasses.replace(engine_profile(), engine=other)))
    for _ in range(3):
        if w.journal.status("run-other") in TERMINAL_STATUSES:
            break
        _ = await w.attempt("run-other")
    assert w.journal.status("run-other") == "failed"
    failed = [e.data for e in w.events("run-other") if e.type in ("run.failed", "run.quarantined")]
    assert failed and "version_not_allowed" in str(failed[-1])
    assert w.broker.acquisitions == 0, "no sandbox for an engine the admin did not allow"

    never = CassetteTransport.replay(Cassette.model_validate(
        {"manifest": {"name": "j15", "source": "scripted", "provider": "scripted"}}
    ))
    endpoint = GatewayModelEndpoint(never)
    binding = endpoint.rig.pin(endpoint.run_id, transport=never)
    with pytest.raises(ModelNotGranted):
        await endpoint.rig.gateway.chat(
            endpoint.rig.token(binding), {}, body_of({"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
        )
    assert ENGINE_MODEL_ID != "gpt-4o" and never.played == 0, "the provider was never asked"


@pytest.mark.parametrize(("given", "expected"), [
    ({}, "test"),
    ({"SECRET_KEY": ""}, "test"),
    ({"SECRET_KEY": "", "SECRET_KEY_FILE": "/run/secrets/key"}, ""),
    ({"SECRET_KEY": "given"}, "given"),
    ({"SECRET_KEY_FILE": "/run/secrets/key"}, None),
])
def test_the_recorder_sets_the_test_secret_key_only_when_none_is_given(given: dict[str, str], expected: str | None) -> None:
    from tests.harness_replay.ga26.__main__ import (
        TEST_SECRET_KEY,
        ensure_test_secret_key,
    )
    env = dict(given)
    ensure_test_secret_key(env)
    assert env.get("SECRET_KEY") == (TEST_SECRET_KEY if expected == "test" else expected)
