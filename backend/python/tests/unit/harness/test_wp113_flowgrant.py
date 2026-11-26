"""WP-113 FLOWGRANT: once a person allows a destination for the conversation, nothing in the conversation asks again
for it, with the same or less sensitive data: not the run that asked, not its parent, not a sibling or engine child.

Seen in a recorded conversation: a Claude Code child that read a private record asked 14 times, onyx.app three
times, pipeshub.com three times and the web search six, because a child's card never offered the conversation and a
grant given on a child's stream reached nothing else. Node now names each grant and writes a copy on every open run
(``ConversationGrantReach``); these tests hold the worker's side of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from harness_core.effects.flow import (
    LEGACY_GRANT_CEILING,
    ConversationGrants,
    GrantingLedger,
    grants_from_wire,
    grants_wire,
)
from harness_core.engines.durable.kernel import _EngineAttemptRun, _PresentingHold
from harness_core.journal.types import JournalEvent
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from pydantic import JsonValue

REPO = Path(__file__).resolve().parents[5]
RECORDED = json.loads((REPO / "contracts/harness/vectors/flow-grants-recorded.json").read_text())
FETCH = "web@searxng/fetch_url"
ONYX = "sha256:4c5c0f51fdaf2b50038b8c674f654f5407e153408d5688d5b5fbcf38395aa0c3"
PIPESHUB = "sha256:e50a24657b577a620ef914bb453c46e4e282f380a38c19026372cd82626c16cb"


def _event(
    seq: int,
    kind: str,
    writer: str,
    data: dict[str, JsonValue],
    *,
    stream: str = "child",
) -> JournalEvent:
    return JournalEvent(
        org_id="o",
        session_id="s",
        stream_id=stream,
        seq=seq,
        run_id=stream,
        type=kind,
        critical=False,
        ts="2026-11-07T12:32:30Z",
        writer=writer,
        data=data,  # type: ignore[arg-type]
    )


def _offer(
    ix: str, key: str, sensitivity: str = "private", tool: str = FETCH
) -> dict[str, JsonValue]:
    return {
        "interactionId": ix,
        "toolId": tool,
        "rule": "flow_external_write",
        "layer": "flow",
        "conversationGrant": True,
        "sensitivity": sensitivity,
        "destination": {"label": "onyx.app", "key": key},
    }


def _granted_on(
    grants: ConversationGrants,
    ix: str,
    key: str,
    sensitivity: str = "private",
    seq: int = 1,
) -> None:
    grants.apply(
        [
            _event(
                seq, "interaction.explained", "worker", _offer(ix, key, sensitivity)
            ),
            _event(
                seq + 1,
                "interaction.resolved",
                "node",
                {"interactionId": ix, "decision": "approved"},
            ),
            _event(
                seq + 2,
                "interaction.granted",
                "node",
                {
                    "interactionId": ix,
                    "scope": "conversation",
                    "toolId": FETCH,
                    "key": key,
                    "sensitivity": sensitivity,
                },
            ),
        ]
    )


def test_fg1_a_grant_another_run_was_given_holds_by_what_node_names() -> None:
    """The parent, a sibling or the session's next run gets Node's copy: it holds without an ask of its own."""
    grants = ConversationGrants()
    grants.apply(
        [
            _event(
                1,
                "interaction.granted",
                "node",
                {
                    "interactionId": "ix.approve.k1",
                    "scope": "conversation",
                    "toolId": FETCH,
                    "key": ONYX,
                    "sensitivity": "private",
                    "fromRunId": "run-child",
                },
                stream="s",
            )
        ]
    )
    assert grants.allows(FETCH, ONYX, "private")
    assert not grants.allows(FETCH, PIPESHUB, "private")
    assert not grants.allows("web@searxng/web_search", ONYX, "private")


def test_fg2_a_copy_never_pairs_with_an_ask_of_this_run_that_shares_its_id() -> None:
    """Loop ids repeat across runs (``ix.approve.t2.0``): the copy names its grant, never this run's own offer."""
    grants = ConversationGrants()
    grants.apply(
        [
            _event(
                1,
                "interaction.explained",
                "worker",
                _offer("ix.approve.t2.0", PIPESHUB),
            ),
            _event(
                2,
                "interaction.resolved",
                "node",
                {"interactionId": "ix.approve.t2.0", "decision": "approved"},
            ),
            _event(
                3,
                "interaction.granted",
                "node",
                {
                    "interactionId": "ix.approve.t2.0",
                    "scope": "conversation",
                    "toolId": FETCH,
                    "key": ONYX,
                    "sensitivity": "private",
                    "fromRunId": "run-other",
                },
            ),
        ]
    )
    assert grants.allows(FETCH, ONYX, "private")
    assert not grants.allows(FETCH, PIPESHUB, "private")


