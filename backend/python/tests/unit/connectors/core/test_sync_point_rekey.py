"""WP87-FU-4b (ADR-KEY-01 D8): the ``SECRET_KEY`` rotation counts and moves connector sync points' sealed fields.

SP-1  dry-run counts every org's sealed sync points by key and writes nothing; apply reseals them under the
      current key, and a deployment on the new key alone (the previous one retired) reads every delta link.
SP-2  a sync point a connector rewrote between the read and the write is left as written and counted previous.
SP-3  a wrong previous key reads as unreadable and nothing is written.
SP-4  only the fields still under the previous key are rewritten, by a compare-and-set on just those; a sync point
      with no sealed field is not counted.
SP-5  the walk pages by key through every org, active or not, external or not.
SP-6  main prints counts only, a failure by our message or by type; the module takes only ``--apply``.
SP-7  at volume (QAFINAL-FU-VOL): page reads per org and writes per stale point within the pre-registered budget.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.connectors.core.base.sync_point import rekey as sync_point_rekey
from app.connectors.core.base.sync_point.rekey import main, rekey, sealed_fields
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    sync_point_cipher,
)
from app.core.org_context import current_org_id
from app.utils.encryption.encryption_service import EncryptionService
from app.utils.encryption.secret_key_ring import SecretKeyRingError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_LOG = logging.getLogger("test_sync_point_rekey")
ORG_A, ORG_B = "org-a", "org-b"


def _secret() -> str:
    return secrets.token_hex(32)


def _cipher_of(secret: str) -> EncryptionService:
    return EncryptionService("aes-256-gcm", hashlib.sha256(secret.encode()).hexdigest(), _LOG)


class ContractGraph:
    """The three provider methods the step uses, as every graph backend's contract states them: one org's sync
    points in ``syncPointKey`` order after a cursor, a compare-and-set on stored values, and every org."""

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}
        self.orgs = [{"_key": ORG_A, "isExternal": False}, {"_key": ORG_B, "isExternal": True}]
        self.pages: list[tuple[str, str | None]] = []
        self.before_write: Any = None

    async def get_all_orgs(self, *, active: bool = True, is_external: bool = False, raise_on_error: bool = False) -> list[dict]:
        assert active is False and raise_on_error is True
        return [org for org in self.orgs if org["isExternal"] is is_external]

    async def list_sync_points(self, org_id: str, collection: str, *, after_key: str | None = None, limit: int = 500) -> list[dict]:
        assert collection == "syncPoints" and current_org_id() == org_id
        self.pages.append((org_id, after_key))
        keys = sorted(k for k, p in self.points.items() if p["orgId"] == org_id and (after_key is None or k > after_key))
        return [dict(self.points[k]) for k in keys[:limit]]

    async def update_sync_point_if(self, sync_point_key: str, expected: dict[str, Any], sync_point_data: dict, collection: str) -> bool:
        if self.before_write is not None:
            self.before_write(sync_point_key)
        point = self.points.get(sync_point_key)
        if point is None or any(point.get(field) != value for field, value in expected.items()):
            return False
        point.update(sync_point_data)
        return True

    # What SyncPoint's data store calls.
    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.points.setdefault(key, {}).update(data)

    async def get_sync_point(self, key: str, *, raise_on_error: bool = False) -> dict[str, Any] | None:
        return dict(self.points[key]) if key in self.points else None


class _Store:
    def __init__(self, graph: ContractGraph) -> None:
        self.graph = graph

    @contextlib.asynccontextmanager
    async def transaction(self) -> AsyncIterator[ContractGraph]:
        yield self.graph


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    old, new = _secret(), _secret()
    monkeypatch.setenv("SECRET_KEY", old)
    monkeypatch.delenv("SECRET_KEY_PREVIOUS", raising=False)
    monkeypatch.delenv("SECRET_KEY_NEXT", raising=False)
    monkeypatch.setattr(EncryptionService, "_instance", None)
    yield old, new
    EncryptionService._instance = None  # pyright: ignore[reportPrivateUsage]


def _switch(monkeypatch: pytest.MonkeyPatch, current: str, previous: str | None) -> EncryptionService:
    monkeypatch.setenv("SECRET_KEY", current)
    if previous is None:
        monkeypatch.delenv("SECRET_KEY_PREVIOUS", raising=False)
    else:
        monkeypatch.setenv("SECRET_KEY_PREVIOUS", previous)
    monkeypatch.setattr(EncryptionService, "_instance", None)
    return sync_point_cipher()


async def _connector_points(graph: ContractGraph, org: str, count: int) -> list[SyncPoint]:
    """Sync points written the way a connector writes them: through ``SyncPoint``, the delta link sealed."""
    points = []
    for i in range(count):
        point = SyncPoint(f"conn-{org}", org, SyncDataPointType.RECORDS, cast("Any", _Store(graph)))
        await point.update_sync_point(f"drive-{i}", {"deltaLink": f"https://graph.example/{org}/{i}?token=t{i}", "n": i}, ["deltaLink"])
        points.append(point)
    return points


def _graph(graph: ContractGraph) -> IGraphDBProvider:
    return cast("IGraphDBProvider", graph)


async def test_sp1_dry_run_counts_apply_reseals_and_the_new_key_alone_reads_every_delta_link(
    keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = keys
    graph = ContractGraph()
    written = [*await _connector_points(graph, ORG_A, 3), *await _connector_points(graph, ORG_B, 2)]
    graph.points["org-a/plain"] = {"orgId": ORG_A, "syncPointKey": "org-a/plain", "cursor": "c"}
    ring = _switch(monkeypatch, new, old)
    before = json.dumps(graph.points, sort_keys=True)
    dry = await rekey(_graph(graph), ring, apply=False)
    assert (dry.total, dry.current, dry.previous, dry.moved, dry.unreadable) == (5, 0, 5, 0, 0)
    assert json.dumps(graph.points, sort_keys=True) == before
    applied = await rekey(_graph(graph), ring, apply=True)
    assert (applied.total, applied.current, applied.previous, applied.moved) == (5, 5, 0, 5)
    after = await rekey(_graph(graph), ring, apply=False)
    assert (after.current, after.previous, after.moved) == (5, 0, 0)
    _ = _switch(monkeypatch, new, None)
    for i, point in enumerate(written[:3]):
        point.encryption_service = sync_point_cipher()
        read = await point.read_sync_point(f"drive-{i}")
        assert read["deltaLink"] == f"https://graph.example/{ORG_A}/{i}?token=t{i}" and "deltaLink_encrypted" not in read


async def test_sp2_a_sync_point_the_connector_rewrote_meanwhile_is_left_as_written(
    keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = keys
    graph = ContractGraph()
    _ = await _connector_points(graph, ORG_A, 2)
    ring = _switch(monkeypatch, new, old)
    raced = "org-a/conn-org-a/records/drive-0"
    fresh = ring.encrypt("https://graph.example/the-connectors-own")

    def connector_syncs(key: str) -> None:
        if key == raced:
            graph.points[raced]["deltaLink"] = fresh

    graph.before_write = connector_syncs
    report = await rekey(_graph(graph), ring, apply=True)
    assert (report.total, report.moved, report.previous) == (2, 1, 1)
    assert graph.points[raced]["deltaLink"] == fresh
    graph.before_write = None
    again = await rekey(_graph(graph), ring, apply=False)
    assert (again.current, again.previous) == (2, 0)


async def test_sp3_a_wrong_previous_key_is_unreadable_and_nothing_is_written(
    keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _old, new = keys
    graph = ContractGraph()
    _ = await _connector_points(graph, ORG_A, 2)
    before = json.dumps(graph.points, sort_keys=True)
    ring = _switch(monkeypatch, new, _secret())
    report = await rekey(_graph(graph), ring, apply=True)
    assert (report.total, report.unreadable, report.moved, report.previous) == (2, 2, 0, 0)
    assert json.dumps(graph.points, sort_keys=True) == before


async def test_sp4_only_the_stale_fields_move_by_a_compare_and_set_on_just_those(
    keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = keys
    stale = _cipher_of(old).encrypt("stale-link")
    graph = ContractGraph()
    ring = _switch(monkeypatch, new, old)
    current = ring.encrypt("current-link")
    graph.points["org-a/k"] = {
        "orgId": ORG_A, "syncPointKey": "org-a/k", "deltaLink": stale, "deltaLink_encrypted": True,
        "pageToken": current, "pageToken_encrypted": True, "cursor": "plain", "cursor_encrypted": False,
    }
    expected: list[dict[str, Any]] = []
    original = graph.update_sync_point_if

    async def recording(key: str, want: dict[str, Any], data: dict, collection: str) -> bool:
        expected.append({"want": dict(want), "fields": sorted(data)})
        return await original(key, want, data, collection)

    graph.update_sync_point_if = recording  # type: ignore[method-assign]
    report = await rekey(_graph(graph), ring, apply=True)
    assert (report.total, report.moved) == (1, 1)
    assert expected == [{"want": {"deltaLink": stale}, "fields": ["deltaLink"]}]
    assert graph.points["org-a/k"]["pageToken"] == current
    assert ring.decrypt_detailed(graph.points["org-a/k"]["deltaLink"]) == ("stale-link", True)
    assert sealed_fields({"a": 1, "a_encrypted": True, "b_encrypted": True, "c": 2, "c_encrypted": "yes"}) == {"a": 1}


async def test_sp5_the_walk_pages_by_key_through_every_org(keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    old, new = keys
    graph = ContractGraph()
    _ = await _connector_points(graph, ORG_A, 5)
    _ = await _connector_points(graph, ORG_B, 1)
    graph.orgs.append({"_key": ORG_A, "isExternal": False})
    ring = _switch(monkeypatch, new, old)
    report = await rekey(_graph(graph), ring, apply=False, page=2)
    assert report.total == 6
    a_pages = [after for org, after in graph.pages if org == ORG_A]
    assert len(a_pages) == 3 and a_pages[0] is None
    assert [org for org, _ in graph.pages] == [ORG_A, ORG_A, ORG_A, ORG_B]
    assert await sync_point_rekey.all_org_ids(_graph(graph)) == [ORG_A, ORG_B]


async def test_sp6_main_prints_counts_only_and_a_failure_by_our_message_or_type(
    keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = keys
    graph = ContractGraph()
    _ = await _connector_points(graph, ORG_A, 2)
    ring = _switch(monkeypatch, new, old)

    async def build(_log: logging.Logger) -> tuple[IGraphDBProvider, EncryptionService]:
        return _graph(graph), ring

    assert await main(["--apply"], build) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"step": "sync-points", "total": 2, "current": 2, "previous": 0, "moved": 2, "unreadable": 0, "draining": 0}
    assert "graph.example" not in out and old not in out and new not in out

    async def misconfigured(_log: logging.Logger) -> tuple[IGraphDBProvider, EncryptionService]:
        raise SecretKeyRingError("SECRET_KEY_PREVIOUS is the same as SECRET_KEY: a rotation needs a new key")

    async def leaky(_log: logging.Logger) -> tuple[IGraphDBProvider, EncryptionService]:
        raise RuntimeError("bolt://neo4j:hunter2@host")

    assert await main([], misconfigured) == 1
    assert json.loads(capsys.readouterr().out)["error"].startswith("SECRET_KEY_PREVIOUS is the same")
    assert await main([], leaky) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"step": "sync-points", "error": "RuntimeError"}
    assert "hunter2" not in out
    assert sync_point_rekey.parse_apply(["--apply"]) is True and sync_point_rekey.parse_apply([]) is False
    with pytest.raises(SystemExit):
        _ = sync_point_rekey.parse_apply(["--retire"])


_BUDGETS = Path(__file__).resolve().parents[6] / "frontend" / "tests" / "e2e" / "perf" / "volume-budgets.json"


async def test_sp7_volume_pages_and_writes_stay_within_the_pre_registered_budget(
    keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """QAFINAL-FU-VOL ``sync-point-rekey``: an org's points are read in pages, never one read per point, and only a
    stale point is written, once; a dry run writes nothing."""
    budget = json.loads(_BUDGETS.read_text())["stores"]["sync-point-rekey"]
    assert (budget["pageReadsPerOrg"], budget["writesPerStalePoint"]) == ("ceil(points / 500) + 1", 1)
    old, new = keys
    orgs, per_org = [f"org-{i}" for i in range(3)], 1_200
    graph = ContractGraph()
    graph.orgs = [{"_key": org, "isExternal": False} for org in orgs]
    stale_cipher = _cipher_of(old)
    ring = _switch(monkeypatch, new, old)
    stale = 0
    for org in orgs:
        for i in range(per_org):
            key = f"{org}/p{i:05d}"
            link = f"https://graph.example/{org}/{i}"
            is_stale = i % 2 == 0
            stale += is_stale
            sealed = stale_cipher.encrypt(link) if is_stale else ring.encrypt(link)
            graph.points[key] = {"orgId": org, "syncPointKey": key, "deltaLink": sealed, "deltaLink_encrypted": True}
    writes: list[str] = []
    original = graph.update_sync_point_if

    async def counting(key: str, want: dict[str, Any], data: dict, collection: str) -> bool:
        writes.append(key)
        return await original(key, want, data, collection)

    graph.update_sync_point_if = counting  # type: ignore[method-assign]
    max_pages = math.ceil(per_org / sync_point_rekey.PAGE) + 1

    dry = await rekey(_graph(graph), ring, apply=False)
    assert (dry.total, dry.previous, dry.current, dry.moved) == (len(orgs) * per_org, stale, len(orgs) * per_org - stale, 0)
    assert writes == []
    for org in orgs:
        assert sum(1 for o, _ in graph.pages if o == org) <= max_pages

    graph.pages.clear()
    applied = await rekey(_graph(graph), ring, apply=True)
    assert (applied.moved, applied.previous) == (stale, 0)
    assert len(writes) == stale * budget["writesPerStalePoint"] and len(set(writes)) == stale
    for org in orgs:
        assert sum(1 for o, _ in graph.pages if o == org) <= max_pages
