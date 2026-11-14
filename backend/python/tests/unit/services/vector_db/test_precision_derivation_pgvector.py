"""The index's precision on a store with one space per organisation: derived per org, under a statement timeout.

The real pgvector provider (real filter compiler and counting SQL) runs over a fake database runtime. The fake
records the SQL, the org bound at the time of the call and the time budget the caller put on it.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.core import org_context
from app.core.db.errors import DbTimeout
from app.services.messaging.precision_runtime import build_precision_verdict
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore
from app.services.vector_db.pgvector.catalog import CollectionRow
from app.services.vector_db.pgvector.pgvector import PgVectorService
from app.services.vector_db.precision_guard import (
    PrecisionDerivationTimeout,
    PrecisionGuard,
    PrecisionUnreadable,
)

log = logging.getLogger("derive-test")
ORG = "a" * 24
ROW = CollectionRow(7, "records", True, True, "v", "ready")


class _Runtime:
    """Stands in for the database runtime: answers each counting statement, records how it was run."""

    def __init__(self, *, tagged: dict[str, int] | None = None, points: int = 0) -> None:
        self.tagged = tagged or {}
        self.points = points
        self.calls: list[dict] = []
        self.fail: Exception | None = None

    async def run(self, fn, **opts):  # noqa: ANN202
        bound = org_context.current().org_id
        runtime = self

        class Tx:
            async def fetchval(self, sql, *args):  # noqa: ANN202
                runtime.calls.append({"sql": sql, "args": args, "org": bound, "timeout_ms": opts.get("timeout_ms")})
                if runtime.fail:
                    raise runtime.fail
                for tag, n in runtime.tagged.items():
                    if tag in args or any(tag in a for a in args if isinstance(a, list)):
                        return n
                if "count_filtered" in sql:
                    return 0
                return runtime.points

        return await fn(Tx())


def _graph(orgs: list[str]) -> MagicMock:
    graph = MagicMock()
    graph.get_all_orgs = AsyncMock(return_value=[{"_key": o} for o in orgs])
    return graph


def _service(runtime: _Runtime) -> PgVectorService:
    svc = PgVectorService.__new__(PgVectorService)
    svc.config_service = MagicMock()
    svc._db = runtime
    catalog = MagicMock()
    catalog.collection = AsyncMock(return_value=ROW)
    catalog.require_collection = AsyncMock(return_value=ROW)
    catalog.active_space = AsyncMock(return_value=SimpleNamespace(space_id=1, dim=4))
    svc._catalog = catalog
    return svc


def _guard(runtime: _Runtime):  # noqa: ANN202
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    pins = CollectionPinStore(config, log)
    return PrecisionGuard(pins, _service(runtime), log), pins, config


@pytest.mark.asyncio
async def test_the_counts_run_for_the_org_under_the_statement_timeout() -> None:
    runtime = _Runtime(points=5)
    guard, _, _ = _guard(runtime)
    standing = await guard.derive("records", ORG)
    assert (standing.precision, standing.source) == (FP32, "legacy")  # five text points and no tag: legacy
    assert runtime.calls, "the counts must reach the database"
    assert {c["org"] for c in runtime.calls} == {ORG}
    assert {c["timeout_ms"] for c in runtime.calls} == {3000}
    tagged = [c for c in runtime.calls if "count_filtered" in c["sql"]]
    assert len(tagged) == 4 and all("pt.payload #>>" in c["sql"] for c in tagged)  # the real compiled positive predicate


@pytest.mark.asyncio
async def test_an_int8_claimant_is_refused_when_the_probe_times_out() -> None:
    runtime = _Runtime()
    runtime.fail = DbTimeout("statement timeout")
    guard, pins, _ = _guard(runtime)
    with pytest.raises(PrecisionDerivationTimeout):
        await guard.verify_batch("records", ORG, INT8, fallback=False, intent=INT8)
    assert await pins.get("records", ORG, fresh=True) is None  # nothing claimed


@pytest.mark.asyncio
async def test_a_probe_that_never_answers_is_a_timeout_too(monkeypatch) -> None:
    monkeypatch.setattr("app.services.vector_db.precision_guard.DERIVE_TIMEOUT_SECONDS", 0.05)
    runtime = _Runtime()

    async def hang(fn, **_o):  # noqa: ANN202
        import asyncio

        await asyncio.sleep(30)

    runtime.run = hang
    guard, pins, _ = _guard(runtime)
    with pytest.raises(PrecisionUnreadable):
        await guard.verify_batch("records", ORG, INT8, fallback=False, intent=INT8)
    assert await pins.get("records", ORG, fresh=True) is None


@pytest.mark.asyncio
async def test_an_fp32_claimant_claims_without_a_probe() -> None:
    runtime = _Runtime(points=9)
    runtime.fail = DbTimeout("a probe would time out")
    guard, pins, _ = _guard(runtime)
    await guard.verify_batch("records", ORG, FP32, fallback=False, intent=FP32)
    assert runtime.calls == []
    assert (await pins.get("records", ORG, fresh=True)).precision == FP32


@pytest.mark.asyncio
async def test_an_fp32_batch_under_a_fallback_is_still_probed_and_never_claims() -> None:
    runtime = _Runtime()
    runtime.fail = DbTimeout("x")
    guard, pins, _ = _guard(runtime)
    with pytest.raises(PrecisionUnreadable):
        await guard.verify_batch("records", ORG, FP32, fallback=True, intent=INT8)
    assert await pins.get("records", ORG, fresh=True) is None


@pytest.mark.asyncio
async def test_at_read_time_a_timeout_is_no_derivation_not_legacy() -> None:
    runtime = _Runtime(points=9)
    runtime.fail = DbTimeout("statement timeout")
    guard, _, _ = _guard(runtime)
    standing = await guard.standing_for_read("records", ORG)
    assert standing.precision is None and standing.source == "unknown"


@pytest.mark.asyncio
async def test_a_store_that_is_not_per_org_is_not_scoped_or_bounded() -> None:
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    db = MagicMock()
    db.get_capabilities.return_value = SimpleNamespace(spaces_per_org=False)
    db.filter_collection = AsyncMock(side_effect=lambda must=None, **_k: dict(must or {}))
    db.count_points = AsyncMock(return_value=0)
    db.get_collection_info = AsyncMock(return_value=SimpleNamespace(exists=True, points_count=0))
    guard = PrecisionGuard(CollectionPinStore(config, log), db, log)
    standing = await guard.derive("records", ORG)
    assert standing.source == "none"
    assert db.count_points.await_count == 4


@pytest.mark.asyncio
async def test_the_verdict_derives_for_the_organisations_of_the_deployment_before_any_message_is_read() -> None:
    runtime = _Runtime(tagged={"int8-w": 3}, points=3)
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    registry = MagicMock()
    registry.list_managed_collections = AsyncMock(
        return_value=[SimpleNamespace(name="records", embedding_model="m")],
    )
    graph = _graph([ORG])
    verdict = build_precision_verdict(config, registry, _service(runtime), graph, log)
    from app.services.messaging.precision_check import ServerState

    verdict._fetch_states = AsyncMock(return_value={"m": ServerState(FP32, FP32, False)})
    await verdict.refresh()
    assert verdict.mismatch  # the index holds int8-tagged points and the server serves fp32; no message was read
    assert {c["org"] for c in runtime.calls} == {ORG}
    graph.get_all_orgs.assert_awaited_with(active=True, raise_on_error=True)
    # the derived pin is written as a restore, under the org's key
    assert (await CollectionPinStore(config, log).get("records", ORG, fresh=True)).precision == INT8


@pytest.mark.asyncio
async def test_an_organisation_listing_that_fails_follows_the_poll_failure_rule() -> None:
    runtime = _Runtime(tagged={"int8-w": 3}, points=3)
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    registry = MagicMock()
    registry.list_managed_collections = AsyncMock(return_value=[SimpleNamespace(name="records", embedding_model="m")])
    graph = _graph([ORG])
    verdict = build_precision_verdict(config, registry, _service(runtime), graph, log)
    from app.services.messaging.precision_check import ServerState

    verdict._fetch_states = AsyncMock(return_value={"m": ServerState(FP32, FP32, False)})
    await verdict.refresh()
    assert verdict.mismatch
    graph.get_all_orgs.side_effect = RuntimeError("graph down")
    await verdict.refresh()
    assert verdict.mismatch  # the last verdict is kept for two periods, as for any failed read


@pytest.mark.asyncio
async def test_a_derivation_timeout_leaves_the_verdict_at_match_and_the_write_guard_in_place() -> None:
    runtime = _Runtime(tagged={"int8-w": 3}, points=3)
    runtime.fail = DbTimeout("statement timeout")
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    registry = MagicMock()
    registry.list_managed_collections = AsyncMock(return_value=[SimpleNamespace(name="records", embedding_model="m")])
    verdict = build_precision_verdict(config, registry, _service(runtime), _graph([ORG]), log)
    from app.services.messaging.precision_check import ServerState

    verdict._fetch_states = AsyncMock(return_value={"m": ServerState(FP32, FP32, False)})
    await verdict.refresh()
    assert not verdict.mismatch
    guard = PrecisionGuard(CollectionPinStore(config, log), _service(runtime), log)
    with pytest.raises(PrecisionUnreadable):
        await guard.verify_batch("records", ORG, INT8, fallback=False, intent=INT8)
