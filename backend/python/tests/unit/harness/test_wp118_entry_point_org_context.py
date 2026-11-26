"""WP-118: the Python harness's sweeps and shared background work bind per row, never from the task that started them.

Main #4703: enumeration runs under ``system``, each row's work under ``for_org`` of that row's own org; work shared
by every caller (a reload, a pull) starts detached, so the first caller's org never becomes everyone's."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.control_plane.runtime.live_layer import ReloadingLiveLayerCache
from app.control_plane.testing.memory import InMemoryLiveLayerSource
from app.core import org_context
from app.harness.credentials.binding_writer import DueBinding
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.testing.clock import ManualClock


def _seen() -> tuple[str | None, str] | None:
    ctx = org_context._ctx.get()  # pyright: ignore[reportPrivateUsage]
    return None if ctx is None else (ctx.org_id, ctx.source)


def _user(org: str) -> org_context.OrgContext:
    return org_context.OrgContext(org, "u1", "user", "jwt", "-")


async def test_a_live_layer_reload_acts_for_the_org_it_reloads_not_its_first_reader() -> None:
    seen: list[tuple[str | None, str] | None] = []

    class _Source(InMemoryLiveLayerSource):
        async def load(self, org_id: str, since: int | None) -> Any:  # noqa: ANN401
            seen.append(_seen())
            return await super().load(org_id, since)

    cache = ReloadingLiveLayerCache(_Source(), ManualClock())
    with org_context.bind(_user("org-a")):
        _ = await cache.current("org-a")
    assert seen == [("org-a", "job:live-layer-reload")]


@dataclass
class _Writer:
    rows: list[DueBinding]
    scans: list[tuple[str | None, str] | None] = field(default_factory=list)

    async def due(self, before: int, limit: int, holder: str | None) -> list[DueBinding]:
        self.scans.append(_seen())
        return self.rows


@dataclass
class _Documents:
    seen: dict[str, tuple[str | None, str] | None] = field(default_factory=dict)

    async def document(self, org_id: str, binding: str) -> None:
        self.seen[binding] = _seen()


def _due(binding: str, org: str) -> DueBinding:
    return DueBinding(binding, org, "inst", "owner", "oauth", 1, None, None)


async def test_the_token_refresher_scans_across_orgs_and_refreshes_each_row_as_its_org() -> None:
    from app.connectors.core.base.token_service.token_refresher import TokenRefresher

    writer, documents = _Writer([_due("b1", "org-a"), _due("b2", "org-b")]), _Documents()
    refresher = TokenRefresher(writer, documents, [], ManualClock())  # type: ignore[arg-type]
    _ = await refresher.run_once()
    assert writer.scans == [(None, "system:token-refresh")]
    assert documents.seen == {"b1": ("org-a", "job:token-refresh"), "b2": ("org-b", "job:token-refresh")}


async def test_a_shared_image_pull_runs_detached_from_the_request_that_asked_first() -> None:
    seen: list[tuple[str | None, str] | None] = []
    provider = DockerClientProvider.__new__(DockerClientProvider)
    provider._pulls = {}  # pyright: ignore[reportPrivateUsage]

    async def pull(image: str) -> None:
        seen.append(_seen())

    provider._pull = pull  # type: ignore[method-assign]
    with org_context.bind(_user("org-a")):
        await provider.pull_image("engine:1")
    assert seen == [None]
    await asyncio.sleep(0)
