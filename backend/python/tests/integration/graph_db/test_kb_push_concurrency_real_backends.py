"""Concurrency of pushes on real Neo4j and Arango.

PUSH_IT_SEEDS="a,b" (base seeds, default 0), PUSH_IT_RUNS_PER_SEED (default 3; the gate runs 20).

Runs against the integration graph stores like the other graph ITs (backend-matrix discovers it by
this environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD; it skips when
the variable is unset and the store is down, and fails when the variable is set and the store is not).
- the create/update race at fresh seeds, through one service and through two services on two graph connections;
- push vs trash: trash before the claim, while the push's
  transaction is open, after the commit; and free-running with random delays.
"""
from __future__ import annotations

import asyncio
import os
import random
from typing import TYPE_CHECKING

import pytest

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.modules.record_metadata.schemas import MetadataSchemaRegistry
from tests.integration.graph_db import test_kb_push_real_backends as IT
from tests.integration.real_graph import connect_arango, connect_neo4j

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

SEEDS = [int(s) for s in os.environ.get("PUSH_IT_SEEDS", "0").split(",")]
RUNS = int(os.environ.get("PUSH_IT_RUNS_PER_SEED", "3"))
env = IT.env  # the fixture, shared with test_kb_push_real_backends.py
CASES = [(s, r) for s in SEEDS for r in range(RUNS)]
SOFT = {"/services/platform/settings": {"featureFlags": {"ENABLE_SOFT_DELETE": True}}}


def check_invariants(outcomes: list) -> None:
    for v, r in outcomes:
        assert not isinstance(r, Exception) or (isinstance(r, PushRefusal) and r.status == 409), (v, repr(r))


@pytest.mark.parametrize("seed,run", CASES)
async def test_race_fresh_seeds(env, seed, run) -> None:
    await IT.test_same_key_race(env, seed * 1000 + run)


async def _second_service(env: IT.Env, backend: str, monkeypatch: pytest.MonkeyPatch) -> tuple[object, object]:
    graph2 = await (connect_neo4j(IT.logger, monkeypatch) if backend == "neo4j" else connect_arango(IT.logger, IT.ARANGO_DB))
    processor = DataSourceEntitiesProcessor(IT.logger, GraphDataStore(IT.logger, graph2), env.processor.config_service)
    processor.org_id = env.org_id
    processor.messaging_producer = env.processor.messaging_producer
    svc = IT.build_service(graph2, env.org_id, env.kb_service, processor, env.storage, env.events, env.cleanups,
                           MetadataSchemaRegistry(IT.FakeConfig()))
    return graph2, svc


