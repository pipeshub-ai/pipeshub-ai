"""The push's decide-again retry and its unknown-outcome commit, on real Neo4j and Arango.

Runs against the integration graph stores like the other graph ITs: NEO4J_IT_URI, NEO4J_IT_PASSWORD,
ARANGO_IT_URL, ARANGO_IT_PASSWORD.

- an error that must not retry propagates after one transaction;
- a loss on every attempt: count claims and transactions, time it, expect 409 PUSH_CONFLICT, one storage create, doc cleaned;
- a commit whose outcome is unknown (the store committed, the client saw an error).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.service import MAX_ATTEMPTS, RETRY_BUDGET_S
from tests.integration.graph_db import test_kb_push_real_backends as IT

if TYPE_CHECKING:
    from collections.abc import Callable

env = IT.env  # the fixture, shared with test_kb_push_real_backends.py
pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]


def _is_arango(env) -> bool:
    return hasattr(env.graph, "http_client")


def _errors(env: IT.Env, kind: str) -> Callable[[], Exception]:
    from neo4j.exceptions import ClientError, ConstraintError, TransientError

    from app.services.graph_db.arango.arango_http_client import ArangoQueryError
    if _is_arango(env):
        return {
            "lost": lambda: ArangoQueryError("Query failed", 410, '{"code":410,"error":true,"errorNum":1654,"errorMessage":"aborted"}'),
            "conflict": lambda: ArangoQueryError("Query failed", 409, '{"code":409,"error":true,"errorNum":1200,"errorMessage":"write-write conflict"}'),
            "unique": lambda: ArangoQueryError("Query failed", 409, '{"code":409,"error":true,"errorNum":1210,"errorMessage":"unique constraint violated"}'),
            "syntax": lambda: ArangoQueryError("Query failed", 400, '{"code":400,"error":true,"errorNum":1501,"errorMessage":"syntax error"}'),
            "plain": lambda: RuntimeError("something else"),
        }[kind]
    return {
        "lost": lambda: TransientError("Neo.TransientError.Transaction.Terminated: injected"),
        "conflict": lambda: TransientError("Neo.TransientError.Transaction.DeadlockDetected: injected"),
        "unique": lambda: ConstraintError("Neo.ClientError.Schema.ConstraintValidationFailed: injected"),
        "syntax": lambda: ClientError("Neo.ClientError.Statement.SyntaxError: injected"),
        "plain": lambda: RuntimeError("something else"),
    }[kind]


def _inject_claim(env: IT.Env, make: Callable[[], Exception], times: int = 10**9) -> dict:
    """Make claim_push_record raise ``make()`` (up to ``times``), counting calls."""
    real = env.graph.claim_push_record
    state = {"calls": 0, "fired": 0}

    async def claim(**kw: object) -> object:
        state["calls"] += 1
        if state["fired"] < times:
            state["fired"] += 1
            raise make()
        return await real(**kw)

    env.graph.claim_push_record = claim
    state["restore"] = lambda: setattr(env.graph, "claim_push_record", real)
    return state


@pytest.mark.parametrize("kind", ["unique", "syntax", "plain"])
async def test_an_error_that_is_not_a_lost_transaction_is_not_retried(env, kind) -> None:
    IT.record_keys(env, "rec-1")
    state = _inject_claim(env, _errors(env, kind))
    try:
        with pytest.raises(Exception) as e:
            await env.push(env.body())
    finally:
        state["restore"]()
    assert not isinstance(e.value, PushRefusal) or e.value.status >= 500
    assert state["calls"] == 1, state["calls"]
    assert await env.record() is None and env.storage.creates == 1 and not env.live_docs()


@pytest.mark.parametrize("kind", ["lost", "conflict"])
@pytest.mark.parametrize("action", ["create", "content"])
async def test_a_loss_on_every_attempt_ends_in_409_with_one_upload(env, kind, action) -> None:
    IT.record_keys(env, "rec-1")
    if action == "content":
        await env.push(env.body())
    before = await env.record()
    creates = env.storage.creates
    state = _inject_claim(env, _errors(env, kind))
    t = time.monotonic()
    try:
        with pytest.raises(PushRefusal) as e:
            await env.push(env.body(version=2, text="new") if action == "content" else env.body())
    finally:
        state["restore"]()
    took = time.monotonic() - t
    assert (e.value.status, e.value.reason) == (409, "PUSH_CONFLICT")
    assert env.storage.creates == creates + 1, "the storage document is reused across attempts"
    rec = await env.record()
    if action == "create":
        assert rec is None and not env.live_docs()
    else:
        assert rec["externalRecordId"] == before["externalRecordId"] and env.live_docs() == {before["externalRecordId"]}
    assert state["calls"] == MAX_ATTEMPTS, state["calls"]  # one bounded loop, not a loop in a loop
    assert took < RETRY_BUDGET_S + 2, took


@pytest.mark.parametrize("action", ["create", "content"])
async def test_a_commit_whose_outcome_is_unknown(env, action) -> None:
    """The store commits, then the client sees an error (a lost response). Is the referenced document kept?"""
    IT.record_keys(env, "rec-1")
    if action == "content":
        await env.push(env.body())
    real = env.graph.commit_transaction
    fired = {"n": 0}

    async def commit(txn: str) -> None:
        await real(txn)
        if fired["n"] == 0:
            fired["n"] += 1
            raise TimeoutError("commit response lost")

    env.graph.commit_transaction = commit
    try:
        try:
            r = await env.push(env.body(version=2, text="new") if action == "content" else env.body())
            assert r.http_status in (200, 201)
        except Exception as e:  # what the router would turn into a 500
            assert isinstance(e, TimeoutError)
    finally:
        env.graph.commit_transaction = real
    rec = await env.record()
    doc = rec["externalRecordId"] if rec else None
    assert rec is not None
    assert doc in env.live_docs(), "the record references a document the push cleaned up"
    assert C.content_rev(env.storage.docs[doc]) == C.parse_revision_id(rec["externalRevisionId"])[2]
    again = await env.push(env.body(version=2, text="new") if action == "content" else env.body())
    assert again.status == "unchanged"


async def test_an_identical_repush_heals_a_record_whose_document_is_missing(env: IT.Env) -> None:
    """The record is FAILED because indexing could not read its (lost) document: the same push stores it again."""
    IT.record_keys(env, "rec-1")
    first = await env.push(env.body())
    rec = await env.record()
    lost = rec["externalRecordId"]
    env.storage.gone.add(lost)
    await env.graph.batch_upsert_nodes([{**rec, "_key": rec.get("_key") or rec["id"], "indexingStatus": "FAILED"}], "records")
    creates = env.storage.creates
    again = await env.push(env.body())
    healed = await env.record()
    assert (again.http_status, again.status, again.event) == (200, "updated", "updateRecord")
    assert env.storage.creates == creates + 1 and healed["externalRecordId"] != lost and healed["version"] == 1
    assert healed["indexingStatus"] == "QUEUED"
    assert C.content_rev(env.storage.docs[healed["externalRecordId"]]) == first.content_rev
    assert (await env.push(env.body())).status == "unchanged"


async def test_an_identical_repush_of_a_failed_record_with_its_document_makes_no_storage_write(env: IT.Env) -> None:
    IT.record_keys(env, "rec-1")
    await env.push(env.body())
    rec = await env.record()
    await env.graph.batch_upsert_nodes([{**rec, "_key": rec.get("_key") or rec["id"], "indexingStatus": "FAILED"}], "records")
    creates = env.storage.creates
    assert (await env.push(env.body())).status == "unchanged" and env.storage.creates == creates


@pytest.mark.parametrize("action", ["create", "content"])
async def test_a_commit_the_server_finishes_after_the_client_gave_up_keeps_its_document(env: IT.Env, action: str) -> None:
    """the client's commit call times out, the store applies the commit 0.5 s later."""
    import asyncio

    IT.record_keys(env, "rec-1")
    if action == "content":
        await env.push(env.body())
    real = env.graph.commit_transaction
    fired = {"n": 0}

    async def commit(txn: str) -> None:
        if fired["n"] == 0:
            fired["n"] += 1
            asyncio.get_running_loop().call_later(0.5, lambda: asyncio.ensure_future(real(txn)))
            raise TimeoutError("commit response lost")
        await real(txn)

    env.graph.commit_transaction = commit
    try:
        with pytest.raises(TimeoutError):
            await env.push(env.body(version=2, text="new") if action == "content" else env.body())
        await asyncio.sleep(1.5)
    finally:
        env.graph.commit_transaction = real
    rec = await env.record()
    new_docs = set(env.storage.docs) - (set() if action == "create" else {sorted(env.storage.docs)[0]})
    assert new_docs <= env.live_docs() | ({sorted(env.storage.docs)[0]} if action == "content" else set()), env.cleaned()
    assert not (new_docs & env.cleaned()), "the push deleted a document the store may have committed"
    if rec is not None:  # the abort that followed the timeout may have won; if the commit landed, its document is live
        assert rec["externalRecordId"] in env.live_docs()
        assert C.content_rev(env.storage.docs[rec["externalRecordId"]]) == C.parse_revision_id(rec["externalRevisionId"])[2]
