"""UI-B1 G5, ADR-GRANT-01 D8: the conversation's owner takes an "allow for this conversation" back.

Node writes ``interaction.grant_revoked`` on every open run of the conversation and on its root stream; the worker's
grant fold must drop the grant however it reached the run (its own ask, a copy from another run, or its spawn's pin),
so the next call to that tool and destination asks again. A later grant holds again; only Node can revoke.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.effects.flow import REVOKED_EVENT, ConversationGrants
from harness_core.journal.types import JournalEvent

if TYPE_CHECKING:
    from pydantic import JsonValue

FETCH = "web@searxng/fetch_url"
ONYX = "sha256:4c5c0f51fdaf2b50038b8c674f654f5407e153408d5688d5b5fbcf38395aa0c3"
OTHER = "sha256:e50a24657b577a620ef914bb453c46e4e282f380a38c19026372cd82626c16cb"


def _event(seq: int, kind: str, writer: str, data: dict[str, JsonValue], *, run_id: str | None = "run") -> JournalEvent:
    return JournalEvent(
        org_id="o",
        session_id="s",
        stream_id="s",
        seq=seq,
        run_id=run_id,
        type=kind,
        critical=False,
        ts="2026-11-09T09:00:00Z",
        writer=writer,  # type: ignore[arg-type]
        data=data,
    )


def _copy(seq: int, key: str = ONYX, sensitivity: str = "private") -> JournalEvent:
    return _event(
        seq,
        "interaction.granted",
        "node",
        {
            "interactionId": "ix.approve.t1.0",
            "scope": "conversation",
            "toolId": FETCH,
            "key": key,
            "sensitivity": sensitivity,
            "fromRunId": "other",
        },
    )


def _revoked(seq: int, key: str = ONYX, writer: str = "node", run_id: str | None = "run") -> JournalEvent:
    return _event(seq, REVOKED_EVENT, writer, {"toolId": FETCH, "key": key, "by": "p_owner"}, run_id=run_id)


def test_uib1_r1_a_revoked_copy_answers_nothing_and_leaves_other_destinations_held() -> None:
    grants = ConversationGrants()
    grants.apply([_copy(1), _copy(2, OTHER)])
    assert grants.allows(FETCH, ONYX, "private")
    grants.apply([_revoked(3)])
    assert not grants.allows(FETCH, ONYX, "public")
    assert grants.allows(FETCH, OTHER, "private")


def test_uib1_r2_a_revocation_drops_what_the_spawn_pinned_too() -> None:
    grants = ConversationGrants(inherited=[(FETCH, ONYX, "private")])
    assert grants.allows(FETCH, ONYX)
    # The session's own event (no run): what a root run admitted after every run ended reads.
    grants.apply([_revoked(1, run_id=None)])
    assert not grants.allows(FETCH, ONYX)
    assert grants.held_grants() == frozenset()


def test_uib1_r3_a_grant_given_again_after_the_revocation_holds_again() -> None:
    grants = ConversationGrants()
    grants.apply([_copy(1), _revoked(2), _copy(3, sensitivity="public")])
    assert grants.allows(FETCH, ONYX, "public")
    assert not grants.allows(FETCH, ONYX, "private")


def test_uib1_r4_an_own_ask_revoked_is_not_paired_again_by_a_late_answer() -> None:
    grants = ConversationGrants()
    ix = "ix.approve.t2.0"
    offer = {"interactionId": ix, "rule": "flow_external_write", "toolId": FETCH, "conversationGrant": True,
             "sensitivity": "private", "destination": {"label": "onyx.app", "key": ONYX}}
    grants.apply([
        _event(1, "interaction.explained", "worker", offer),
        _event(2, "interaction.granted", "node", {"interactionId": ix, "scope": "conversation"}),
        _event(3, "interaction.resolved", "node", {"interactionId": ix, "decision": "approved"}),
    ])
    assert grants.allows(FETCH, ONYX, "private")
    grants.apply([_revoked(4), _event(5, "interaction.resolved", "node", {"interactionId": ix, "decision": "approved"})])
    assert not grants.allows(FETCH, ONYX, "public")


def test_uib1_r5_only_node_revokes() -> None:
    grants = ConversationGrants()
    grants.apply([_copy(1), _revoked(2, writer="worker")])
    assert grants.allows(FETCH, ONYX, "private")
