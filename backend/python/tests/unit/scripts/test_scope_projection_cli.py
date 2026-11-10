"""The scope back-fill command resolves the shared scope generation before it writes a stamp."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.scripts import scope_projection


async def test_run_resolves_the_scope_generation_before_the_first_write(monkeypatch) -> None:
    order: list[str] = []
    container = MagicMock()
    container.graph_provider = AsyncMock(return_value=MagicMock(disconnect=AsyncMock()))
    monkeypatch.setattr("app.containers.indexing.IndexingAppContainer.init", lambda name: container)
    monkeypatch.setattr(
        "app.modules.indexing.vector_membership_backfill._resolve_indexing_pipeline", AsyncMock(return_value=MagicMock())
    )

    async def register(config: object, logger: object) -> None:
        order.append("register")

    class Job:
        def __init__(self, *args: object) -> None:
            pass

        async def run_connector(self, *args: object, **kwargs: object) -> MagicMock:
            order.append("write")
            return MagicMock(state="done", stale=0, line=lambda: "done")

    monkeypatch.setattr("app.services.permissions.wiring.register_scope_generation", register)
    monkeypatch.setattr("app.services.vector_db.scope_backfill.ScopeBackfill", Job)
    assert await scope_projection._main(["run", "--org", "o", "--connector", "c"]) == 0
    assert order == ["register", "write"]


async def test_run_exits_non_zero_when_the_scope_generation_is_unavailable(monkeypatch) -> None:
    from app.services.permissions.scope_fields import (
        UNAVAILABLE_GENERATION,
        set_resolved_generation,
    )

    graph = MagicMock(disconnect=AsyncMock())
    container = MagicMock()
    container.graph_provider = AsyncMock(return_value=graph)
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")
    monkeypatch.setattr("app.containers.indexing.IndexingAppContainer.init", lambda name: container)
    monkeypatch.setattr(
        "app.modules.indexing.vector_membership_backfill._resolve_indexing_pipeline", AsyncMock(return_value=MagicMock())
    )

    async def register(config: object, logger: object) -> None:  # what it does when the shared state cannot be read
        set_resolved_generation(UNAVAILABLE_GENERATION)

    monkeypatch.setattr("app.services.permissions.wiring.register_scope_generation", register)
    try:
        assert await scope_projection._main(["run", "--org", "o", "--connector", "c"]) == 1
    finally:
        set_resolved_generation(None)
    graph.permission_stamp_graph.assert_not_called()
