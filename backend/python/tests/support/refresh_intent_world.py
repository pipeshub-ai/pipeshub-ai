"""One record, driven by the model's action alphabet over the repository's own code (G-46).

``tests/support/refresh_intent_model.py`` is the reference model. This is the same world with the real pieces in it:
the record handler's ``_settle_refresh_intent`` and ``_end_refresh_run`` (the run's start and end), the walk's
``_clear_status_before_publish`` and ``_keep_request_of_running_records``, ``record_event`` (the stranded sweep's event),
and ``_republish_refresh_intents`` (the intent sweep). What is simulated is the clock, a graph that holds the record
(with the compare-and-set the three stores give), a broker that can lose a message, the record lease, a twin, and the
runs themselves (a run is an IN_PROGRESS row, then COMPLETED or a crash).
"""
from __future__ import annotations

import logging
import random
from collections import Counter
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from app import indexing_main
from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.connectors.services.event_service import EventService
from app.core.org_context import OrgContext, bind
from app.modules.indexing import refresh_intent as ri
from app.modules.indexing.record_republish import is_parked_duplicate, record_event
from app.services.messaging.kafka.handlers import record as record_handler
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.services.paid_cache import access as paid_cache_access
from app.services.paid_cache import run as paid_cache_run

if TYPE_CHECKING:
    from collections.abc import Iterator

KEY = "rec-1"
ORG = "org-1"
START_MS = 1_800_000_000_000
MINUTE_MS = 60_000
IN_PROGRESS = ProgressStatus.IN_PROGRESS.value
GRAPH_LOG = logging.getLogger("refresh_intent_world")


class IntentGraph:
    """The record store, with the semantics the three stores give: a compare-and-set where null matches absent."""

    def __init__(self, doc: dict[str, Any]) -> None:
        self.docs = {KEY: doc}

    async def get_document(self, key: str, collection: str, **_kw: object) -> dict | None:
        doc = self.docs.get(key)
        return dict(doc) if doc is not None else None

    async def update_node(self, key: str, collection: str, fields: dict) -> bool:
        if key not in self.docs:
            return False
        self.docs[key].update(fields)
        return True

    async def update_node_fields_if_match(
        self, key: str, collection: str, updates: dict, expected: dict, transaction: str | None = None, **_kw: object,
    ) -> bool:
        doc = self.docs.get(key)
        if doc is None:
            return False
        for name, value in expected.items():
            held = doc.get(name)
            if (value is None and held is not None) or (value is not None and held != value):
                return False
        doc.update(updates)
        return True

    async def update_record_fields_for_ids(self, ids: list[str], fields: dict) -> int:
        n = 0
        for key in ids:
            if key in self.docs:
                self.docs[key].update(fields)
                n += 1
        return n

    async def update_indexing_status_for_record_ids(self, ids: list[str], status: str, *, fields: dict | None = None) -> None:
        for key in ids:
            if key in self.docs:
                self.docs[key].update({"indexingStatus": status, **(fields or {})})

    async def compare_and_set_indexing_status(self, ids: list[str], expected: str, new: str, *_a: object) -> list[str]:
        done = []
        for key in ids:
            if self.docs.get(key, {}).get("indexingStatus") == expected:
                self.docs[key]["indexingStatus"] = new
                done.append(key)
        return done

    async def get_records_with_refresh_intent(self, org_id: str, *, after_key: str | None = None, limit: int = 100) -> list[dict]:
        rows = [
            dict(d, _key=k) for k, d in sorted(self.docs.items())
            if d.get(ri.ID_FIELD) and d.get("orgId") == org_id and d.get("isDeleted") is not True
            and (after_key is None or k > after_key)
        ]
        return rows[:limit]


class _Producer:
    def __init__(self, world: World) -> None:
        self.world = world

    async def send_event(self, *, topic: str, event_type: str, payload: dict, key: str) -> None:
        self.world.msgs.append({"event_type": event_type, "payload": dict(payload), "derived": True})


class _Leases:
    """The record lease: held while a run is in progress."""

    def __init__(self, world: World) -> None:
        self.world = world

    async def try_acquire(self, pool: str, owner: str, slots: int, seconds: float) -> bool:
        return self.world.active is None

    async def release(self, pool: str, owner: str) -> None:
        return None


class _Cache:
    def __init__(self) -> None:
        self.done: set[tuple[str, str, str]] = set()

    async def refresh_done(self, org: str, record: str, intent_id: str) -> bool:
        return (org, record, intent_id) in self.done

    async def mark_refresh_done(self, org: str, record: str, intent_id: str) -> None:
        self.done.add((org, record, intent_id))


