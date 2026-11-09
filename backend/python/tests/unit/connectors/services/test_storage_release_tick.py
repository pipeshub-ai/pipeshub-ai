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


@pytest.fixture
def released(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """The connectors whose storage the tick released, with the org context bound at the time."""
    seen: list[tuple[str, object]] = []

    async def release(*_a: object, connector_id: str, **_kw: object) -> bool:
        seen.append((connector_id, peek()))
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


async def test_a_gone_app_is_released_in_the_intents_org(released: list) -> None:
    assert await tick({}, "z") == "released"
    assert [c for c, _ in released] == ["z"] and bound_to(released[0][1], ORG_A)
    assert peek() is None

