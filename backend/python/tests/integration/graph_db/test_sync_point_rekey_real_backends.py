"""WP87-FU-4b: the ``SECRET_KEY`` rotation's sync-point step against a real Neo4j and a real ArangoDB.

RB-1 ``list_sync_points`` gives one org's sync points in ``syncPointKey`` order, a page at a time after a cursor,
     and never another org's.
RB-2 the step counts sealed delta links by key, reseals them under the current key with the provider's
     compare-and-set, and a connector reading under the new key alone gets every link back; a compare-and-set on
     a value that changed is refused.

The PostgreSQL store's case is in ``postgres/test_records.py``. Runs on Neo4j and ArangoDB (backend-matrix).
Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import secrets
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.sync_point.rekey import rekey
from app.core.org_context import for_org
from app.utils.encryption.encryption_service import EncryptionService
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "sync_point_rekey_it"
SYNC_POINTS = CollectionNames.SYNC_POINTS.value

logger = logging.getLogger("sync-point-rekey-it")


def _hashed(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        yield provider, uuid.uuid4().hex[:10]


async def _write(provider: IGraphDBProvider, org: str, key: str, data: dict) -> str:
    full = f"{org}/{key}"
    with for_org(org, job="test"):
        await provider.upsert_sync_point(full, {"orgId": org, "syncPointKey": full, **data}, SYNC_POINTS)
    return full


async def test_rb1_one_orgs_sync_points_page_in_key_order(graph: tuple[IGraphDBProvider, str]) -> None:
    provider, run = graph
    org, other = f"{run}-org", f"{run}-other"
    for key in ("c", "a", "e", "b", "d"):
        await _write(provider, org, key, {"cursor": key})
    await _write(provider, other, "a", {"cursor": "x"})
    try:
        with for_org(org, job="test"):
            first = await provider.list_sync_points(org, SYNC_POINTS, limit=2)
            rest = await provider.list_sync_points(org, SYNC_POINTS, after_key=first[-1]["syncPointKey"], limit=10)
        assert [d["cursor"] for d in first + rest] == ["a", "b", "c", "d", "e"]
        assert all(d["orgId"] == org for d in first + rest)
    finally:
        for key in ("a", "b", "c", "d", "e"):
            await provider.remove_sync_point(f"{org}/{key}", SYNC_POINTS)
        await provider.remove_sync_point(f"{other}/a", SYNC_POINTS)


async def test_rb2_the_step_reseals_delta_links_through_the_provider(
    graph: tuple[IGraphDBProvider, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider, run = graph
    old, new = secrets.token_hex(32), secrets.token_hex(32)
    monkeypatch.setenv("SECRET_KEY", new)
    monkeypatch.setenv("SECRET_KEY_PREVIOUS", old)
    monkeypatch.delenv("SECRET_KEY_NEXT", raising=False)
    monkeypatch.setattr(EncryptionService, "_instance", None)
    ring = EncryptionService.get_instance("aes-256-gcm", _hashed(new), logger)
    sealing = EncryptionService("aes-256-gcm", _hashed(old), logger)
    org = f"{run}-org"
    keys = [
        await _write(provider, org, f"drive-{i}", {"deltaLink": sealing.encrypt(f"https://graph.example/{i}"), "deltaLink_encrypted": True})
        for i in range(3)
    ]
    keys.append(await _write(provider, org, "plain", {"cursor": "c"}))

    async def only_this_org(_graph: IGraphDBProvider) -> list[str]:
        return [org]

    try:
        dry = await rekey(provider, ring, apply=False, page=2, orgs=only_this_org)
        assert (dry.total, dry.previous, dry.current, dry.unreadable) == (3, 3, 0, 0)
        applied = await rekey(provider, ring, apply=True, page=2, orgs=only_this_org)
        assert (applied.moved, applied.current, applied.previous) == (3, 3, 0)
        after = await rekey(provider, ring, apply=False, orgs=only_this_org)
        assert (after.current, after.previous) == (3, 0)
        # The ring writes under the new secret's HKDF key on a hardened install, not its SHA-256 key.
        retired = EncryptionService("aes-256-gcm", ring.secret_key, logger)
        with for_org(org, job="test"):
            points = await provider.list_sync_points(org, SYNC_POINTS)
            sealed = {p["syncPointKey"]: p["deltaLink"] for p in points if p.get("deltaLink_encrypted") is True}
            assert {k: retired.decrypt(v) for k, v in sealed.items()} == {
                f"{org}/drive-{i}": f"https://graph.example/{i}" for i in range(3)
            }
            assert not await provider.update_sync_point_if(
                keys[0], {"deltaLink": "a value it no longer holds"}, {"deltaLink": "x"}, SYNC_POINTS
            )
    finally:
        for key in keys:
            await provider.remove_sync_point(key, SYNC_POINTS)
        EncryptionService._instance = None  # pyright: ignore[reportPrivateUsage]
