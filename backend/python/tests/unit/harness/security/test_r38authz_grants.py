"""R38-AUTHZ (SEC-B-01, SEC-C-04): an "allow for this conversation" counts only for runs of the person who gave it.

The root runs of a shared conversation share one stream (the session's), so a worker for Alice's run reads every
decision Bob makes there: his named grants, his own asks' offers, approvals and grants. The worker's fold
(``ConversationGrants`` with the run's ``principal``) holds only the grants its actor gave; a grant naming nobody was
written before grants named their giver and stays the run's own; the owner's revocation drops any grant. A child holds
what its parent's actor held, as its admission pinned it.

The card's reason also names the call's ``argsDigest`` for a Node that accepts it (``approval_args_digest``), so Node
can decline the same call (same tool, same arguments) the person already declined in another run of the conversation.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from app.harness.approvals import flow_guarded
from app.harness.flow_destination import pipeshub_destination
from app.harness.labels import SessionSourceLedger, inherited_grants
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.effects.ask_reason import card_reason
from harness_core.effects.flow import (
    REVOKED_EVENT,
    ConversationGrants,
    GrantingLedger,
    reason_for_journal,
)
from harness_core.effects.types import args_digest
from harness_core.journal.types import APPROVAL_ARGS_DIGEST, JournalEvent
from harness_core.kernel.durable.ports import allow_all
from harness_core.logs import HarnessLog
from tests.unit.harness.test_qa6fix_explained_skew import (
    OLDER_NODE,
    POST,
    engine_reasons,
    engine_world,
    held,
    loop_world,
    org_asks,
)
from tests.unit.harness.test_wp29_session_grants import READ, _asked, _call, _world
from tests.unit.harness_core.durable.support import RUN, reply, scripted

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.kernel.durable import DurableKernel
    from tests.unit.harness_core.durable.support import World

LOG = HarnessLog.named("tests.r38authz")
FETCH = "web@searxng/fetch_url"
ONYX = "sha256:4c5c0f51fdaf2b50038b8c674f654f5407e153408d5688d5b5fbcf38395aa0c3"
ALICE, BOB = "p_alice", "p_bob"
ALICE_RUN, BOB_RUN = "run-alice", "run-bob"
DIGEST = "sha256:" + "0" * 64


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


def _event(seq: int, kind: str, writer: str, data: dict[str, JsonValue], *, run_id: str | None) -> JournalEvent:
    return JournalEvent(
        org_id="o",
        session_id="s",
        stream_id="s",
        seq=seq,
        run_id=run_id,
        type=kind,
        critical=False,
        ts="2026-11-13T09:00:00Z",
        writer=writer,  # type: ignore[arg-type]
        data=data,
    )


def _named(seq: int, by: object = BOB, *, run_id: str = ALICE_RUN, **extra: JsonValue) -> JournalEvent:
    """Node's copy on ``run_id`` of a grant given on Bob's run (FLOWGRANT), naming who gave it."""
    data: dict[str, JsonValue] = {
        "interactionId": "ix.approve.b.0",
        "scope": "conversation",
        "toolId": FETCH,
        "key": ONYX,
        "sensitivity": "private",
        "fromRunId": BOB_RUN,
        **extra,
    }
    if by is not None:
        data["by"] = cast("JsonValue", by)
    return _event(seq, "interaction.granted", "node", data, run_id=run_id)


def _paired(first: int, ix: str, *, run_id: str, by: str | None, named: bool = True) -> list[JournalEvent]:
    """An ask on ``run_id`` the person ``by`` allowed for the conversation: the offer, their approval, Node's grant."""
    offer: dict[str, JsonValue] = {
        "interactionId": ix,
        "rule": "flow_external_write",
        "toolId": FETCH,
        "conversationGrant": True,
        "sensitivity": "private",
        "destination": {"label": "onyx.app", "key": ONYX},
    }
    resolved: dict[str, JsonValue] = {"interactionId": ix, "decision": "approved", "channel": "web"}
    granted: dict[str, JsonValue] = {"interactionId": ix, "scope": "conversation"}
    if named:
        granted |= {"toolId": FETCH, "key": ONYX, "sensitivity": "private"}
    if by is not None:
        resolved["by"] = by
        granted["by"] = by
    return [
        _event(first, "interaction.explained", "worker", offer, run_id=run_id),
        _event(first + 1, "interaction.resolved", "node", resolved, run_id=run_id),
        _event(first + 2, "interaction.granted", "node", granted, run_id=run_id),
    ]


# SEC-B-01: another participant's grant never answers this run's asks


