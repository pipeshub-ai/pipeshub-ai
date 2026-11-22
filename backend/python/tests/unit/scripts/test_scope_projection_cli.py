"""The scope back-fill command resolves the shared scope generation before it writes a stamp."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.scripts import scope_projection
from app.services.permissions import scope_fields


async def test_run_resolves_the_scope_generation_before_the_first_write(monkeypatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")
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


def _sweep_env(monkeypatch, rows) -> list:
    container = MagicMock()
    container.graph_provider = AsyncMock(return_value=MagicMock(disconnect=AsyncMock()))
    monkeypatch.setattr("app.containers.indexing.IndexingAppContainer.init", lambda name: container)
    monkeypatch.setattr("app.modules.indexing.vector_membership_backfill._resolve_indexing_pipeline", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("app.services.permissions.wiring.register_scope_generation", AsyncMock())
    calls: list = []

    class Sweep:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_once(self, org: str, connectors: object) -> list:
            calls.append((org, connectors))
            return rows

    monkeypatch.setattr("app.services.vector_db.scope_sweep.ScopeSweep", Sweep)
    return calls


async def test_sweep_refuses_while_the_scope_fields_are_off(monkeypatch) -> None:
    monkeypatch.delenv("ACL_SCOPE_FIELDS", raising=False)
    monkeypatch.delenv("ACL_FILTER_MODE", raising=False)
    calls = _sweep_env(monkeypatch, [])
    assert await scope_projection._main(["sweep", "--org", "o"]) == 1 and not calls


async def test_sweep_exits_non_zero_only_when_a_connector_failed(monkeypatch) -> None:
    from app.services.vector_db.scope_sweep import SweepRow

    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")
    calls = _sweep_env(monkeypatch, [SweepRow("a", "done"), SweepRow("b", "idle")])
    assert await scope_projection._main(["sweep", "--org", "o", "--connector", "a"]) == 0 and calls == [("o", ["a"])]
    _sweep_env(monkeypatch, [SweepRow("a", "done"), SweepRow("b", "failed", "boom")])
    assert await scope_projection._main(["sweep", "--org", "o"]) == 1


async def test_view_prints_the_lines_and_exits_non_zero_only_when_something_needs_attention(monkeypatch, capsys) -> None:
    from app.services.vector_db.scope_view import ConnectorView, OrgView

    container = MagicMock()
    container.graph_provider = AsyncMock(return_value=MagicMock(disconnect=AsyncMock()))
    monkeypatch.setattr("app.containers.indexing.IndexingAppContainer.init", lambda name: container)
    monkeypatch.setattr("app.modules.indexing.vector_membership_backfill._resolve_indexing_pipeline", AsyncMock(return_value=None))
    seen = {}

    async def org_view(graph: object, org: str, vdb: object, **kw: object) -> OrgView:
        seen.update(vdb=vdb, **kw)
        return OrgView(org, [ConnectorView("a", current=False, attention=["pending_roots: 1 root(s)"])] if seen.get("bad") else [ConnectorView("a", True)])

    monkeypatch.setattr("app.services.vector_db.scope_view.org_view", org_view)
    monkeypatch.setattr("app.services.permissions.generation.current_generation", AsyncMock(return_value=0))
    monkeypatch.setitem(scope_fields._resolved, "generation", None)      # the view resolves the generation: put it back for the tests after this one
    assert await scope_projection._main(["view", "--org", "o", "--connector", "a"]) == 1      # no vector store: said, and needs attention
    assert "vector_store_not_configured" in capsys.readouterr().out and seen["vdb"] is None and seen["connectors"] == ["a"]
    seen["bad"] = True
    assert await scope_projection._main(["view", "--org", "o"]) == 1 and "pending_roots" in capsys.readouterr().out
    monkeypatch.setattr("app.modules.indexing.vector_membership_backfill._resolve_indexing_pipeline", AsyncMock(return_value=MagicMock()))
    seen["bad"] = False
    assert await scope_projection._main(["view", "--org", "o"]) == 0


async def test_view_judges_by_the_deployments_generation_and_writes_none(monkeypatch) -> None:
    from app.services.permissions.scope_fields import (
        current_scope_generation,
        set_resolved_generation,
    )
    from app.services.vector_db.scope_view import OrgView

    config = MagicMock(list_keys_in_directory=AsyncMock(return_value=["/services/scopeFields/generation/1"]))
    container = MagicMock(config_service=lambda: config)
    container.graph_provider = AsyncMock(return_value=MagicMock(disconnect=AsyncMock()))
    monkeypatch.setattr("app.containers.indexing.IndexingAppContainer.init", lambda name: container)
    monkeypatch.setattr("app.modules.indexing.vector_membership_backfill._resolve_indexing_pipeline", AsyncMock(return_value=MagicMock()))
    monkeypatch.delenv("ACL_SCOPE_FIELDS", raising=False)      # this process's flag is off; the deployment's is on
    seen: list[int] = []

    async def org_view(*args: object, **kw: object) -> OrgView:
        seen.append(current_scope_generation())
        return OrgView("o", [])

    monkeypatch.setattr("app.services.vector_db.scope_view.org_view", org_view)
    try:
        assert await scope_projection._main(["view", "--org", "o"]) == 0
        assert seen == [1]
        config.list_keys_in_directory.side_effect = ConnectionError("down")      # unreadable: said, not judged against 0
        assert await scope_projection._main(["view", "--org", "o"]) == 1 and seen == [1]
    finally:
        set_resolved_generation(None)
    config.set_config.assert_not_called()
    config.create_config_if_absent.assert_not_called()


async def test_sweep_with_an_interval_goes_on_after_a_listing_that_fails(monkeypatch) -> None:
    import asyncio

    from app.services.vector_db.scope_sweep import SweepRow

    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")
    _sweep_env(monkeypatch, [])
    runs: list[int] = []

    class Sweep:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_once(self, org: str, connectors: object) -> list:
            runs.append(1)
            if len(runs) == 1:
                raise RuntimeError("connection reset")
            return [SweepRow("a", "done")]

    class Stop(BaseException):
        pass

    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            raise Stop

    monkeypatch.setattr("app.services.vector_db.scope_sweep.ScopeSweep", Sweep)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    with contextlib.suppress(Stop):
        await scope_projection._main(["sweep", "--org", "o", "--interval", "5"])
    assert len(runs) == 2 and waits == [5, 5]      # the first round's error did not end the loop


@pytest.mark.parametrize("command", [["run", "--connector", "c"], ["sweep"], ["rewrite", "--connector", "c"]])
async def test_a_writing_command_with_the_scope_fields_off_records_nothing(monkeypatch, command) -> None:
    """Registering from a process with the flag off would record the deployment as switched off; the next start with it on would then move
    the generation and make every stamp already written stale. So the command refuses before it registers anything."""
    container = MagicMock()
    container.graph_provider = AsyncMock(return_value=MagicMock(disconnect=AsyncMock()))
    monkeypatch.setattr("app.containers.indexing.IndexingAppContainer.init", lambda name: container)
    monkeypatch.setattr("app.modules.indexing.vector_membership_backfill._resolve_indexing_pipeline", AsyncMock(return_value=MagicMock()))
    register = AsyncMock()
    monkeypatch.setattr("app.services.permissions.wiring.register_scope_generation", register)
    # after the patches: importing the container loads a local .env, which could switch the fields back on
    monkeypatch.delenv("ACL_SCOPE_FIELDS", raising=False)
    monkeypatch.delenv("ACL_FILTER_MODE", raising=False)
    assert await scope_projection._main([command[0], "--org", "o", *command[1:]]) == 1
    register.assert_not_awaited()


def _rewrite_env(monkeypatch, outcomes: dict) -> list:
    """``outcomes``: connector -> a RewriteResult state, or an exception to raise."""
    from app.services.vector_db.scope_rewrite import RewriteResult

    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")
    container = MagicMock()
    container.graph_provider = AsyncMock(return_value=MagicMock(disconnect=AsyncMock()))
    monkeypatch.setattr("app.containers.indexing.IndexingAppContainer.init", lambda name: container)
    monkeypatch.setattr("app.modules.indexing.vector_membership_backfill._resolve_indexing_pipeline", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr("app.services.permissions.wiring.register_scope_generation", AsyncMock())
    ran: list = []

    class Job:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def run_connector(self, org: str, connector: str) -> RewriteResult:
            ran.append(connector)
            out = outcomes[connector]
            if isinstance(out, Exception):
                raise out
            return RewriteResult(org, connector, state=out, roots=["r"])

    monkeypatch.setattr("app.services.vector_db.scope_rewrite.ScopeRewriteJob", Job)
    return ran


async def test_rewrite_runs_every_connector_and_fails_only_for_an_error_or_a_connector_that_needs_the_full_pass(monkeypatch, capsys) -> None:
    ran = _rewrite_env(monkeypatch, {"a": "done", "b": RuntimeError("boom"), "c": "busy", "d": "full_pass"})
    assert await scope_projection._main(["rewrite", "--org", "o", "--connector", "a", "--connector", "c"]) == 0 and ran == ["a", "c"]
    assert "a  done  roots=1" in capsys.readouterr().out
    ran.clear()
    assert await scope_projection._main(["rewrite", "--org", "o", "--connector", "b", "--connector", "a"]) == 1
    assert ran == ["b", "a"] and "error RuntimeError: boom" in capsys.readouterr().out      # the failure did not hold the next connector
    assert await scope_projection._main(["rewrite", "--org", "o", "--connector", "d"]) == 1


async def test_status_shows_the_pending_roots_and_the_last_sweep_error(monkeypatch, capsys) -> None:
    from app.services.permissions.pending import with_roots
    from app.services.permissions.structure_generation import PENDING_KEY
    from app.services.vector_db.scope_sweep import SWEEP_KEY

    graph = MagicMock(disconnect=AsyncMock())
    container = MagicMock()
    container.graph_provider = AsyncMock(return_value=graph)
    monkeypatch.setattr("app.containers.indexing.IndexingAppContainer.init", lambda name: container)
    app = {PENDING_KEY: with_roots(None, ["r1", "r2"], 3), SWEEP_KEY: '{"n":2,"e":"boom","t":5}'}
    graph.get_document = AsyncMock(return_value=app)
    monkeypatch.setattr("app.services.vector_db.scope_backfill.status", AsyncMock(return_value=None))
    assert await scope_projection._main(["status", "--org", "o", "--connector", "c"]) == 0
    out = capsys.readouterr().out
    assert "not started; 2 pending root(s), last error (2 in a row): boom" in out
    graph.get_document = AsyncMock(return_value={})
    await scope_projection._main(["status", "--org", "o", "--connector", "c"])
    assert "0 pending root(s), no error" in capsys.readouterr().out