def test_fg3_a_grant_given_over_public_data_never_covers_private_data() -> None:
    grants = ConversationGrants()
    _granted_on(grants, "ix.approve.k1", ONYX, "public")
    assert grants.allows(FETCH, ONYX, "public")
    assert not grants.allows(FETCH, ONYX, "private")
    # Content nobody can name (a write's own reply) raises no grant: the classification levels hold it (OWD-15).
    assert grants.allows(FETCH, ONYX, "unknown")
    _granted_on(grants, "ix.approve.k2", ONYX, "private", seq=10)
    assert grants.allows(FETCH, ONYX, "private")


def test_fg4_an_older_grant_pairs_with_its_offer_and_keeps_the_offer_s_data() -> None:
    """A Node from before FLOWGRANT names only the ask: the worker's own offer says what it allows."""
    grants = ConversationGrants()
    grants.apply(
        [
            _event(
                1,
                "interaction.explained",
                "worker",
                _offer("ix.approve.k1", ONYX, "public"),
            ),
            _event(
                2,
                "interaction.resolved",
                "node",
                {"interactionId": "ix.approve.k1", "decision": "approved"},
            ),
            _event(
                3,
                "interaction.granted",
                "node",
                {"interactionId": "ix.approve.k1", "scope": "conversation"},
            ),
        ]
    )
    assert grants.allows(FETCH, ONYX, "public")
    assert not grants.allows(FETCH, ONYX, "private")


def test_fg5_a_child_inherits_each_grant_with_its_data() -> None:
    grants = ConversationGrants()
    _granted_on(grants, "ix.approve.k1", ONYX, "public")
    _granted_on(grants, "ix.approve.k2", PIPESHUB, "private", seq=10)
    wire = grants_wire(grants.held_grants())
    assert {"toolId": FETCH, "key": ONYX, "sensitivity": "public"} in wire
    child = ConversationGrants(grants_from_wire(wire))
    assert child.allows(FETCH, PIPESHUB, "private")
    assert not child.allows(FETCH, ONYX, "private")
    legacy = ConversationGrants(grants_from_wire([{"toolId": FETCH, "key": ONYX}]))
    assert legacy.allows(FETCH, ONYX, LEGACY_GRANT_CEILING)
    # The round 20 gate (PT-402 and others): a pin from before grants named their data holds at any data, private too.
    assert legacy.allows(FETCH, ONYX, "private") and legacy.allows(FETCH, ONYX, "unknown")


def test_fg6_grants_off_hold_nothing_even_when_node_names_them() -> None:
    grants = ConversationGrants(enabled=False)
    _granted_on(grants, "ix.approve.k1", ONYX)
    assert not grants.allows(FETCH, ONYX, "public")
    assert grants.held_grants() == frozenset()


class _Inner:
    def __init__(self) -> None:
        self.asked = 0

    def interaction_for(self, call: object) -> str:
        return "ix.approve.k9"

    async def hold(self, call: object) -> str:
        self.asked += 1
        return "denied"


async def test_fg7_an_engine_asks_nobody_when_a_grant_reached_its_stream_since_it_last_read() -> (
    None
):
    """A sibling or child already running reads Node's copy before it asks (one journal read per ask)."""
    view = SimpleNamespace(
        asking={}, reasons={"ix.approve.k9": _offer("ix.approve.k9", ONYX)}
    )
    inner = _Inner()
    hold = _PresentingHold(inner, view)  # type: ignore[arg-type]
    seen: list[object] = []

    async def granted(reason: object) -> bool:
        seen.append(reason)
        return True

    hold.granted = granted  # type: ignore[assignment]
    call = SimpleNamespace(
        call=SimpleNamespace(name="fetch_url", arguments={"url": "https://onyx.app/"})
    )
    assert await hold.hold(call) == "approved"  # type: ignore[arg-type]
    assert inner.asked == 0 and len(seen) == 1
    assert "ix.approve.k9" not in view.asking

    async def not_granted(reason: object) -> bool:
        return False

    view.reasons["ix.approve.k9"] = _offer("ix.approve.k9", ONYX)
    hold.granted = not_granted  # type: ignore[assignment]
    assert await hold.hold(call) == "denied"  # type: ignore[arg-type]
    assert inner.asked == 1