def test_sec_b_01_bob_s_named_grant_never_allows_alice_s_run() -> None:
    alice = ConversationGrants(principal=ALICE)
    alice.apply([_named(1)])
    assert not alice.allows(FETCH, ONYX, "public")
    assert alice.held_grants() == frozenset()


def test_sec_b_01_bob_s_own_paired_grant_on_the_shared_root_stream_never_allows_alice_s_run() -> None:
    stream = _paired(1, "ix.approve.b.0", run_id=BOB_RUN, by=BOB)
    alice, bob = ConversationGrants(principal=ALICE), ConversationGrants(principal=BOB)
    alice.apply(stream)
    bob.apply(stream)
    assert not alice.allows(FETCH, ONYX, "public")
    assert bob.allows(FETCH, ONYX, "private")


def test_sec_b_01_bob_s_unnamed_grant_paired_by_his_approval_never_allows_alice_s_run() -> None:
    # Node names who resolved even where the grant itself names nothing: the approval does not pair for Alice.
    stream = _paired(1, "ix.approve.b.0", run_id=BOB_RUN, by=BOB, named=False)
    stream[2] = _event(3, "interaction.granted", "node", {"interactionId": "ix.approve.b.0", "scope": "conversation"},
                       run_id=BOB_RUN)
    alice = ConversationGrants(principal=ALICE)
    alice.apply(stream)
    assert not alice.allows(FETCH, ONYX, "public")


def test_sec_b_01_alice_s_own_grant_allows_her_run() -> None:
    alice = ConversationGrants(principal=ALICE)
    alice.apply([*_paired(1, "ix.approve.a.0", run_id=ALICE_RUN, by=ALICE), _named(4, ALICE, fromRunId=ALICE_RUN)])
    assert alice.allows(FETCH, ONYX, "private")


def test_sec_b_01_a_grant_naming_nobody_is_the_run_s_own_as_before() -> None:
    legacy = ConversationGrants(principal=ALICE)
    legacy.apply([_named(1, None)])
    assert legacy.allows(FETCH, ONYX, "private")
    paired = ConversationGrants(principal=ALICE)
    paired.apply(_paired(1, "ix.approve.a.0", run_id=ALICE_RUN, by=None, named=False))
    assert paired.allows(FETCH, ONYX, "private")


def test_sec_b_01_without_a_principal_every_grant_holds_as_before() -> None:
    grants = ConversationGrants()
    grants.apply([_named(1)])
    assert grants.allows(FETCH, ONYX, "private")


def test_sec_b_01_the_owner_s_revocation_drops_bob_s_grant_on_bob_s_run() -> None:
    bob = ConversationGrants(principal=BOB)
    bob.apply([_named(1, run_id=BOB_RUN)])
    assert bob.allows(FETCH, ONYX)
    revoked = {"toolId": FETCH, "key": ONYX, "by": "p_owner"}
    bob.apply([_event(2, REVOKED_EVENT, "node", revoked, run_id=None)])
    assert not bob.allows(FETCH, ONYX)


# Bypass variants: anything but the run's exact principal ref is someone else's


@pytest.mark.parametrize(
    "by",
    ["P_alice", "p_ALICE", " p_alice", "p_alice ", "p_alice\n", "", "p_", "p_alice​", "ｐ_alice", 0, True,
     ["p_alice"], {"ref": "p_alice"}, None],
    ids=["case-prefix", "case-name", "lead-space", "trail-space", "newline", "empty", "bare-prefix", "zero-width",
         "fullwidth", "int", "bool", "list", "object", "null"],
)
def test_sec_b_01_a_by_that_is_not_exactly_the_run_s_principal_is_not_the_run_s(by: object) -> None:
    alice = ConversationGrants(principal=ALICE)
    # Present with a value that is not Alice's ref (null included): not a grant written before ``by``, and not hers.
    data: dict[str, JsonValue] = {"interactionId": "ix.approve.b.0", "scope": "conversation", "toolId": FETCH,
                                  "key": ONYX, "sensitivity": "private", "by": cast("JsonValue", by)}
    alice.apply([_event(1, "interaction.granted", "node", data, run_id=ALICE_RUN)])
    assert not alice.allows(FETCH, ONYX, "public")
    stream = _paired(2, "ix.approve.a.0", run_id=ALICE_RUN, by=None, named=False)
    stream[1] = _event(3, "interaction.resolved", "node",
                       {"interactionId": "ix.approve.a.0", "decision": "approved", "by": cast("JsonValue", by)},
                       run_id=ALICE_RUN)
    alice.apply(stream)
    assert not alice.allows(FETCH, ONYX, "public")