class World:
    def __init__(self, seed: int, *, twins: bool, steps: int = 40) -> None:
        self.R = random.Random(seed)
        self.twins = twins
        self.steps = steps
        self.now_ms = START_MS
        self.t = 0
        self.graph = IntentGraph({
            "_key": KEY, "orgId": ORG, "indexingStatus": ProgressStatus.COMPLETED.value, "version": 1,
            "virtualRecordId": "v1", "md5Checksum": "m1", "origin": "CONNECTOR", "connectorId": "c1", "connectorName": "web",
        })
        self.msgs: list[dict] = []
        self.active: dict | None = None
        self.crashed = False
        self.twin_inflight = False
        self.reqs: dict[int, int] = {}
        self.sat: set[int] = set()
        self.notes: Counter[str] = Counter()
        self.cache = _Cache()
        self.handler = RecordEventHandler.__new__(RecordEventHandler)
        self.handler.logger = GRAPH_LOG
        self.handler.config_service = object()
        self.handler.event_processor = SimpleNamespace(graph_provider=self.graph)
        self.handler.producer = _Producer(self)
        self.handler._producer_loop = None
        self.service = EventService.__new__(EventService)
        self.service.graph_provider = self.graph
        self.service.logger = GRAPH_LOG

    # ---- helpers
    def tick(self) -> int:
        self.t += 1
        return self.t

    def clock(self) -> int:
        return self.now_ms

    def advance(self, low_ms: int, high_ms: int) -> None:
        self.now_ms += self.R.randint(low_ms, high_ms)

    @property
    def row(self) -> dict:
        return self.graph.docs[KEY]

    def unsatisfied_before(self, ts: int) -> list[int]:
        return [k for k, tk in self.reqs.items() if k not in self.sat and tk < ts]

    def _lost_or_requeued(self, payload: dict, event_type: str, *, allow_loss: bool, derived: bool) -> None:
        if not (allow_loss and self.R.random() < 0.5):
            self.msgs.append({"event_type": event_type, "payload": payload, "derived": derived})

    # ---- actions
    async def user_force(self) -> None:
        k = self.tick()
        intent_id = f"i{k}"
        self.reqs[k] = k
        status = self.row["indexingStatus"]
        records = [SimpleNamespace(id=KEY, indexing_status=status)]
        if status == IN_PROGRESS:
            records, _kept = await self.service._keep_request_of_running_records(records, intent_id)
            if not records:
                return
        await self.service._clear_status_before_publish(records, intent_id)
        if self.R.random() < 0.1:
            self.notes["publish_failed"] += 1
            return
        payload = {
            "recordId": KEY, "orgId": ORG, "version": 1, "virtualRecordId": "v1", "forceReindex": True,
            "refreshPaidOutputs": True, ri.PAYLOAD_ID_KEY: intent_id, "_event_id": f"e{k}", "_delivery_id": f"d{k}",
        }
        self.msgs.append({"event_type": EventTypes.REINDEX_RECORD.value, "payload": payload})
        await self.graph.compare_and_set_indexing_status([KEY], ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value)

    async def plain_update(self) -> None:
        k = self.tick()
        payload = {"recordId": KEY, "orgId": ORG, "version": 1, "virtualRecordId": "v1", "_event_id": f"e{k}", "_delivery_id": f"d{k}"}
        self.msgs.append({"event_type": EventTypes.UPDATE_RECORD.value, "payload": payload})
        if self.active is None and self.row["indexingStatus"] != IN_PROGRESS:
            self.row["indexingStatus"] = ProgressStatus.QUEUED.value

    async def lose(self) -> None:
        if self.msgs:
            self.msgs.pop(self.R.randrange(len(self.msgs)))
            self.notes["lost"] += 1

    async def twin_start(self) -> None:
        self.twin_inflight = True

    async def twin_complete(self) -> None:
        if not self.twin_inflight:
            return
        self.twin_inflight = False
        # The store's promotion moves every QUEUED same-content copy that holds no request (the filter of
        # update_queued_duplicates_status; the real statements are tested on the three stores).
        if self.row["indexingStatus"] == ProgressStatus.QUEUED.value and self.active is None and ri.stored_intent(self.row) is None:
            self.row["indexingStatus"] = ProgressStatus.COMPLETED.value
            self.notes["promoted"] += 1

    async def consume(self, *, allow_loss: bool = True) -> None:
        if self.active is not None or not self.msgs:
            return
        msg = self.msgs.pop(0)
        event_type, payload = msg["event_type"], msg["payload"]
        # Derived: not the user's own message. Built from the record (a sweep, a hand-over), or a plain event the record's
        # request turned into a refresh. Kept by the world, not read off the payload, so removing a payload marker is seen.
        was_refresh = paid_cache_run.refresh_requested(payload)
        run = await self.handler._settle_refresh_intent(event_type, payload, dict(self.row), KEY, parse_now=False)
        if run is not None and await self.handler._refresh_already_ran(run, dict(self.row), payload):
            self.notes["deduped"] += 1
            return
        derived = bool(msg.get("derived")) or bool(run is not None and run.refresh and not was_refresh)
        if run is not None and run.refresh:
            if self.R.random() < 0.15:                       # a hand-back before the refresh branch
                self.notes["pre_branch_handback"] += 1
                self._lost_or_requeued(payload, event_type, allow_loss=allow_loss, derived=derived)
                return
            if self.R.random() < 0.2:                        # RefreshPurgeHeld
                self.notes["held"] += 1
                self._lost_or_requeued(payload, event_type, allow_loss=allow_loss, derived=derived)
                return
        self.active = {
            "run": run, "payload": payload, "start": self.tick(),
            "derived": derived,
        }
        self.row["indexingStatus"] = IN_PROGRESS
        self.crashed = False

    async def finish(self, *, allow_crash: bool = True) -> None:
        if self.active is None:
            return
        active, self.active = self.active, None
        if allow_crash and self.R.random() < 0.2:
            self.crashed = True
            self.notes["crash"] += 1
            return
        run = active["run"]
        if run is not None and run.refresh:
            unsatisfied = self.unsatisfied_before(active["start"])
            if active["derived"] and not unsatisfied:
                self.notes["spurious_any"] += 1
            self.sat.update(unsatisfied)
        self.row["indexingStatus"] = ProgressStatus.COMPLETED.value
        if run is not None:
            await self.handler._end_refresh_run(KEY, run, active["payload"])

    async def stale_sweep(self) -> None:
        if self.crashed and self.active is None and self.row["indexingStatus"] == IN_PROGRESS:
            payload = {"recordId": KEY, "orgId": ORG, "version": 1, "virtualRecordId": "v1"}
            ri.with_intent(payload, self.row, self.now_ms)        # what the recovery pass adds (indexing_main)
            self.msgs.append({"event_type": EventTypes.REINDEX_RECORD.value, "payload": payload, "derived": True})
            self.row["indexingStatus"] = ProgressStatus.QUEUED.value
            self.crashed = False

    async def stranded_sweep(self) -> None:
        status = self.row["indexingStatus"]
        if self.active is not None or self.msgs or status not in (ProgressStatus.QUEUED.value, ProgressStatus.NOT_STARTED.value):
            return
        if self.twin_inflight and status == ProgressStatus.QUEUED.value and is_parked_duplicate(self.row):
            return                                           # _still_parked_behind_a_twin
        event_type, payload = record_event(self.row, record_key=KEY, connector_id="c1")
        self.msgs.append({"event_type": event_type, "payload": payload, "derived": True})

    async def intent_sweep(self) -> None:
        await indexing_main._republish_refresh_intents(
            graph_provider=self.graph, logger=GRAPH_LOG, producer=_Producer(self), run_coordination=_pass,
            concurrency_manager=_Leases(self), page_size=10, org_id=ORG, read_backlog=None,
        )

    # ---- run
    async def drive(self) -> tuple[bool, Counter[str]]:
        actions = [
            self.user_force, self.plain_update, self.lose, self.consume, self.consume, self.finish, self.finish,
            self.stale_sweep, self.stranded_sweep, self.intent_sweep,
        ]
        if self.twins:
            actions += [self.twin_start, self.twin_complete]
        await self.user_force()
        for _ in range(self.steps):
            self.advance(0, 30 * MINUTE_MS)
            await self.R.choice(actions)()
        for _ in range(80):                                   # drain: no new requests, no losses, no crashes
            self.advance(90 * MINUTE_MS, 90 * MINUTE_MS)
            await self.twin_complete()
            await self.consume(allow_loss=False)
            await self.finish(allow_crash=False)
            await self.stale_sweep()
            await self.stranded_sweep()
            await self.intent_sweep()
            if (
                not self.msgs and self.active is None and not self.crashed
                and self.row["indexingStatus"] not in (ProgressStatus.QUEUED.value, ProgressStatus.NOT_STARTED.value, IN_PROGRESS)
                and ri.live_intent(self.row, self.now_ms) is None
            ):
                break
        dropped = [k for k in self.reqs if k not in self.sat]
        return bool(dropped), self.notes