async def test_fg8_the_engine_re_reads_its_stream_and_checks_tool_destination_and_data() -> (
    None
):
    ledger = GrantingLedger(cast("object", SimpleNamespace()))  # type: ignore[arg-type]
    synced: list[bool] = []

    async def sync() -> None:
        synced.append(True)
        ledger.grants.apply(
            [
                _event(
                    1,
                    "interaction.granted",
                    "node",
                    {
                        "interactionId": "ix.approve.k1",
                        "scope": "conversation",
                        "toolId": FETCH,
                        "key": ONYX,
                        "sensitivity": "public",
                        "fromRunId": "run-parent",
                    },
                )
            ]
        )

    run = SimpleNamespace(ledger=ledger, sync=sync, log=HarnessLog.named("tests.wp113"))
    since = _EngineAttemptRun._granted_since
    assert await since(run, _offer("ix", ONYX, "public"))  # type: ignore[arg-type]
    assert synced == [True]
    assert not await since(run, _offer("ix", ONYX, "private"))  # type: ignore[arg-type]
    assert not await since(run, _offer("ix", PIPESHUB, "public"))  # type: ignore[arg-type]
    # An ask that offered no grant (an admin's own, the policy webhook) is never answered by one.
    assert not await since(
        run, {**_offer("ix", ONYX, "public"), "conversationGrant": False}
    )  # type: ignore[arg-type]


def _replay(choose_conversation: bool) -> tuple[list[str], list[tuple[str, str]]]:
    """The recorded asks in order. An ask a grant answers is never asked; the person allows each asked one for the
    conversation (or once, as recorded)."""
    grants = ConversationGrants()
    asked: list[str] = []
    seq = 0
    for event in RECORDED["events"]:
        if event["type"] != "interaction.explained":
            continue
        data = event["data"]
        destination = data["destination"]
        if grants.allows(data["toolId"], destination["key"], data["sensitivity"]):
            continue
        asked.append(destination["label"])
        ix = data["interactionId"]
        seq += 3
        batch = [
            _event(seq, "interaction.explained", "worker", data),
            _event(
                seq + 1,
                "interaction.resolved",
                "node",
                {"interactionId": ix, "decision": "approved"},
            ),
        ]
        if choose_conversation:
            batch.append(
                _event(
                    seq + 2,
                    "interaction.granted",
                    "node",
                    {
                        "interactionId": ix,
                        "scope": "conversation",
                        "toolId": data["toolId"],
                        "key": destination["key"],
                        "sensitivity": data["sensitivity"],
                    },
                )
            )
        grants.apply(batch)
    return asked, sorted(grants.held())


def test_fg10_replay_one_grant_per_place_and_the_second_and_third_onyx_calls_do_not_ask() -> (
    None
):
    once, _ = _replay(choose_conversation=False)
    assert len(once) == 14
    assert (
        once.count("onyx.app") == 3
        and once.count("pipeshub.com") == 3
        and once.count("SearXNG") == 6
    )
    asked, held = _replay(choose_conversation=True)
    assert asked == [
        "github.com/onyx-dot-app/onyx (public, read-only, about 68 MB)",
        "github.com/pipeshub-ai/pipeshub-ai (public, read-only, about 42 MB)",
        "onyx.app",
        "pipeshub.com",
        "SearXNG",
    ]
    assert (FETCH, ONYX) in held


@pytest.mark.parametrize("label", ["onyx.app", "pipeshub.com", "SearXNG"])
def test_fg11_each_place_repeats_under_one_key(label: str) -> None:
    """The web search is one destination (its provider), a site one per host: the grant's key is the same each time."""
    keys = {
        (e["data"]["toolId"], e["data"]["destination"]["key"])
        for e in RECORDED["events"]
        if e["type"] == "interaction.explained"
        and e["data"]["destination"]["label"] == label
    }
    assert len(keys) == 1


def test_fg12_an_offer_that_names_no_data_holds_at_any_data_as_before() -> None:
    """Round 20 gate (policy table PT-402..PT-644, gitf08): a grant paired with an offer that does not say what the run
    had read held for any data before FLOWGRANT and still does; unknown ranks with public, so it is not the widest."""
    grants = ConversationGrants()
    offer = {k: v for k, v in _offer("ix.approve.k1", ONYX).items() if k != "sensitivity"}
    grants.apply(
        [
            _event(1, "interaction.explained", "worker", offer),
            _event(2, "interaction.resolved", "node", {"interactionId": "ix.approve.k1", "decision": "approved"}),
            _event(3, "interaction.granted", "node", {"interactionId": "ix.approve.k1", "scope": "conversation"}),
        ]
    )
    assert grants.allows(FETCH, ONYX, "private")
    assert grants.allows(FETCH, ONYX, "unknown")