def test_sec_b_01_a_copy_naming_alice_s_run_but_given_by_bob_is_not_hers() -> None:
    alice = ConversationGrants(principal=ALICE)
    alice.apply([_named(1, BOB, fromRunId=ALICE_RUN)])
    assert not alice.allows(FETCH, ONYX, "public")


def test_sec_b_01_bob_s_approval_of_a_colliding_interaction_never_pairs_with_alice_s_offer() -> None:
    ix = "ix.approve.same.0"
    alice = ConversationGrants(principal=ALICE)
    # Alice's run offered the grant; Bob's run, on the same stream, approves and is granted an ask with the same id.
    [offer, *_] = _paired(1, ix, run_id=ALICE_RUN, by=ALICE)
    _, bob_resolved, bob_granted = _paired(1, ix, run_id=BOB_RUN, by=BOB, named=False)
    alice.apply([offer, bob_resolved.model_copy(update={"seq": 2}), bob_granted.model_copy(update={"seq": 3})])
    assert not alice.allows(FETCH, ONYX, "public")
    # Even an approval that names Alice, written on Bob's run, pairs with no offer of hers.
    forged = _event(4, "interaction.resolved", "node", {"interactionId": ix, "decision": "approved", "by": ALICE},
                    run_id=BOB_RUN)
    alice.apply([forged])
    assert not alice.allows(FETCH, ONYX, "public")


def test_sec_b_01_a_replayed_grant_of_bob_s_at_a_later_seq_is_still_bob_s() -> None:
    alice = ConversationGrants(principal=ALICE)
    alice.apply([_named(1), _named(2), _named(3, sensitivity="public")])
    assert not alice.allows(FETCH, ONYX, "public")


def test_sec_b_01_an_offer_bob_answered_is_not_awaited_by_alice_s_run() -> None:
    # Alice's engine re-reads the stream while an offer is unanswered; Bob's answer ends that, though it grants her
    # nothing, so her worker does not keep re-reading for an answer that already came.
    alice = ConversationGrants(principal=ALICE)
    offer, resolved, _ = _paired(1, "ix.approve.b.0", run_id=BOB_RUN, by=BOB)
    alice.apply([offer])
    assert alice.awaiting()
    alice.apply([resolved])
    assert not alice.awaiting() and not alice.allows(FETCH, ONYX)


# A child holds what its parent's actor held


def _spec(principal: str, pinned: list[JsonValue]) -> RunSpec:
    return cast("RunSpec", SimpleNamespace(extensions={"x.pipeshub": {"inheritedGrants": pinned}}, principal_ref=principal))


def test_sec_b_01_a_child_runs_as_its_parent_s_actor_and_reads_only_their_grants() -> None:
    child = inherited_grants(_spec(ALICE, [{"toolId": FETCH, "key": ONYX, "sensitivity": "public"}]))
    assert child.principal == ALICE and child.allows(FETCH, ONYX, "public")
    # Bob's grant copied onto the child's own stream answers none of its asks.
    child.apply([_named(1, BOB, run_id="child-1", sensitivity="private")])
    assert not child.allows(FETCH, ONYX, "private")


def test_sec_b_01_the_parent_pins_only_what_its_actor_holds() -> None:
    parent = GrantingLedger(SessionSourceLedger(), ConversationGrants(principal=ALICE))
    other = "sha256:e50a24657b577a620ef914bb453c46e4e282f380a38c19026372cd82626c16cb"
    parent.apply([_named(1, BOB), _named(2, ALICE, key=other, interactionId="ix.approve.a.0")])
    assert parent.context_grants() == [{"toolId": FETCH, "key": other, "sensitivity": "private"}]


# End to end on the reference journal: two root runs of one conversation, two people


def _kernel(world: World, run_id: str, principal: str, *replies: object) -> DurableKernel:
    _, port = scripted(*replies)  # type: ignore[arg-type]
    ledger = GrantingLedger(SessionSourceLedger(), ConversationGrants(principal=principal))
    approval = flow_guarded(allow_all, ledger=ledger, run_id=run_id, log=LOG, rules=flow_rules(LOG),
                            destination=pipeshub_destination, grants=ledger.grants)
    return world.kernel(port, approval=approval, ledger=ledger)