@pytest.mark.parametrize("seed,run", CASES[: max(1, len(CASES) // 2)])
async def test_race_two_services(env, seed, run, request, monkeypatch) -> None:
    backend = request.node.callspec.params["env"]
    graph2, svc2 = await _second_service(env, backend, monkeypatch)
    try:
        IT.record_keys(env, "rec-1")
        rng = random.Random(seed * 7919 + run)
        versions = list(range(1, 17))
        rng.shuffle(versions)
        results = await asyncio.gather(*(env.push(env.body(version=v, text=f"content {v}"), service=(svc2 if i % 2 else None))
                                         for i, v in enumerate(versions)), return_exceptions=True)
        outcomes = list(zip(versions, results))
        check_invariants(outcomes)
        ok = [(v, r) for v, r in outcomes if not isinstance(r, Exception)]
        assert ok, outcomes
        rec = await env.record()
        assert rec["version"] == max(v for v, _ in ok)
        doc = rec["externalRecordId"]
        assert C.content_rev(env.storage.docs[doc]) == C.parse_revision_id(rec["externalRevisionId"])[2]
        assert env.live_docs() == {doc}, (env.live_docs(), doc)
        assert len([e for e in env.events if e["eventType"] in ("newRecord", "updateRecord")]) == len([1 for _, r in ok if r.event])
    finally:
        await graph2.disconnect()


# ---- push vs trash ---------------------------------------------------------------------------------

def soft_delete_on(env: IT.Env) -> IT.FakeConfig:
    cfg = IT.FakeConfig()
    cfg.data.update(SOFT)
    env.processor.config_service = cfg
    env.kb_service.config_service = cfg
    return cfg


async def trash(env: IT.Env, ext: str) -> dict:
    return await env.kb_service.delete_records_in_kb(env.kb_id, [env.key(ext)], env.user_id)


@pytest.mark.parametrize("point", ["before_claim", "inside_txn", "after_commit"])
async def test_push_vs_trash_at_each_step(env, point) -> None:
    soft_delete_on(env)
    ext = f"tr-{point}"
    IT.record_keys(env, ext)
    await env.push(env.body(ext=ext, version=1, text="old"))
    old = await env.record(ext)
    real_claim = env.graph.claim_push_record
    real_send = env.processor.messaging_producer.send_message
    trash_result = {}

    async def claim(**kw: object) -> object:
        if point == "before_claim" and "t" not in trash_result:
            trash_result["t"] = await trash(env, ext)
        r = await real_claim(**kw)
        if point == "inside_txn" and "t" not in trash_result:
            task = asyncio.ensure_future(trash(env, ext))
            trash_result["task"] = task
            await asyncio.sleep(0.5)  # the trash runs while this transaction holds the claim
            trash_result["t"] = "pending"
        return r

    async def send(topic: str, message: dict, key: str | None = None) -> None:
        if point == "after_commit" and "t" not in trash_result and message.get("eventType") == "updateRecord":
            trash_result["t"] = await trash(env, ext)
        return await real_send(topic, message, key=key)

    env.graph.claim_push_record = claim
    env.processor.messaging_producer.send_message = send
    try:
        try:
            r = await env.push(env.body(ext=ext, version=2, text="new"))
            outcome = ("ok", r.http_status, r.status)
        except PushRefusal as e:
            outcome = ("refused", e.status, e.reason)
        if "task" in trash_result:
            trash_result["t"] = await trash_result["task"]
    finally:
        env.graph.claim_push_record = real_claim
        env.processor.messaging_producer.send_message = real_send
    t = trash_result.get("t")
    rec = await env.record(ext)
    assert isinstance(t, dict) and t.get("success") is not False, ("trash failed", t)
    assert rec is not None and rec.get("isDeleted") is True, rec
    doc = rec["externalRecordId"]
    assert C.content_rev(env.storage.docs[doc]) == C.parse_revision_id(rec["externalRevisionId"])[2], "invariant I"
    if outcome[0] == "ok":
        assert outcome[1] == 200 and doc != old["externalRecordId"], "a 2xx push must leave its revision on the record"
    else:
        assert outcome[1:] == (409, "RECORD_IN_TRASH"), outcome
        assert doc == old["externalRecordId"]
    assert env.live_docs() == {doc}, (env.live_docs(), doc)
    restored = await env.kb_service.restore_record(env.key(ext), env.user_id, env.org_id)
    assert restored.get("success") is not False, restored
    after = await env.record(ext)
    assert not after.get("isDeleted") and after["externalRecordId"] == doc
    assert C.content_rev(env.storage.docs[doc]) == C.parse_revision_id(after["externalRevisionId"])[2]


@pytest.mark.parametrize("seed,run", CASES[: max(1, len(CASES) // 2)])
async def test_push_vs_trash_free_running(env, seed, run) -> None:
    soft_delete_on(env)
    rng = random.Random(seed * 104729 + run)
    ext = f"fr-{run}"
    IT.record_keys(env, ext)
    await env.push(env.body(ext=ext, version=1, text="old"))
    old = await env.record(ext)

    async def delayed(coro_fn: Callable[[], Awaitable[object]], d: float) -> object:
        await asyncio.sleep(d)
        return await coro_fn()

    push_r, trash_r = await asyncio.gather(
        delayed(lambda: env.push(env.body(ext=ext, version=2, text="new")), rng.random() * 0.05),
        delayed(lambda: trash(env, ext), rng.random() * 0.05),
        return_exceptions=True,
    )
    rec = await env.record(ext)
    assert not isinstance(trash_r, Exception) and trash_r.get("success") is not False, ("trash", trash_r)
    assert rec.get("isDeleted") is True
    doc = rec["externalRecordId"]
    assert C.content_rev(env.storage.docs[doc]) == C.parse_revision_id(rec["externalRevisionId"])[2]
    if isinstance(push_r, Exception):
        assert isinstance(push_r, PushRefusal) and (push_r.status, push_r.reason) in ((409, "RECORD_IN_TRASH"), (409, "PUSH_CONFLICT")), repr(push_r)
        assert doc == old["externalRecordId"]
    else:
        assert push_r.http_status == 200 and doc != old["externalRecordId"]
    assert env.live_docs() == {doc}, (env.live_docs(), doc)