async def _pass(awaitable: Any) -> Any:  # noqa: ANN401
    return await awaitable


@contextmanager
def patched(world: World) -> Iterator[None]:
    """The writes on, the clock the world's, the paid-result marker the world's, and every record live."""
    with ExitStack() as stack:
        stack.enter_context(patch.dict("os.environ", {"REFRESH_INTENT_WRITE": "true", "STRANDED_RECORD_REPUBLISH_AFTER_SECONDS": "3600"}))
        for module in (
            "app.indexing_main", "app.modules.indexing.record_republish", "app.services.messaging.kafka.handlers.record",
            "app.connectors.services.event_service",
        ):
            stack.enter_context(patch(f"{module}.get_epoch_timestamp_in_ms", world.clock))
        stack.enter_context(patch.object(paid_cache_access, "paid_cache_for", lambda *_a, **_k: world.cache))
        stack.enter_context(patch.object(record_handler, "is_live_record", lambda _r: True))
        stack.enter_context(bind(OrgContext(ORG, None, "job", "job:test", "t")))
        yield


async def run_world(seed: int, *, twins: bool, steps: int = 40) -> tuple[bool, Counter[str]]:
    """One random world."""
    world = World(seed, twins=twins, steps=steps)
    with patched(world):
        return await world.drive()