async def test_sec_b_01_on_the_journal_bob_s_allow_for_the_conversation_never_answers_alice() -> None:
    world = _world()
    assert (await world.run(_kernel(world, RUN, BOB, READ, _call("post_message", "sales")))).status == "suspended"
    [requested] = world.events("interaction.requested")
    world.journal.resolve_interaction(RUN, str(requested.data["interactionId"]), "approved", by=BOB, scope="conversation")
    [granted] = world.events("interaction.granted")
    assert granted.data["by"] == BOB, "the grant names who gave it"
    assert (await world.run(_kernel(world, RUN, BOB, _call("post_message", "sales"), reply("done")))).status == "completed"

    world.admit("run-alice", text="post it to sales too")
    alice = _kernel(world, "run-alice", ALICE, READ, _call("post_message", "sales"))
    assert (await world.run(alice, "run-alice")).status == "suspended"
    assert _asked(world, "run-alice") == [("post_message", "#sales")], "Alice is asked herself"

    world.admit("run-bob-2", text="and again")
    again = _kernel(world, "run-bob-2", BOB, READ, _call("post_message", "sales"), reply("done"))
    assert (await world.run(again, "run-bob-2")).status == "completed"
    assert _asked(world, "run-bob-2") == [], "Bob's own grant still answers his next run"


# argsDigest on the card's reason, for a Node that accepts it


def test_args_digest_is_the_call_s_and_reaches_only_a_journal_that_names_it() -> None:
    reason = {"rule": "writes", "toolId": FETCH, "conversationGrant": False, "argsDigest": DIGEST}
    assert reason_for_journal(reason, layers=True, args_digest=True) == reason
    assert "argsDigest" not in (reason_for_journal(reason, layers=True) or {})
    assert "argsDigest" not in (reason_for_journal(reason, layers=False) or {})
    flow = {**reason, "rule": "flow_external_write", "layer": "flow"}
    assert reason_for_journal(flow, layers=False, args_digest=True) == {k: v for k, v in flow.items() if k != "layer"}


def test_args_digest_is_the_same_for_the_same_call_whatever_asked() -> None:
    from harness_core.kernel.core.messages import ToolCall
    from tests.unit.harness.test_wp97_approval_explain import SAVE, _AsksSilently

    args = {"summary": "Printer down", "project": "OPS"}
    one = card_reason(_AsksSilently(), ToolCall(id="a", name="x", arguments=args), SAVE, LOG)
    other = card_reason(_AsksSilently(), ToolCall(id="b", name="y", arguments=dict(reversed(args.items()))), SAVE, LOG)
    assert one["argsDigest"] == other["argsDigest"] == args_digest(args)
    assert str(one["argsDigest"]).startswith("sha256:") and len(str(one["argsDigest"])) == 71


async def test_args_digest_the_loop_journals_it_when_the_claim_accepts_it() -> None:
    [reason] = await held(loop_world(), org_asks(), POST)
    assert reason.data["argsDigest"] == args_digest({"channel": "sales", "text": "hello"})
    [older] = await held(loop_world(journal_accepts=OLDER_NODE), org_asks(), POST) or [None]
    assert older is None or "argsDigest" not in older.data


async def test_args_digest_the_loop_leaves_it_out_for_a_node_that_does_not_name_it() -> None:
    from harness_core.journal.types import (
        APPROVAL_EDITS,
        APPROVAL_REASONS,
        APPROVAL_RULE_LAYERS,
    )

    accepts = frozenset({APPROVAL_REASONS, APPROVAL_RULE_LAYERS, APPROVAL_EDITS})
    [reason] = await held(loop_world(journal_accepts=accepts), org_asks(), POST)
    assert "argsDigest" not in reason.data and reason.data["rule"] == "writes"
    [named] = await held(loop_world(journal_accepts=accepts | {APPROVAL_ARGS_DIGEST}), org_asks(), POST)
    assert named.data["argsDigest"] == args_digest({"channel": "sales", "text": "hello"})


async def test_args_digest_an_engine_journals_it_only_when_the_claim_accepts_it() -> None:
    from harness_core.journal.types import APPROVAL_REASONS, APPROVAL_RULE_LAYERS

    engine_args = {"channel": "sales", "text": "hello"}
    [reason] = await engine_reasons(engine_world(org_asks()))
    assert reason["argsDigest"] == args_digest(engine_args)
    accepts = frozenset({APPROVAL_REASONS, APPROVAL_RULE_LAYERS})
    [without] = await engine_reasons(engine_world(org_asks(), journal_accepts=accepts))
    assert "argsDigest" not in without
    [named] = await engine_reasons(engine_world(org_asks(), journal_accepts=accepts | {APPROVAL_ARGS_DIGEST}))
    assert named["argsDigest"] == args_digest(engine_args)
