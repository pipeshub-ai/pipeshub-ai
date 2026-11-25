"""The storage-release loop runs detached, with no org bound: each intent's app is read in the intent's own org."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.services import storage_release
from app.connectors.services.storage_release import (
    GRACE_MS,
    StorageReleaseReconciler,
    record_pending_storage_release,
)
from app.core.org_context import current_or_system, peek

from .test_storage_release_finisher import Config

NOW = 10 * 24 * 60 * 60 * 1000
ORG_A, ORG_B = "org-a", "org-b"


class PgLikeApps:
    """``get_document`` on ``apps`` as the PostgreSQL provider answers it: nothing bound raises, a system context reads any org's copy
    of the id (``graph.system_app``), an org context reads that org's partition only."""

    def __init__(self, apps: dict[tuple[str, str], dict]) -> None:
        self.apps = apps

    async def get_document(self, key: str, collection: str, **_kw: object) -> dict | None:
        ctx = current_or_system()
        if ctx.kind == "system":
            return next((doc for (_, k), doc in self.apps.items() if k == key), None)
        return self.apps.get((ctx.org_id, key))


# What each release was given to delete the connector's credentials with; None keeps them.
credentials_of: list[object] = []


@pytest.fixture
def released(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """The connectors whose storage the tick released, with the org context bound at the time."""
    seen: list[tuple[str, object]] = []
    credentials_of.clear()

    async def release(*_a: object, connector_id: str, org_config_service: object = None, **_kw: object) -> bool:
        seen.append((connector_id, peek()))
        credentials_of.append(org_config_service)
        return True

    monkeypatch.setattr(storage_release, "release_connector_storage", release)
    monkeypatch.setattr(storage_release, "StorageCleanupHelper", lambda *_a: SimpleNamespace(close=AsyncMock()))
    return seen


async def tick(apps: dict[tuple[str, str], dict], connector_id: str) -> str:
    config = Config()
    await record_pending_storage_release(config, org_id=ORG_A, connector_id=connector_id, now_ms=NOW - GRACE_MS - 1)
    lock = SimpleNamespace(try_acquire=AsyncMock(return_value=True))
    reconciler = StorageReleaseReconciler(logger=MagicMock(), graph_provider=PgLikeApps(apps), config_service=config, lock=lock, now_ms=lambda: NOW)
    return await reconciler.tick()


def bound_to(ctx: object, org: str) -> bool:
    return (ctx.kind, ctx.org_id, ctx.source) == ("job", org, "job:storage_release")


async def test_an_app_that_still_exists_in_the_intents_org_is_not_released(released: list) -> None:
    assert await tick({(ORG_A, "x"): {"_key": "x", "type": "DRIVE"}}, "x") == "idle"
    assert released == []


async def test_an_id_only_another_org_holds_is_gone_for_this_org_and_released_in_it(released: list) -> None:
    """A system read would answer org B's copy and keep org A's storage forever."""
    assert await tick({(ORG_B, "y"): {"_key": "y", "type": "DRIVE"}}, "y") == "released"
    assert [c for c, _ in released] == ["y"] and bound_to(released[0][1], ORG_A)
    assert credentials_of == [None]  # keyed by the id alone: they are org B's


async def test_a_gone_app_is_released_in_the_intents_org(released: list) -> None:
    assert await tick({}, "z") == "released"
    assert [c for c, _ in released] == ["z"] and bound_to(released[0][1], ORG_A)
    assert credentials_of[0] is not None
    assert peek() is None


class AnyOrgApps:
    """``get_document`` as a store that ignores the bound org answers it: the app, whoever asks."""

    def __init__(self, apps: dict[str, object]) -> None:
        self.apps = apps

    async def get_document(self, key: str, collection: str, **_kw: object) -> object:
        app = self.apps.get(key)
        if isinstance(app, Exception):
            raise app
        return app


async def tick_all(apps: dict[str, object], intents: dict[str, str]) -> tuple[str, Config, AsyncMock]:
    config, finish = Config(), AsyncMock(return_value={"success": True})
    for n, (connector_id, org) in enumerate(intents.items()):
        await record_pending_storage_release(config, org_id=org, connector_id=connector_id, now_ms=NOW - GRACE_MS - 100 + n)
    lock = SimpleNamespace(try_acquire=AsyncMock(return_value=True))
    outcome = await StorageReleaseReconciler(
        logger=MagicMock(), graph_provider=AnyOrgApps(apps), config_service=config, lock=lock, now_ms=lambda: NOW,
        finish_kb_delete=finish, finish_connector_delete=finish,
    ).tick()
    return outcome, config, finish


@pytest.mark.parametrize("status", ["DELETING", "ACTIVE"])
async def test_an_intent_never_finishes_or_settles_another_orgs_connector(released: list, status: str) -> None:
    app = {"_key": "x", "type": "DRIVE", "status": status, "orgId": ORG_B}
    outcome, config, finish = await tick_all({"x": app}, {"x": ORG_A})
    assert outcome == "idle" and released == [] and len(config.kv) == 1
    finish.assert_not_awaited()


async def test_a_connector_that_cannot_be_read_does_not_hold_up_another_orgs_release(released: list) -> None:
    outcome, config, _ = await tick_all({"bad": RuntimeError("unreadable")}, {"bad": ORG_A, "gone": ORG_B})
    assert outcome == "released" and [c for c, _ in released] == ["gone"]
    assert f"/services/storageRelease/pending/{ORG_A}/bad" in config.kv


async def test_a_store_that_reads_nothing_fails_the_tick_so_the_loop_backs_off(released: list) -> None:
    with pytest.raises(RuntimeError):
        await tick_all({"x": RuntimeError("down")}, {"x": ORG_A})
    assert released == []


async def test_a_stale_intent_on_another_orgs_connector_is_dropped_so_its_own_org_can_delete_it() -> None:
    config = Config()
    await record_pending_storage_release(config, org_id=ORG_A, connector_id="x", now_ms=NOW - storage_release.STALE_MS - 1)
    lock = SimpleNamespace(try_acquire=AsyncMock(return_value=True))
    app = {"_key": "x", "type": "DRIVE", "status": "DELETING", "orgId": ORG_B}
    finish = AsyncMock()
    outcome = await StorageReleaseReconciler(
        logger=MagicMock(), graph_provider=AnyOrgApps({"x": app}), config_service=config, lock=lock, now_ms=lambda: NOW,
        finish_connector_delete=finish,
    ).tick()
    assert outcome == "dropped" and config.kv == {}
    finish.assert_not_awaited()
    await record_pending_storage_release(config, org_id=ORG_B, connector_id="x", now_ms=NOW)


@pytest.mark.parametrize("org_of_app", [ORG_A, ORG_B])
async def test_an_intent_that_cannot_be_cleared_does_not_hold_up_the_later_ones(released: list, org_of_app: str) -> None:
    class KeepsX(Config):
        async def delete_config(self, key: str) -> bool:
            return False if key.endswith("/x") else await super().delete_config(key)

    config, old = KeepsX(), NOW - storage_release.STALE_MS - 2
    await record_pending_storage_release(config, org_id=ORG_A, connector_id="x", now_ms=old)
    await record_pending_storage_release(config, org_id=ORG_A, connector_id="gone", now_ms=old + 1)
    lock = SimpleNamespace(try_acquire=AsyncMock(return_value=True))
    apps = AnyOrgApps({"x": {"_key": "x", "type": "DRIVE", "status": "ACTIVE", "orgId": org_of_app}})
    outcome = await StorageReleaseReconciler(
        logger=MagicMock(), graph_provider=apps, config_service=config, lock=lock, now_ms=lambda: NOW,
    ).tick()
    assert outcome == "released" and [c for c, _ in released] == ["gone"]
