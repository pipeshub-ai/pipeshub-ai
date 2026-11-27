"""``StoredEngineCeilings`` (``EngineCeilingSource`` over the org's live layer) keeps the port's contract: a ceiling
saved to the org layer Node serves reads back, a change is seen at the next read, and an unreadable layer is refused,
never read as "no ceiling"."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.control_plane.contracts.generated.live_layer import (
    LiveLayer as LiveLayerDocument,
)
from app.control_plane.runtime.live_layer import (
    LIVE_TTL_S,
    LiveLayerUnavailable,
    ReloadingLiveLayerCache,
)
from app.harness.run_policy.stored import StoredEngineCeilings
from harness_core.testing import ManualClock
from harness_core.testing.contracts.engine_profiles import (
    CeilingEnv,
    EngineCeilingSourceContract,
)


class _StoredCeilings:
    """``CeilingEnv`` over the live layer: an admin's save reaches the org layer Node serves and names the org in a
    change event, as every control-plane write does."""

    def __init__(self) -> None:
        self._clock = ManualClock()
        self._ceilings: dict[str, Any] = {}
        self._revisions: dict[str, int] = {}
        self._up = True
        self._live = ReloadingLiveLayerCache(self, self._clock)

    @property
    def source(self) -> StoredEngineCeilings:
        return StoredEngineCeilings(self._live)

    async def load(self, org_id: str, since: int | None) -> LiveLayerDocument | None:
        if not self._up:
            raise LiveLayerUnavailable("the internal live route is unreachable")
        revision = self._revisions.get(org_id, 0)
        if since == revision:
            return None
        layer: dict[str, Any] = {"schemaVersion": 1, "scope": {"kind": "live"}}
        if org_id in self._ceilings:
            layer["engineProfile"] = self._ceilings[org_id]
        return LiveLayerDocument.model_validate(
            {
                "schemaVersion": 1,
                "orgId": org_id,
                "revision": revision,
                "layers": [{"layer": layer}],
                "revokedBindings": [],
            }
        )

    async def store(self, org_id: str, ceiling: Any) -> None:  # noqa: ANN401
        if ceiling is None:
            _ = self._ceilings.pop(org_id, None)
        else:
            self._ceilings[org_id] = ceiling
        revision = self._revisions.get(org_id, 0) + 1
        self._revisions[org_id] = revision
        self._live.on_change(
            SimpleNamespace(org_id=org_id, revision=revision, previous=revision - 1)
        )  # type: ignore[arg-type]

    async def store_raw(self, org_id: str, raw: Any) -> None:  # noqa: ANN401
        await self.store(org_id, raw)

    def break_reads(self) -> None:
        """The live route is gone for longer than a held layer may be answered from."""
        self._up = False
        _ = self._clock.advance(LIVE_TTL_S)


class TestStoredEngineCeilingsContract(EngineCeilingSourceContract):
    @pytest.fixture
    def ceiling_env(self) -> CeilingEnv:
        return _StoredCeilings()
