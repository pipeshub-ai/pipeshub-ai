"""Background projection of record property values onto the record, for the records indexing did not do.

Indexing projects a record's values when it indexes it (``record_metadata.projection``). Some records are
never reached that way: a knowledge-base record whose values were pushed (indexing holds none for it), a ticket
changed at its source without a re-index, every record of a scope that was already there when a property
entered its schema, a record completed as a copy of another. This sweep finds them and calls the same
``project_properties``, so there is one writer and one fence; a sweep and an index run, or two sweeps, can never
overwrite newer values with older ones.

Two selections, both of records whose projection is stale (``property_projection_queries``):

- the change seek: records whose platform write time (``updatedAtTimestamp``) is after a watermark, keyset-paged by
  ``(updatedAtTimestamp, id)``. The watermark is the wall-clock start of the last finished lap minus an hour for
  clock skew. It is never a maximum over record stamps: one record stamped years ahead would otherwise hide every
  later change. A record that arrives late with an old source time still has a fresh write time, so it is seen.
- the convergence walk: every stale record of the connector, by id. It runs when the scope's union (names and
  generation) has not been walked yet, once a day as a repair, and in place of the seek while the change index is
  absent or still building (a query hinted at an index that is not online fails).

One lap over one scope runs under a lease (one process at a time) and an epoch: the scope's state document is
written only while the epoch this lap claimed is still the stored one, so a process that lost its lease cannot move
progress. A lap that stops (lease lost, epoch lost, flag off) resumes from the stored cursor; a record the crashed
lap had already written is current by then and is not selected again.

Bounds: a record whose projection fails (values unreadable) is retried by the next walk, at most three walks in a
row, then by the daily repair. A record whose write time does not move when it changes is found by the repair only.

A write time is carried as the store returned it (an integer or a fraction), never converted: a cursor cut to a whole number
re-selects every record of a fractional tie. The statements compare the raw stamp, while the projection stores its whole-number
part, so a record stamped with a fraction is selected again until its stamp moves and is answered "current" without a write.
A full page that moves the cursor nowhere ends the lap ("stalled", backed off); one scope works at most ``SCOPE_MAX_PAGES``
pages and ``SCOPE_BUDGET_SECONDS`` per pass, so one scope cannot hold the others up; it resumes from its cursor.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames, Connectors
from app.core.org_context import current_org_id, for_org, spawn_detached, system
from app.events.processor import convert_record_dict_to_record
from app.models.record_metadata import RecordMetadata
from app.modules.record_metadata.projection import (
    CURRENT,
    NOT_PROVIDED,
    WRITTEN,
    ScopeUnion,
    ScopeUnionCache,
    UnionUnavailable,
    default_union_cache,
    project_properties,
    projection_enabled,
)
from app.modules.record_metadata.schemas import MetadataSchemaRegistry
from app.services.graph_db.conditions import EQ, IS_NULL, Cond
from app.telemetry.modules.record_storage_metrics import (
    record_property_sweep_lap,
    record_property_sweep_row,
)
from app.utils.env_config import env_int, env_seconds
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

PAGE_SIZE: Final = 500
#: Clock skew between the writers of ``updatedAtTimestamp``: the watermark trails the lap's start by this.
SKEW_MS: Final = 3_600_000
REPAIR_PERIOD_MS: Final = 24 * 3_600_000
SEEK_INTERVAL_SECONDS: Final = 900.0
LEASE_TTL_SECONDS: Final = 120.0
LEASE_RENEW_SECONDS: Final = 40.0
DEFAULT_MAX_SCOPES: Final = 2
#: Share of one core the sweep may use: after a batch it sleeps for (1 / duty - 1) times the batch's time.
DUTY: Final = 0.10
#: Walks in a row that may end with failed records before the scope counts as walked (the repair retries).
MAX_WALK_ATTEMPTS: Final = 3
#: Stored files read at once.
READ_CONCURRENCY: Final = 8
#: A scope's work in one pass is bounded, so one scope that cannot finish cannot hold the pass (it resumes from its cursor next pass).
SCOPE_MAX_PAGES: Final = 200
SCOPE_BUDGET_SECONDS: Final = 600.0

_APPS = CollectionNames.APPS.value
_STATE_FIELD: Final = "propSweep"
_EPOCH_FIELD: Final = "propSweepEpoch"

SEEK: Final = "seek"
WALK: Final = "walk"
WALK_NO_INDEX: Final = "walk_index_unavailable"

logger = logging.getLogger(__name__)


# ---- state ---------------------------------------------------------------------------------------


@dataclass
class SweepState:
    """Where a scope's sweep stands. ``lap`` is the kind in progress (None between laps)."""

    watermark: int | None = None
    covered_gen: int | None = None
    covered_hash: str | None = None
    repair_at: int | None = None
    lap_end: int | None = None
    lap: str | None = None
    lap_start: int | None = None
    cursor_ts: int | float | None = None     # the stored write time as the store returned it: never converted
    cursor_id: str | None = None
    attempts: int = 0
    failures: int = 0

    def dump(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def load(cls, raw: object) -> "SweepState":
        """The stored state; an absent or unreadable one is a scope never swept, which is always safe."""
        if not isinstance(raw, str) or not raw:
            return cls()
        try:
            data = json.loads(raw)
        except ValueError:
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class Scope:
    """A connector instance or knowledge base: its ``apps`` document key, org and connector type."""

    key: str
    org_id: str
    connector: Connectors


class StateStore(Protocol):
    async def load(self, scope: Scope) -> tuple[SweepState, int | None]: ...

    async def claim(self, scope: Scope, epoch: int | None) -> int | None:
        """Take the next epoch, only while ``epoch`` is still the stored one. None when another process did."""
        ...

    async def save(self, scope: Scope, epoch: int, state: SweepState) -> bool:
        """Store ``state``, only while the stored epoch is ``epoch``."""
        ...


class GraphStateStore:
    """State on the scope's ``apps`` document, written with the graph's compare-and-set."""

    def __init__(self, graph: "IGraphDBProvider") -> None:
        self._graph = graph

    async def load(self, scope: Scope) -> tuple[SweepState, int | None]:
        doc = await self._graph.get_document(scope.key, _APPS, raise_on_error=True) or {}
        epoch = doc.get(_EPOCH_FIELD)
        epoch = int(epoch) if isinstance(epoch, int) and not isinstance(epoch, bool) else None
        return SweepState.load(doc.get(_STATE_FIELD)), epoch

    async def claim(self, scope: Scope, epoch: int | None) -> int | None:
        taken = (epoch or 0) + 1
        held = Cond(_EPOCH_FIELD, IS_NULL) if epoch is None else Cond(_EPOCH_FIELD, EQ, epoch)
        applied = await self._graph.update_node_fields_if(scope.key, _APPS, {_EPOCH_FIELD: taken}, [held])
        return taken if applied else None

    async def save(self, scope: Scope, epoch: int, state: SweepState) -> bool:
        return bool(await self._graph.update_node_fields_if(
            scope.key, _APPS, {_STATE_FIELD: state.dump()}, [Cond(_EPOCH_FIELD, EQ, epoch)],
        ))


# ---- leases --------------------------------------------------------------------------------------


class Lease(Protocol):
    def held(self) -> bool:
        """False once renewal has failed."""
        ...

    async def release(self) -> None: ...


class Leases(Protocol):
    async def acquire(self, key: str) -> Lease | None: ...

    async def record_busy(self, record_id: str) -> bool:
        """Whether an index run holds the record now (its own run projects it)."""
        ...


CoordinationRunner = Callable[[Awaitable[Any]], Awaitable[Any]]


class ManagerLeases:
    """Leases from the consumers' ``DistributedConcurrencyManager``.

    The manager belongs to the consumer's main loop; every call goes through ``run_coordination`` (the
    consumer's hop to that loop), because a task on another loop must not call it.
    """

    def __init__(
        self, manager: Any, run_coordination: CoordinationRunner | None = None,  # noqa: ANN401
        *, ttl: float = LEASE_TTL_SECONDS, renew_every: float = LEASE_RENEW_SECONDS,
    ) -> None:
        self._manager = manager
        self._run = run_coordination
        self._ttl = ttl
        self._renew_every = renew_every

    async def _call(self, coro: Awaitable[Any]) -> Any:  # noqa: ANN401
        return await (self._run(coro) if self._run is not None else coro)

    async def acquire(self, key: str) -> Lease | None:
        owner = f"property-sweep:{uuid4().hex}"
        if not await self._call(self._manager.try_acquire(key, owner, 1, self._ttl)):
            return None
        lease = _ManagerLease(self, key, owner)
        lease.start()
        return lease

    async def record_busy(self, record_id: str) -> bool:
        pool, owner = f"record:{record_id}", f"property-sweep:{uuid4().hex}"
        if not await self._call(self._manager.try_acquire(pool, owner, 1, self._ttl)):
            return True
        await self._call(self._manager.release(pool, owner))
        return False


class _ManagerLease:
    def __init__(self, leases: ManagerLeases, key: str, owner: str) -> None:
        self._leases, self._key, self._owner = leases, key, owner
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        # Detached: the renewal serves the lease manager, not the scope's org, and must outlive nothing of this request's context.
        self._task = spawn_detached(self._renew(), name="property-sweep-lease-renewal")

    async def _renew(self) -> None:
        leases = self._leases
        while True:
            await asyncio.sleep(leases._renew_every)
            if not await leases._call(leases._manager.renew(self._key, self._owner, leases._ttl)):
                # Observed through held(): the task is done.
                raise RuntimeError("Lost property sweep lease")

    def held(self) -> bool:
        return self._task is not None and not self._task.done()

    async def release(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        with contextlib.suppress(Exception):
            await self._leases._call(self._leases._manager.release(self._key, self._owner))


# ---- stored values -------------------------------------------------------------------------------


class PropertiesUnavailable(Exception):
    """The record's stored values cannot be read now (no revision yet, another revision, storage error)."""


PropertiesReader = Callable[[dict[str, Any]], Awaitable[RecordMetadata | None]]


class ArtifactPropertiesReader:
    """``properties.json`` of the revision the record is on, read through the revision pointer.

    None means the revision holds no such file (its record has no values). Anything else that stops the
    read raises ``PropertiesUnavailable``: the record is retried, never stamped as having no values.
    """

    def __init__(self, blob_store: Any, store: Any = None) -> None:  # noqa: ANN401
        from app.modules.artifacts.store import NodeArtifactStore

        self._blob = blob_store
        self._store = store or NodeArtifactStore(blob_store)

    async def __call__(self, doc: dict[str, Any]) -> RecordMetadata | None:
        import hashlib

        from pydantic import ValidationError

        from app.modules.artifacts.models import (
            MANIFEST_PATH,
            PROPERTIES_PATH,
            Manifest,
        )

        # The org is the one the sweep is running in (bound from its enumeration); the manifest below must name it as well.
        vrid, org_id = doc.get("virtualRecordId"), current_org_id()
        if not isinstance(vrid, str) or not vrid or not isinstance(org_id, str) or not org_id:
            raise PropertiesUnavailable("no_virtual_record")
        try:
            lookup = await self._blob.get_document_id_by_virtual_record_id(vrid) or {}
            rev, document_id = lookup.get("currentRev"), lookup.get("manifestDocId")
            if not rev or not document_id:
                raise PropertiesUnavailable("no_pointer")
            if doc.get("contentRev") and doc["contentRev"] != rev:
                raise PropertiesUnavailable("other_revision")
            urls = await self._store.read_urls(org_id, document_id, [MANIFEST_PATH])
            data = await self._store.get_if_present(urls[MANIFEST_PATH])
            if data is None:
                raise PropertiesUnavailable("no_manifest")
            try:
                manifest = Manifest.model_validate_json(data)
            except ValidationError as exc:
                raise PropertiesUnavailable("bad_manifest") from exc
            if (manifest.virtualRecordId, manifest.rev, manifest.orgId) != (vrid, rev, org_id):
                raise PropertiesUnavailable("other_revision")
            entry = manifest.artifacts.get("properties")
            if entry is None:
                return None
            if entry.path != PROPERTIES_PATH or entry.sha256 is None or entry.size is None:
                raise PropertiesUnavailable("bad_manifest")
            urls = await self._store.read_urls(org_id, document_id, [PROPERTIES_PATH])
            raw = await self._store.get(urls[PROPERTIES_PATH])
            if len(raw) != entry.size or hashlib.sha256(raw).hexdigest() != entry.sha256:
                raise PropertiesUnavailable("integrity")
            return RecordMetadata.model_validate_json(raw)
        except PropertiesUnavailable:
            raise
        except Exception as exc:
            # Only the type: an HTTP error can carry a signed URL.
            raise PropertiesUnavailable(f"read_error:{type(exc).__name__}") from exc


# ---- the sweep -----------------------------------------------------------------------------------


def connector_of_app(app: dict[str, Any]) -> Connectors | None:
    """The connector type of an app document, or None when it names none this module knows."""
    kind = app.get("type")
    if not isinstance(kind, str) or not kind:
        return None
    for candidate in (kind, kind.upper()):
        try:
            return Connectors(candidate)
        except ValueError:
            continue
    return None


class Stalled(Exception):
    """A full page moved the cursor nowhere: the next read would be the same page."""


class Stop(Exception):
    """The lap ends here: the lease or the epoch was lost, or the flag turned off."""


@dataclass
class LapCounts:
    rows: int = 0
    failed: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)

    def add(self, outcome: str, *, failed: bool = False) -> None:
        self.rows += 1
        self.failed += 1 if failed else 0
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1


class PropertyProjectionSweep:
    def __init__(
        self,
        *,
        graph: "IGraphDBProvider",
        registry: MetadataSchemaRegistry,
        state: StateStore,
        leases: Leases,
        reader: PropertiesReader,
        org_ids: Callable[[], Awaitable[list[str]]],
        cache: ScopeUnionCache | None = None,
        log: logging.Logger | None = None,
        clock: Callable[[], int] = get_epoch_timestamp_in_ms,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        enabled: Callable[[], bool] = projection_enabled,
        page_size: int = PAGE_SIZE,
        max_scopes: int = DEFAULT_MAX_SCOPES,
        duty: float = DUTY,
        skew_ms: int = SKEW_MS,
        repair_ms: int = REPAIR_PERIOD_MS,
        interval_ms: int = int(SEEK_INTERVAL_SECONDS * 1000),
        lease_backoff_ms: int = int(LEASE_TTL_SECONDS * 1000),
        read_concurrency: int = READ_CONCURRENCY,
        max_pages: int = SCOPE_MAX_PAGES,
        scope_budget_s: float = SCOPE_BUDGET_SECONDS,
    ) -> None:
        self._graph = graph
        self._registry = registry
        self._state = state
        self._leases = leases
        self._reader = reader
        self._org_ids = org_ids
        self._cache = cache or default_union_cache()
        self._log = log or logger
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._enabled = enabled
        self._page = max(1, page_size)
        self._max_pages = max(1, max_pages)
        self._scope_budget_s = scope_budget_s
        self._max_scopes = max(1, max_scopes)
        self._duty = min(1.0, max(0.01, duty))
        self._skew_ms = skew_ms
        self._repair_ms = repair_ms
        self._interval_ms = interval_ms
        self._lease_backoff_ms = lease_backoff_ms
        self._reads = asyncio.Semaphore(max(1, read_concurrency))
        self._backoff_until: dict[str, int] = {}
        self._turn = 0

    # -- one pass over every scope --

    async def scopes(self) -> list[Scope]:
        """Scopes in key order, rotated each pass so a cap on concurrent scopes starves none."""
        found: list[Scope] = []
        for org in await self._org_ids():
            # One org at a time, bound from the enumeration: a store that keeps each org's documents apart (PostgreSQL) reads only that org's
            # here, and one that lists them all is cut to the org it was asked for, so no document is taken from another org's read.
            with for_org(org, job="property_sweep"):
                apps = await self._graph.get_all_documents(_APPS)
            for app in apps:
                key, org_id, connector = app.get("_key") or app.get("id"), app.get("orgId"), connector_of_app(app)
                if not isinstance(key, str) or not key or org_id != org or connector is None:
                    continue
                if app.get("status") == "DELETING":
                    continue
                found.append(Scope(key, org_id, connector))
        found.sort(key=lambda scope: scope.key)
        if found:
            shift = self._turn % len(found)
            found = found[shift:] + found[:shift]
        self._turn += 1
        return found

    async def run_pass(self) -> dict[str, int]:
        """One pass over every scope that has a union. With the flag off: nothing is read or written."""
        if not self._enabled():
            return {}
        scopes = await self.scopes()
        if not scopes:
            return {}
        index_online = await self._change_index_online()
        limit = asyncio.Semaphore(self._max_scopes)
        results: dict[str, int] = {}

        async def one(scope: Scope) -> None:
            async with limit:
                with for_org(scope.org_id, job="property_sweep"):
                    try:
                        outcome = await asyncio.wait_for(self.sweep_scope(scope, index_online=index_online), self._scope_budget_s)
                    except asyncio.TimeoutError:
                        # The lease is released and the cursor is where the last page left it: the scope resumes next pass.
                        record_property_sweep_lap("any", "budget")
                        outcome = "budget"
            results[outcome] = results.get(outcome, 0) + 1

        await asyncio.gather(*(one(scope) for scope in scopes))
        return results

    async def _change_index_online(self) -> bool:
        """Ensure the change index, then say whether it can be queried. Any failure counts as not online."""
        try:
            with system("property_sweep_index"):          # the index is the store's, not an org's
                await self._graph.ensure_record_change_index()
                return bool(await self._graph.record_change_index_online())
        except NotImplementedError:
            raise
        except Exception as exc:
            self._log.warning("Record change index not usable this pass: %s", type(exc).__name__)
            return False

    # -- one scope --

    async def sweep_scope(self, scope: Scope, *, index_online: bool) -> str:
        now = self._clock()
        if self._backoff_until.get(scope.key, 0) > now:
            return "backoff"
        try:
            union = await self._cache.union_for(self._registry, scope.org_id, scope.key, scope.connector)
        except UnionUnavailable:
            return "union_unavailable"
        if union.empty:
            return "no_union"
        lease = await self._leases.acquire(f"property-projection-sweep:{scope.org_id}:{scope.key}")
        if lease is None:
            record_property_sweep_lap("any", "not_leader")
            return "not_leader"
        kind = SEEK
        try:
            state, stored_epoch = await self._state.load(scope)
            epoch = await self._state.claim(scope, stored_epoch)
            if epoch is None:
                record_property_sweep_lap("any", "stopped")
                return "epoch_lost"
            kind = self._lap_kind(state, union, now, index_online=index_online)
            if kind is None:
                record_property_sweep_lap("any", "not_due")
                return "not_due"
            if not await self._lap(scope, union, state, epoch, kind, lease, now):
                record_property_sweep_lap(kind, "paused")
                return "paused"
        except Stalled:
            self._backoff_until[scope.key] = self._clock() + self._lease_backoff_ms
            self._log.warning("Property sweep of scope %s stalled: a page moved its cursor nowhere", scope.key)
            record_property_sweep_lap(kind, "stalled")
            return "stalled"
        except Stop:
            self._backoff_until[scope.key] = self._clock() + self._lease_backoff_ms
            record_property_sweep_lap(kind, "stopped")
            return "stopped"
        except Exception as exc:
            self._log.warning("Property sweep of scope %s failed: %s", scope.key, type(exc).__name__)
            record_property_sweep_lap(kind, "failed")
            return "failed"
        finally:
            await lease.release()
        record_property_sweep_lap(kind, "done")
        return "done"

    def _lap_kind(self, state: SweepState, union: ScopeUnion, now: int, *, index_online: bool) -> str | None:
        """What to do now; None when nothing is due. A lap left part done resumes as the kind it was."""
        if state.lap is not None:
            return state.lap
        walked = state.covered_hash == union.set_hash and (state.covered_gen or 0) >= union.gen
        repair_due = state.repair_at is None or now - state.repair_at >= self._repair_ms
        if not walked or repair_due or state.watermark is None:
            return WALK
        if not index_online:
            return WALK_NO_INDEX
        if state.lap_end is not None and now - state.lap_end < self._interval_ms:
            return None
        return SEEK

    async def _lap(
        self, scope: Scope, union: ScopeUnion, state: SweepState, epoch: int, kind: str, lease: Lease, now: int,
    ) -> bool:
        """True when the lap read its last page; False when this pass's page bound ended it (the cursor is saved, the next pass goes on).
        Raises ``Stalled`` when a full page leaves the cursor where it was, so a lap can never read one page for ever."""
        walking = kind != SEEK
        if state.lap is None:
            state.lap, state.lap_start, state.failures = kind, now, 0
            state.cursor_ts, state.cursor_id = (None, None) if walking else (state.watermark, "")
            await self._save(scope, epoch, state)
        counts = LapCounts()
        pages = 0
        while True:
            self._check(lease)
            started = self._monotonic()
            rows = await self._page_of(scope, union, state, walking=walking)
            counts_before = counts.failed
            await self._project_rows(scope, rows, counts)
            state.failures += counts.failed - counts_before
            if len(rows) < self._page:
                self._finish(state, union, walking=walking)
                await self._save(scope, epoch, state)
                return True
            last = rows[-1]
            before = (state.cursor_ts, state.cursor_id)
            if walking:
                state.cursor_id = str(last.get("_key") or last.get("id"))
            else:
                state.cursor_ts, state.cursor_id = last["updatedAtTimestamp"], str(last.get("_key") or last.get("id"))
            if (state.cursor_ts, state.cursor_id) == before:
                raise Stalled
            await self._save(scope, epoch, state)
            pages += 1
            if pages >= self._max_pages:
                return False
            # Background work: at most `duty` of one core, however fast the pages come.
            await self._sleep((self._monotonic() - started) * (1.0 / self._duty - 1.0))

    def _check(self, lease: Lease) -> None:
        if not lease.held() or not self._enabled():
            raise Stop

    async def _save(self, scope: Scope, epoch: int, state: SweepState) -> None:
        if not await self._state.save(scope, epoch, state):
            raise Stop

    async def _page_of(self, scope: Scope, union: ScopeUnion, state: SweepState, *, walking: bool) -> list[dict[str, Any]]:
        if walking:
            return await self._graph.get_records_needing_projection(
                scope.key, scope.org_id, after_key=state.cursor_id, props_hash=union.set_hash,
                props_gen=union.gen, limit=self._page,
            )
        return await self._graph.get_records_updated_since(
            scope.key, scope.org_id, since=state.cursor_ts or 0, after_id=state.cursor_id or "",
            props_hash=union.set_hash, props_gen=union.gen, limit=self._page,
        )

    def _finish(self, state: SweepState, union: ScopeUnion, *, walking: bool) -> None:
        """The lap read its last page: its start, less the skew, is the new watermark."""
        start = state.lap_start if state.lap_start is not None else self._clock()
        state.watermark = start - self._skew_ms
        state.lap_end = self._clock()
        if walking:
            state.attempts = state.attempts + 1 if state.failures else 0
            if not state.failures or state.attempts >= MAX_WALK_ATTEMPTS:
                state.covered_gen, state.covered_hash, state.repair_at, state.attempts = union.gen, union.set_hash, start, 0
        state.lap = state.lap_start = state.cursor_ts = state.cursor_id = None
        state.failures = 0

    # -- one page --

    async def _project_rows(self, scope: Scope, rows: list[dict[str, Any]], counts: LapCounts) -> None:
        results = await asyncio.gather(*(self._project_row(scope, row) for row in rows))
        for outcome, failed in results:
            counts.add(outcome, failed=failed)
            record_property_sweep_row(outcome)

    async def _project_row(self, scope: Scope, doc: dict[str, Any]) -> tuple[str, bool]:
        """(outcome, failed). Never raises for one record: the pass goes on."""
        try:
            key = str(doc.get("_key") or doc.get("id"))
            if await self._leases.record_busy(key):
                return "skipped_busy", False
            provided: object = NOT_PROVIDED
            if scope.connector is Connectors.KNOWLEDGE_BASE:
                provided = await self._provided(doc)
            result = await project_properties(
                graph=self._graph, registry=self._registry, cache=self._cache,
                record=convert_record_dict_to_record(doc), record_doc=doc, provided=provided,
                logger=self._log, enabled=self._enabled,
            )
            ok = result.status in (WRITTEN, CURRENT)
            # A write that lost to a newer writer is not a failure: that writer's value stands.
            lost = result.status in ("fenced_by_generation", "superseded", "content_changed", "no_union", "no_stamp")
            return result.status, not (ok or lost)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log.debug("Property sweep: record failed: %s", type(exc).__name__)
            return "error", True

    async def _provided(self, doc: dict[str, Any]) -> object:
        """The record's stored values; ``NOT_PROVIDED`` when they cannot be read (the projection then fails closed)."""
        async with self._reads:
            try:
                return await self._reader(doc)
            except PropertiesUnavailable:
                return NOT_PROVIDED


# ---- the loop ------------------------------------------------------------------------------------


async def run_property_projection_sweep_loop(
    app_container: Any,  # noqa: ANN401
    graph_provider: "IGraphDBProvider",
    all_org_ids: Callable[["IGraphDBProvider"], Awaitable[list[str]]],
) -> None:
    """Sweep forever, on its own timer: apart from the recovery lap, which it never delays."""
    from app.modules.indexing.named_entity_completion import _record_lease_manager
    from app.modules.transformers.blob_storage import BlobStorage

    log = app_container.logger()
    interval = max(1.0, env_seconds("PROPERTY_SWEEP_INTERVAL_SECONDS", SEEK_INTERVAL_SECONDS))
    max_scopes = max(1, env_int("PROPERTY_SWEEP_MAX_SCOPES", DEFAULT_MAX_SCOPES))
    sweep: PropertyProjectionSweep | None = None
    said_no_manager = False
    while True:
        try:
            if projection_enabled():
                manager = _record_lease_manager(app_container)
                if manager is None:
                    # Without the consumers' lease manager there is no way to keep two processes off one
                    # scope's progress; the index path still projects what it indexes.
                    if not said_no_manager:
                        log.info("Property projection sweep idle: no lease manager (no record consumer)")
                        said_no_manager = True
                else:
                    if sweep is None:
                        consumers = getattr(app_container, "kafka_consumers", None) or []
                        runner = getattr(consumers[0][1], "_run_on_main_loop", None) if consumers else None
                        config_service = app_container.config_service()
                        sweep = PropertyProjectionSweep(
                            graph=graph_provider,
                            registry=MetadataSchemaRegistry(config_service),
                            state=GraphStateStore(graph_provider),
                            leases=ManagerLeases(manager, runner),
                            reader=ArtifactPropertiesReader(BlobStorage(log, config_service, graph_provider)),
                            org_ids=lambda: all_org_ids(graph_provider),
                            log=log, max_scopes=max_scopes, interval_ms=int(interval * 1000),
                        )
                    await sweep.run_pass()
        except asyncio.CancelledError:
            raise
        except NotImplementedError:
            log.info("Property projection sweep idle: this graph store does not support it")
            return
        except Exception:
            log.exception("Property projection sweep pass failed")
        await asyncio.sleep(interval)


__all__ = [
    "ArtifactPropertiesReader",
    "GraphStateStore",
    "ManagerLeases",
    "PropertiesUnavailable",
    "PropertyProjectionSweep",
    "Scope",
    "SweepState",
    "run_property_projection_sweep_loop",
]
