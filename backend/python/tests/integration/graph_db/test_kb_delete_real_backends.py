"""Deleting a knowledge base that is being pushed to, on real Neo4j and ArangoDB (P3-08, 8a').

Runs against the integration graph stores like the other graph ITs (backend-matrix discovers it by this
environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD; it skips when the variable
is unset and the store is down, and fails when the variable is set and the store is not). Storage and the
broker are recording fakes; the push service, the processor, the KB service and the graph provider are real.

PUSH_IT_SEEDS="a,b" (default 0) and PUSH_IT_RUNS_PER_SEED (default 3; the gate runs 20) scale the race tests.

- T41   push update vs trash, interleaved before the claim, after it, before the commit and after it;
- T42   push create vs KB delete, committing before the marker, after it, in the record batches, after the KB went;
- T42u  an update push held open across the marker and the cascade;
- T42e  a delete that failed, then pushes (before and after the cascade started), and the create-only fallback;
- T42f  the finisher completes a delete the owner can no longer retry;
- T42g  two deleters at once (the finisher and an owner retry, and two owner retries);
- T42r  KB rename while pushes are open.
"""
from __future__ import annotations

import asyncio
import os
import random
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    PushClaimRefused,
)
from app.connectors.services import storage_release
from app.connectors.services.storage_release import GRACE_MS, StorageReleaseReconciler
from app.connectors.sources.localKB.handlers import kb_service as kb_module
from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push.contract import PushRefusal
from tests.integration import real_graph as RG
from tests.integration.graph_db import test_kb_push_real_backends as IT
from tests.integration.graph_db.test_kb_push_concurrency_real_backends import (
    soft_delete_on,
    trash,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

env = IT.env  # the fixture, shared with test_kb_push_real_backends.py
pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

SEEDS = [int(s) for s in os.environ.get("PUSH_IT_SEEDS", "0").split(",")]
RUNS = int(os.environ.get("PUSH_IT_RUNS_PER_SEED", "3"))
CASES = [(s, r) for s in SEEDS for r in range(RUNS)]
RECORDS, APPS = CollectionNames.RECORDS.value, CollectionNames.APPS.value


# ---- helpers ------------------------------------------------------------------------------------------

class Deleting:
    """The KB service armed to delete: a KV store, a recording broker, no storage service, no lane registry."""

    def __init__(self, env_: IT.Env, monkeypatch: pytest.MonkeyPatch) -> None:
        self.env = env_
        self.config = IT.FakeConfig()
        self.events: list[dict] = []
        kafka = MagicMock()

        async def publish_event(_topic: str, event: dict) -> bool:
            self.events.append(event)
            return True

        kafka.publish_event = publish_event
        env_.kb_service.config_service = self.config
        env_.kb_service.kafka_service = kafka
        monkeypatch.setattr(kb_module, "free_lane_of_deleted_connector", AsyncMock())
        self.released = AsyncMock()
        monkeypatch.setattr(kb_module, "release_connector_storage", self.released)
        self.cascade_results: list[dict] = []
        real = env_.graph.delete_connector_instance

        async def spy(**kw: object) -> dict:
            result = await real(**kw)
            self.cascade_results.append(result)
            return result

        env_.graph.delete_connector_instance = spy
        self.real_cascade = real

    async def delete(self) -> dict:
        return await self.env.kb_service.delete_knowledge_base(self.env.kb_id, self.env.user_id, self.env.org_id)

    async def finish(self) -> dict:
        return await self.env.kb_service.finish_delete(self.env.org_id, self.env.kb_id)

    def storage_cleaned(self) -> set[str]:
        return {d for e in self.events if e.get("eventType") == "deleteStoredDocuments" for d in e["payload"]["documentIds"]}


@pytest.fixture
def deleting(env, monkeypatch) -> Deleting:
    return Deleting(env, monkeypatch)


async def survivors(env_: IT.Env) -> list[dict]:
    return await env_.graph.get_documents_paginated(RECORDS, filters={"connectorId": env_.kb_id}, limit=200)


async def kb_doc(env_: IT.Env) -> dict | None:
    return await env_.graph.get_document(env_.kb_id, APPS)


async def push_outcome(env_: IT.Env, **kw: object) -> tuple:
    kw.setdefault("name", str(kw.get("ext", "rec-1")))
    try:
        r = await env_.push(env_.body(**kw))
    except PushRefusal as e:
        return ("refused", e.status, e.reason)
    return ("ok", r.http_status, r.status)


def arango(env_: IT.Env) -> bool:
    return env_.graph.__class__.__name__.startswith("Arango")


def untagged_leaks(env_: IT.Env) -> set[str]:
    """Documents of this KB the release by connector tag would not reach (none: every create tags it)."""
    return {d for d, tags in env_.storage.tags.items() if tags.get("connectorId") != env_.kb_id}


def after_release(env_: IT.Env) -> set[str]:
    """Live documents tagged with this KB once the storage release (delete by connector tag) has run."""
    return {d for d in env_.live_docs() if env_.storage.tags[d].get("connectorId") != env_.kb_id}


def wrap(obj: object, name: str, before: Callable[..., Awaitable[None]] | None = None,
         after: Callable[..., Awaitable[None]] | None = None) -> Callable[[], None]:
    real = getattr(obj, name)

    async def wrapped(*a: object, **kw: object) -> object:
        if before:
            await before(*a, **kw)
        result = await real(*a, **kw)
        if after:
            await after(result, *a, **kw)
        return result

    setattr(obj, name, wrapped)
    return lambda: setattr(obj, name, real)


# ---- T41: push update vs trash ----------------------------------------------------------------------------

@pytest.mark.parametrize("point", ["before_claim", "after_claim", "before_commit", "after_commit"])
@pytest.mark.parametrize("seed,run", CASES)
async def test_push_vs_trash(env, point, seed, run) -> None:
    soft_delete_on(env)
    rng = random.Random(seed * 7919 + run * 31 + len(point))
    ext = f"tr-{point}"
    IT.record_keys(env, ext)
    await env.push(env.body(ext=ext, name=ext, version=1, text="old"))
    old = await env.record(ext)
    state: dict = {}
    restores = []

    async def run_trash() -> None:
        if "t" not in state:
            state["t"] = "started"
            state["t"] = await trash(env, ext)

    async def concurrently(*_a: object, **_kw: object) -> None:
        """Trash while this push's transaction is open: the trash may wait on its locks, so it is not awaited here."""
        state["task"] = asyncio.ensure_future(run_trash())
        await asyncio.sleep(0.3 + rng.random() * 0.3)

    if point == "before_claim":
        restores.append(wrap(env.graph, "claim_push_record", before=lambda **kw: run_trash()))
    elif point == "after_claim":
        restores.append(wrap(env.graph, "claim_push_record", after=lambda result, **kw: concurrently()))
    elif point == "before_commit":
        restores.append(wrap(env.processor, "_refuse_if_kb_not_live", before=concurrently))
    else:
        restores.append(wrap(env.processor, "_publish_membership_sync", after=lambda result, *a: run_trash()))
    try:
        outcome = await push_outcome(env, ext=ext, version=2, text="new")
        if "task" in state:
            await state["task"]
    finally:
        for undo in restores:
            undo()
    t = state.get("t")
    rec = await env.record(ext)
    assert isinstance(t, dict) and t.get("success") is not False, ("the trash failed", t)
    assert rec is not None and rec.get("isDeleted") is True, rec
    doc = rec["externalRecordId"]
    assert C.content_rev(env.storage.docs[doc]) == C.parse_revision_id(rec["externalRevisionId"])[2], "invariant I"
    if outcome[0] == "ok":
        assert outcome[1:] == (200, "updated") and doc != old["externalRecordId"], (
            "a 2xx push must leave its revision on the record", outcome)
    else:
        assert outcome[1:] in ((409, "RECORD_IN_TRASH"), (409, "PUSH_CONFLICT")), outcome
        assert doc == old["externalRecordId"]
    assert env.live_docs() == {doc}, (env.live_docs(), doc)
    restored = await env.kb_service.restore_record(env.key(ext), env.user_id, env.org_id)
    assert restored.get("success") is not False, restored
    after = await env.record(ext)
    assert not after.get("isDeleted") and after["externalRecordId"] == doc
    assert C.content_rev(env.storage.docs[doc]) == C.parse_revision_id(after["externalRevisionId"])[2]


# ---- T42: push create vs KB delete --------------------------------------------------------------------------

POINTS = ["a_before_marker", "b_after_marker", "c_record_batches", "d_after_kb_node"]


def arm_push_during_delete(env_: IT.Env, point: str, push_now: Callable[..., Awaitable[None]]) -> list[Callable[[], None]]:
    """Make the delete call ``push_now`` at *point* (for ``a_before_marker`` the caller pushes first)."""
    if point == "b_after_marker":
        return [wrap(env_.graph, "_collect_connector_entities", before=push_now)]
    if point == "c_record_batches":
        async def in_batches(_tx: object, _keys: object, collection: str, *a: object, **kw: object) -> None:
            if collection == RECORDS:
                await push_now()

        return [wrap(env_.graph, "_delete_nodes_by_keys", before=in_batches)]
    if point == "d_after_kb_node":
        return [wrap(env_.graph, "delete_connector_instance", after=lambda result, **kw: push_now())]
    return []


@pytest.mark.parametrize("seed,run", CASES)
@pytest.mark.parametrize("point", POINTS)
async def test_push_vs_kb_delete(env, deleting, point, seed, run) -> None:
    IT.record_keys(env, "new-1", "kept-1")
    await env.push(env.body(ext="kept-1", name="kept-1", text="already here"))
    seen: dict = {}

    async def push_now(*_a: object, **_kw: object) -> None:
        seen.setdefault("push", await push_outcome(env, ext="new-1", text="during the delete"))

    if point == "a_before_marker":
        await push_now()
    undo = arm_push_during_delete(env, point, push_now)
    try:
        result = await deleting.delete()
    finally:
        for u in undo:
            u()

    assert result["success"] is True, (result, point)
    assert await kb_doc(env) is None
    assert await survivors(env) == [], f"a record of the deleted KB survived ({point})"
    assert untagged_leaks(env) == set() and after_release(env) == set()
    created = seen["push"]
    if point == "a_before_marker":
        assert created == ("ok", 201, "created"), created
    elif point == "d_after_kb_node":
        assert created[:2] == ("refused", 404), created
    else:
        assert created == ("refused", 409, "KB_BEING_DELETED"), created


@pytest.mark.parametrize("point", ["b_after_marker", "c_record_batches"])
async def test_push_vs_kb_delete_with_the_marker_off_lets_the_create_in(env, deleting, monkeypatch, point) -> None:
    """Negative control: without the DELETING marker the create lands during the cascade (201, not 409)."""
    IT.record_keys(env, "new-1")
    monkeypatch.setattr(env.kb_service, "_mark_deleting", AsyncMock(return_value=(None,)))
    seen: dict = {}

    async def push_now(*_a: object, **_kw: object) -> None:
        seen.setdefault("push", await push_outcome(env, ext="new-1", text="during the delete"))

    undo = arm_push_during_delete(env, point, push_now)
    try:
        await deleting.delete()
    finally:
        for u in undo:
            u()
    assert seen["push"] == ("ok", 201, "created"), seen


# ---- T42 window: the push passed the pre-commit read, then the whole delete ran ----------------------------------

@pytest.mark.parametrize(
    "fallback",
    [True, pytest.param(False, marks=pytest.mark.arango_only)],  # on Neo4j the delete waits for the KB lock the push holds, and its late pass removes the record
)
@pytest.mark.parametrize("seed,run", CASES)
async def test_create_that_commits_after_the_cascade_collected_is_removed(env, deleting, seed, run, fallback) -> None:
    if not fallback:
        env.service._undo_create_in_deleted_kb = AsyncMock()
    IT.record_keys(env, "late-1")
    gate_in, gate_out = asyncio.Event(), asyncio.Event()

    async def hold(*_a: object, **_kw: object) -> None:
        gate_in.set()
        await gate_out.wait()

    undo = wrap(env.processor, "_refuse_if_kb_not_live", after=lambda result, *a: hold())
    push_task = asyncio.ensure_future(push_outcome(env, ext="late-1", text="late"))
    try:
        await asyncio.wait_for(gate_in.wait(), 30)
        delete_task = asyncio.ensure_future(deleting.delete())
        if arango(env):
            # The delete runs to the end while the push is held open: the create commits after the cascade.
            result = await delete_task
            gate_out.set()
        else:
            # Neo4j: the delete waits for the KB lock the push holds, so the push goes first.
            await asyncio.sleep(0.5 + random.Random(seed * 13 + run).random() * 0.5)
            gate_out.set()
            result = await delete_task
        outcome = await push_task
    finally:
        undo()
        gate_out.set()
    assert result["success"] is True, result
    assert await kb_doc(env) is None
    if not fallback:
        # Negative control: nothing removes a create that committed after the cascade collected and finished.
        assert outcome == ("ok", 201, "created") and len(await survivors(env)) == 1, (outcome, await survivors(env))
        return
    assert outcome[0] == "refused" and outcome[1] in (404, 409), outcome
    assert await survivors(env) == []
    assert after_release(env) == set()


@pytest.mark.arango_only  # on Neo4j the marker write waits for the KB lock the create holds
@pytest.mark.parametrize("pre_commit_read", [True, False])
async def test_the_pre_commit_read_stops_a_create_before_it_publishes(env, deleting, monkeypatch, pre_commit_read) -> None:
    """A push opened before the marker read the KB as live in its snapshot; the plain read before the commit sees it."""
    IT.record_keys(env, "early-1")
    marked = {}

    async def mark_after_claim(result: object, **kw: object) -> None:
        marked["m"] = await env.kb_service._mark_deleting(env.kb_id)

    undo = [wrap(env.graph, "claim_push_record", after=mark_after_claim)]
    if not pre_commit_read:
        monkeypatch.setattr(env.processor, "_refuse_if_kb_not_live", AsyncMock())
    try:
        outcome = await push_outcome(env, ext="early-1", text="opened before the marker")
    finally:
        for u in undo:
            u()
    assert marked["m"] == (None,)
    assert outcome == ("refused", 409, "KB_BEING_DELETED"), outcome
    assert await env.record("early-1") is None
    published = [e for e in env.events if e["eventType"] == "newRecord"]
    if pre_commit_read:
        assert published == [], "refused before the commit: nothing was announced"
    else:
        assert len(published) == 1, "negative control: only the post-commit fallback caught it, after the announcement"


# ---- T42u: an update push held open across the marker and the cascade ------------------------------------------

async def _update_push_during_cascade(env, deleting, seed: int, run: int, *, hold_at: str, lost_attempts: int | None = None) -> dict:
    IT.record_keys(env, "upd-1")
    await env.push(env.body(ext="upd-1", name="upd-1", version=1, text="old"))
    rng = random.Random(seed * 101 + run)
    gate_in, gate_out = asyncio.Event(), asyncio.Event()
    claims = {"n": 0}

    async def hold(*_a: object, **_kw: object) -> None:
        claims["n"] += 1
        if claims["n"] == 1:
            gate_in.set()
            await gate_out.wait()

    if hold_at == "after_claim":
        undo = wrap(env.graph, "claim_push_record", after=lambda result, **kw: hold())
    else:
        undo = wrap(env.processor, "_refuse_if_kb_not_live", before=hold)
    at_records, lost_enough = asyncio.Event(), asyncio.Event()
    record_batches = {"n": 0}

    async def cascade_at_records(_tx: object, _keys: object, collection: str, *_a: object, **_kw: object) -> None:
        if collection == RECORDS:
            record_batches["n"] += 1
            at_records.set()
            if record_batches["n"] > (lost_attempts or 0):
                lost_enough.set()

    undo_records = wrap(env.graph, "_delete_nodes_by_keys", before=cascade_at_records) if arango(env) else (lambda: None)
    push_task = asyncio.ensure_future(push_outcome(env, ext="upd-1", version=2, text="new"))
    try:
        await asyncio.wait_for(gate_in.wait(), 30)
        delete_task = asyncio.ensure_future(deleting.delete())
        # the marker is committed and the cascade is at its first batch
        if lost_attempts is not None:
            # Hold the push's write on the record until the cascade has lost to it lost_attempts times and
            # started the next attempt, or has given up (no retry left). The cascade's retries back off for a
            # random 0.4 to 1.1 s in all, so a hold of fixed length can end before the retries do.
            await asyncio.wait_for(at_records.wait(), 30)
            lost = asyncio.ensure_future(lost_enough.wait())
            await asyncio.wait({lost, delete_task}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
            lost.cancel()
        else:
            await asyncio.sleep(0.3 + rng.random() * 0.4)
        gate_out.set()
        result = await delete_task
        outcome = await push_task
    finally:
        undo()
        undo_records()
        gate_out.set()
    return {"delete": result, "push": outcome, "claims": claims["n"]}


@pytest.mark.parametrize("hold_at", ["after_claim", "before_commit"])
@pytest.mark.parametrize("seed,run", CASES)
async def test_update_push_during_cascade(env, deleting, seed, run, hold_at) -> None:
    out = await _update_push_during_cascade(env, deleting, seed, run, hold_at=hold_at)

    assert out["delete"]["success"] is True, out
    assert out["push"] in (("refused", 409, "KB_BEING_DELETED"), ("refused", 404, "KB_NOT_FOUND"), ("ok", 200, "updated")), out
    assert await kb_doc(env) is None
    assert await survivors(env) == [], "a record of the deleted KB survived"
    assert after_release(env) == set()
    retried = sum(r.get("cascade_retries", 0) for r in deleting.cascade_results)
    if arango(env) and hold_at == "after_claim":
        # One side loses the write-write conflict on the record and runs again: the cascade's transaction
        # (counted) or the push (it decides again, and then finds the marker).
        assert retried >= 1 or out["claims"] > 1, (retried, out)
    # Neo4j: the marker write waits for the push (its transaction holds the KB node), so the order is the
    # push's; a deadlock between a batch and the push has either side as the victim, and both run again.


@pytest.mark.arango_only  # the lock wait is Arango's; Neo4j batches wait for the lock and are retried only on a deadlock
@pytest.mark.parametrize("lost_attempts", [1, 2])
async def test_update_push_held_past_the_lock_wait_makes_the_cascade_retry(env, deleting, lost_attempts) -> None:
    """Arango: the push holds its write on the record past the store's lock wait; the cascade loses, runs again, wins."""
    out = await _update_push_during_cascade(env, deleting, 0, 0, hold_at="after_claim", lost_attempts=lost_attempts)
    assert out["delete"]["success"] is True, out
    assert sum(r.get("cascade_retries", 0) for r in deleting.cascade_results) >= 1, out
    assert await kb_doc(env) is None and await survivors(env) == []


@pytest.mark.arango_only  # the conflict is certain only on Arango (a write-write conflict); on Neo4j the batch waits for the lock
async def test_update_push_during_cascade_without_the_cascade_retry_leaves_the_kb_deleting(env, deleting, monkeypatch) -> None:
    """Negative control (spike case M): without the retry, a conflict leaves the KB DELETING with its record."""
    with monkeypatch.context() as m:
        m.setattr("app.services.graph_db.arango.arango_http_provider.CASCADE_ATTEMPTS", 1)
        out = await _update_push_during_cascade(env, deleting, 0, 0, hold_at="after_claim", lost_attempts=1)
    kb = await kb_doc(env)
    assert out["delete"]["success"] is False, out
    assert kb is not None and kb.get("status") == "DELETING" and len(await survivors(env)) == 1, (out, kb)
    # the owner's permission edge went with the failed attempt, so only the server can finish: the finisher's path
    finished = await deleting.finish()
    assert finished["success"] is True and await kb_doc(env) is None


# ---- T42e: a delete that failed, then pushes --------------------------------------------------------------------

async def test_failed_kb_delete_before_the_cascade_restores_the_status(env, deleting) -> None:
    IT.record_keys(env, "kept-1", "new-1")
    await env.push(env.body(ext="kept-1", name="kept-1", text="kept"))
    before = await env.record("kept-1")
    env.graph.get_uploaded_document_ids = AsyncMock(side_effect=RuntimeError("list failed"))

    failed = await deleting.delete()

    assert failed["code"] == 503
    assert (await kb_doc(env)).get("status") in (None, "")
    assert (await env.push(env.body(ext="new-1", name="new-1", text="after"))).http_status == 201
    assert (await env.push(env.body(ext="kept-1", name="kept-1", version=2, text="kept v2"))).http_status == 200
    assert (await env.record("kept-1"))["version"] == 2 and before is not None


async def test_failed_kb_delete_after_the_cascade_started_keeps_the_kb_deleting(env, deleting) -> None:
    IT.record_keys(env, "kept-1", "new-1")
    await env.push(env.body(ext="kept-1", name="kept-1", text="kept"))
    env.graph.delete_connector_instance = AsyncMock(return_value={"success": False, "error": "injected"})

    failed = await deleting.delete()

    assert failed["code"] == 500
    assert (await kb_doc(env))["status"] == "DELETING"
    for ext, version in (("new-1", 1), ("kept-1", 2)):
        with pytest.raises(PushRefusal) as e:
            await env.push(env.body(ext=ext, name=ext, version=version, text="while deleting"))
        assert (e.value.status, e.value.reason) == (409, "KB_BEING_DELETED")
    assert (await env.record("kept-1"))["version"] == 1, "no existing record was touched or deleted"
    assert len(await survivors(env)) == 1
    env.graph.delete_connector_instance = deleting.real_cascade
    assert (await deleting.delete())["success"] is True
    assert await kb_doc(env) is None and await survivors(env) == []


async def test_the_post_commit_fallback_removes_a_create_but_never_touches_an_update(env, deleting) -> None:
    """The marker lands after the push's pre-commit read; the cascade then fails and the KB stays DELETING."""
    IT.record_keys(env, "kept-1", "new-1")
    await env.push(env.body(ext="kept-1", name="kept-1", text="kept"))
    marker: dict = {}

    async def start_marker(result: object, *a: object) -> None:
        """Start the marker while the push is open (on Neo4j it waits for the push's KB lock)."""
        marker["task"] = asyncio.ensure_future(env.kb_service._mark_deleting(env.kb_id))
        await asyncio.sleep(0.3)

    async def marker_lands(*a: object, **kw: object) -> None:
        """The push has committed; let the marker commit before the push's last read."""
        await marker["task"]

    undo = [
        wrap(env.processor, "_refuse_if_kb_not_live", after=start_marker),
        wrap(env.processor, "_publish_membership_sync", before=marker_lands),
    ]
    try:
        created = await push_outcome(env, ext="new-1", text="lands in a deleting kb")
        assert created == ("refused", 409, "KB_BEING_DELETED"), created
        assert await env.record("new-1") is None, "the create must be removed"
        assert len(await survivors(env)) == 1

        await env.graph.update_knowledge_base(kb_id=env.kb_id, updates={"status": None})
        updated = await push_outcome(env, ext="kept-1", version=2, text="update in a deleting kb")
        assert updated == ("ok", 200, "updated"), "an update is never compensated: its record is the owner's"
    finally:
        for u in undo:
            u()
    assert (await env.record("kept-1"))["version"] == 2
    assert (await kb_doc(env))["status"] == "DELETING"


async def test_the_fallback_applied_to_an_update_would_delete_an_existing_record(env, deleting) -> None:
    """Negative control: the harm the create-only rule prevents (an owner's record deleted by a failed delete)."""
    IT.record_keys(env, "kept-1")
    await env.push(env.body(ext="kept-1", name="kept-1", text="kept"))
    await env.kb_service._mark_deleting(env.kb_id)
    with pytest.raises(PushRefusal):
        await env.service._undo_create_in_deleted_kb(env.key("kept-1"), env.kb_id, env.org_id, None)
    assert await env.record("kept-1") is None


# ---- T42f: the finisher ----------------------------------------------------------------------------------------

class FakeLock:
    def __init__(self) -> None:
        self.try_acquire = AsyncMock(return_value=True)
        self.refresh = AsyncMock(return_value=True)


async def _stuck_delete(env, deleting, monkeypatch) -> None:
    """The cascade's node phase fails for good after the edges are gone (Arango: the owner's permission edge too)."""
    IT.record_keys(env, "a-1", "b-1")
    await env.push(env.body(ext="a-1", name="a-1", text="one"))
    await env.push(env.body(ext="b-1", name="b-1", text="two"))

    async def broken(*a: object, **kw: object) -> None:
        raise RuntimeError("injected: the node transaction fails")

    with monkeypatch.context() as m:
        m.setattr(env.graph, "_delete_isoftype_targets_from_collected", broken)
        failed = await deleting.delete()
    assert failed["code"] == 500, failed


@pytest.mark.parametrize("finisher_on", [True, False])
async def test_finisher_completes_stuck_delete(env, deleting, monkeypatch, finisher_on) -> None:
    await _stuck_delete(env, deleting, monkeypatch)
    assert (await kb_doc(env))["status"] == "DELETING"
    retry = await env.kb_service.delete_knowledge_base(env.kb_id, env.user_id, env.org_id)
    if arango(env):
        assert retry["code"] == 404, "the cascade took the owner's permission edge; only the server can finish"
    else:
        assert retry["success"] is True, "on Neo4j the owner can still retry"
        return

    (intent,) = await storage_release.list_pending_storage_releases(deleting.config)
    requested = intent["requestedAt"]
    helper = MagicMock()
    helper.release_connector_storage = AsyncMock(return_value=SimpleNamespace(completed=True))
    helper.close = AsyncMock()
    monkeypatch.setattr(storage_release, "StorageCleanupHelper", lambda *a, **k: helper)
    clock = {"now": requested + GRACE_MS - 1}

    async def no_lease(_kb: str) -> bool:
        return False

    reconciler = StorageReleaseReconciler(
        logger=MagicMock(), graph_provider=env.graph, config_service=deleting.config, lock=FakeLock(),
        now_ms=lambda: clock["now"], finish_kb_delete=env.kb_service.finish_delete if finisher_on else None,
        lease_held=no_lease,
    )
    assert await reconciler.tick() == "idle", "within the grace period the owner's own delete is left alone"

    clock["now"] = requested + GRACE_MS + 1
    if not finisher_on:
        assert await reconciler.tick() == "idle"
        assert (await kb_doc(env))["status"] == "DELETING" and len(await survivors(env)) == 2
        return
    assert await reconciler.tick() == "deleted"
    assert await kb_doc(env) is None
    assert await survivors(env) == []
    assert after_release(env) == set()
    assert await reconciler.tick() == "released"
    assert await storage_release.list_pending_storage_releases(deleting.config) == []


# ---- T42g: two deleters at once ---------------------------------------------------------------------------------

async def _collected(env_: IT.Env) -> dict[str, list[str]]:
    graph = env_.graph
    collected = await graph._collect_connector_entities(env_.kb_id, None, include_virtual_record_ids=False)
    targets, ok = await graph._collect_isoftype_targets(None, env_.kb_id)
    assert ok
    by_type: dict[str, list[str]] = {
        RECORDS: collected["record_keys"], CollectionNames.RECORD_GROUPS.value: collected["record_group_keys"],
        CollectionNames.ROLES.value: collected["role_keys"], CollectionNames.GROUPS.value: collected["group_keys"],
    }
    for t in targets:
        by_type.setdefault(t["collection"], []).append(t["key"])
    return by_type


async def _two_deleters(env, deleting, kind: str, *, stagger: bool = False) -> tuple[dict, dict]:
    """Two deleters past their access check and marker. With *stagger* the second one is held at its
    type-document step until the first has finished the whole delete: it then meets nodes already gone."""
    IT.record_keys(env, "r-1", "r-2", "r-3")
    for ext in ("r-1", "r-2", "r-3"):
        await env.push(env.body(ext=ext, name=ext, text=f"content {ext}"))
    if kind == "finisher_and_owner":
        await env.kb_service._mark_deleting(env.kb_id)  # the finisher only acts on a KB that is DELETING
    barrier = {"arrived": 0}
    both = asyncio.Event()
    real = deleting.real_cascade

    async def meet(**kw: object) -> dict:
        barrier["arrived"] += 1
        if barrier["arrived"] >= 2:
            both.set()
        await asyncio.wait_for(both.wait(), 30)
        result = await real(**kw)
        deleting.cascade_results.append(result)
        return result

    env.graph.delete_connector_instance = meet
    if stagger:
        steps = {"n": 0}
        step = env.graph._delete_isoftype_targets_from_collected

        async def second_waits(*a: object, **kw: object) -> tuple:
            steps["n"] += 1
            if steps["n"] == 2:
                for _ in range(600):
                    if deleting.cascade_results:
                        break
                    await asyncio.sleep(0.1)
            return await step(*a, **kw)

        env.graph._delete_isoftype_targets_from_collected = second_waits
    first = deleting.delete()
    second = deleting.finish() if kind == "finisher_and_owner" else deleting.delete()
    first_result, second_result = await asyncio.gather(first, second)
    return first_result, second_result


@pytest.mark.parametrize("kind", ["finisher_and_owner", "two_owner_retries"])
@pytest.mark.parametrize("seed,run", CASES)
async def test_concurrent_deleters(env, deleting, kind, seed, run) -> None:
    keys = None

    async def snapshot(*_a: object, **_kw: object) -> None:
        nonlocal keys
        if keys is None:
            keys = await _collected(env)

    undo = wrap(env.kb_service, "_mark_deleting", before=snapshot)
    try:
        a, b = await _two_deleters(env, deleting, kind)
    finally:
        undo()
    assert (a["success"], b["success"]) == (True, True), (a, b)
    assert keys and keys[RECORDS], keys
    for collection, ids in keys.items():
        assert await env.graph.get_existing_keys(collection, ids) == set(), f"{collection} nodes remain"
    assert await kb_doc(env) is None
    assert await survivors(env) == []
    # duplicate cleanup events are allowed; their effect is asserted once
    assert after_release(env) == set()


async def test_concurrent_deleters_with_the_count_rule_on_the_type_docs_fail(env, deleting) -> None:
    """Negative control (round 5): the isOfType step measured by a count makes the second deleter 500."""
    graph = env.graph

    async def count_rule(transaction: str, targets: list[dict], *args: object, **kw: object) -> tuple:
        by: dict[str, list[str]] = {}
        for t in targets:
            by.setdefault(t["collection"], []).append(t["key"])
        kept = {k: v for k, v in kw.items() if k in ("raise_on_error", "retry_conflicts", "stats")}
        total = 0
        for collection, ids in by.items():
            deleted, _failed = await graph._delete_nodes_by_keys(transaction, ids, collection, **kept)
            if deleted < len(ids):
                raise Exception(f"CRITICAL: Partial deletion of isOfType targets ({collection}: {deleted}/{len(ids)}).")
            total += deleted
        return (total, [])

    graph._delete_isoftype_targets_from_collected = count_rule
    a, b = await _two_deleters(env, deleting, "two_owner_retries", stagger=True)
    assert (a["success"], b["success"]) != (True, True), "the count rule must break the second deleter"


async def test_a_second_deleter_that_meets_the_type_documents_already_gone_succeeds(env, deleting) -> None:
    """The same schedule as the control above, with the real rule: none of the keys remain, so it is done."""
    a, b = await _two_deleters(env, deleting, "two_owner_retries", stagger=True)
    assert (a["success"], b["success"]) == (True, True), (a, b)
    assert await kb_doc(env) is None and await survivors(env) == []


# ---- T42r: KB rename while pushes are open -------------------------------------------------------------------------

@pytest.mark.parametrize("seed,run", CASES)
async def test_kb_rename_during_push(env, seed, run) -> None:
    IT.record_keys(env, "u-1", *[f"c-{i}" for i in range(4)])
    await env.push(env.body(ext="u-1", name="u-1", version=1, text="v1"))
    rng = random.Random(seed * 977 + run)

    async def later(coro_fn: Callable[[], Awaitable[object]]) -> object:
        await asyncio.sleep(rng.random() * 0.05)
        return await coro_fn()

    jobs = [later(lambda: env.push(env.body(ext="u-1", name="u-1", version=2, text="v2")))]
    jobs += [later(lambda i=i: env.push(env.body(ext=f"c-{i}", name=f"c-{i}", text=f"c{i}"))) for i in range(4)]
    renames = [later(lambda i=i: env.kb_service.update_knowledge_base(env.kb_id, env.user_id, {"name": f"renamed {run} {i}"}))
               for i in range(3)]
    results = await asyncio.gather(*jobs, *renames, return_exceptions=True)
    pushes, rename_results = results[: len(jobs)], results[len(jobs):]
    assert all(not isinstance(r, Exception) for r in pushes), pushes
    assert all(r.http_status in (200, 201) for r in pushes), pushes
    assert all(not isinstance(r, Exception) and r.get("success") for r in rename_results), rename_results


@pytest.mark.parametrize("hold", [0.3])
async def test_a_rename_waits_out_a_writer_holding_the_kb_document(env, hold) -> None:
    """A transaction holds the KB document's write past the store's lock wait (Arango: the rename gets a
    write-write conflict, 1200); the rename writes again and succeeds the first time, without a retry from the caller."""
    tx = await env.graph.begin_transaction(read=[APPS], write=[APPS])
    try:
        assert await env.graph.update_knowledge_base(kb_id=env.kb_id, updates={"description": "held"}, transaction=tx)
        rename = asyncio.ensure_future(env.kb_service.update_knowledge_base(env.kb_id, env.user_id, {"name": "renamed under a held write"}))
        await asyncio.sleep(hold)
    finally:
        await env.graph.commit_transaction(tx)
    result = await asyncio.wait_for(rename, 30)

    assert result.get("success") is True, result
    assert (await kb_doc(env))["name"] == "renamed under a held write"


async def test_connecting_a_second_test_worker_does_not_stall_an_open_transaction(env, monkeypatch) -> None:
    """Regression for the stall that failed the race gate on a shared store.

    A second worker's connect ran the schema DDL (CREATE ... IF NOT EXISTS / collection properties),
    which takes the schema lock even when it creates nothing. Queued behind this worker's open
    transaction, it held back every later read or new transaction of this worker, among them the push's
    own pre-commit read: a wait until the store's transaction timeout (120 s Neo4j, 60 s Arango). The
    connect now reads the schema first and runs the DDL only against a store that lacks it.
    """
    logger = IT.logger
    tx = await env.graph.begin_transaction(read=[APPS], write=[APPS])
    second = None
    try:
        assert await env.graph.update_knowledge_base(kb_id=env.kb_id, updates={"description": "open"}, transaction=tx)
        connecting = asyncio.ensure_future(
            RG.connect_arango(logger, IT.ARANGO_DB) if arango(env) else RG.connect_neo4j(logger, monkeypatch)
        )
        await asyncio.sleep(1.0)
        # what the pushing worker does next: a read outside the transaction, and a new transaction
        assert await asyncio.wait_for(env.graph.get_document(env.kb_id, APPS), 10) is not None
        other = await asyncio.wait_for(env.graph.begin_transaction(read=[RECORDS], write=[RECORDS]), 10)
        await env.graph.rollback_transaction(other)
        second = await asyncio.wait_for(connecting, 30)
    finally:
        await env.graph.commit_transaction(tx)
        if second is not None:
            await second.disconnect()


# ---- the marker itself, on a real store ---------------------------------------------------------------------------

async def test_a_deleting_kb_shows_on_reads_and_refuses_writes(env, deleting) -> None:
    env.graph.delete_connector_instance = AsyncMock(return_value={"success": False, "error": "injected"})
    await deleting.delete()

    got = await env.kb_service.get_knowledge_base(env.kb_id, env.user_id)
    assert got["status"] == "DELETING"
    listed = await env.kb_service.list_user_knowledge_bases(user_id=env.user_id, org_id=env.org_id, page=1, limit=50)
    assert [kb["status"] for kb in listed["knowledgeBases"] if kb["id"] == env.kb_id] == ["DELETING"]
    refused = await env.kb_service.create_folder_in_kb(env.kb_id, "f", env.user_id, env.org_id)
    assert (refused["code"], refused["errorCode"]) == (409, "KB_BEING_DELETED")
    renamed = await env.kb_service.update_knowledge_base(env.kb_id, env.user_id, {"name": "nope"})
    assert renamed["code"] == 409
    with pytest.raises(PushRefusal) as e:
        await env.service.authorize(env.kb_id, env.user_id)
    assert (e.value.status, e.value.reason) == (409, "KB_BEING_DELETED")
    env.graph.delete_connector_instance = deleting.real_cascade


async def test_the_pre_commit_read_raises_the_refusal_the_service_maps(env) -> None:
    await env.kb_service._mark_deleting(env.kb_id)
    with pytest.raises(PushClaimRefused) as e:
        await env.processor._refuse_if_kb_not_live(env.kb_id)
    assert e.value.claim.value == "KB_DELETING"
    await env.graph.update_knowledge_base(kb_id=env.kb_id, updates={"status": None})
    await env.processor._refuse_if_kb_not_live(env.kb_id)
