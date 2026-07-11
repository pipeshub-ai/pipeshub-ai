"""Redis-backed event counter powering the org-wide indexing progress bar.

Design rules (see the implementation plan):
  * Every method is best-effort — it swallows its own errors and applies a short
    Redis timeout. A Redis outage or latency spike MUST NOT propagate into
    indexing, the connectors, or the sync loop. This is the global isolation
    rule; do not let an exception escape a public method here.
  * Keys are raw ``progress:*`` (no service prefix) so the Node ticker reads the
    exact same keys via its raw ioredis client.
  * ``seed_org`` / ``reconcile_org`` OVERWRITE a connector hash to DB truth, so
    boot-seed, on-demand-seed, and the slow reconcile are all idempotent and
    safe to interleave with the live funnel.

Loop safety: the indexing service runs indexing on a **separate worker-thread
event loop** while the heartbeat/reconcile loop runs on the main loop. A single
redis-asyncio client binds to the loop that first touches it and raises
"attached to a different loop" from the other. So the counter keeps **one client
per event loop**, created lazily on first use — the funnel (worker loop) and the
maintenance loop (main loop) never share a client.

The counter is a per-process module singleton initialised in each service's
startup (indexing, connectors, query). ``bump_status`` is the funnel helper wired
at every ``indexingStatus`` write site.
"""

import asyncio
import logging

from redis import asyncio as aioredis  # type: ignore

# Raw Redis keys (no prefix).
ACTIVE_ORGS_KEY = "progress:active_orgs"
HEARTBEAT_KEY = "progress:indexer:heartbeat"
# Orgs that gained a new connector and need a prompt whole-workspace reconcile
# (so the bar shows ALL connectors right away, not just the newly-syncing one).
DIRTY_ORGS_KEY = "progress:dirty_orgs"


def connectors_key(org: str) -> str:
    return f"progress:connectors:{org}"


def counts_key(org: str, conn: str) -> str:
    return f"progress:counts:{org}:{conn}"


# The connector's display name lives as a reserved field INSIDE its counts hash
# (no separate key). The Node classifier ignores this field, so it never affects
# the status counts. Reconcile stores the unique instance name here; discovery
# stores the connector type as a provisional value until the first reconcile.
NAME_FIELD = "__name"


# Bucket for records whose connectorId is (legacy) null. Real KB/uploaded records
# carry the KB's connectorId and bucket normally; this only guards a HINCRBY on a
# null key for pre-existing docs written before connectorId became required.
KB_CONNECTOR_FALLBACK = "knowledge-base"

_REDIS_TIMEOUT_SECONDS = 2.0


class ProgressCounter:
    """Thin, self-isolating, loop-safe wrapper over the raw Redis progress ops."""

    def __init__(self, redis_url: str, logger: logging.Logger) -> None:
        self._redis_url = redis_url
        self._logger = logger
        # One client per event loop (keyed by id(loop)); see the module docstring.
        self._clients: dict[int, aioredis.Redis] = {}
        # Live background tasks for fire-and-forget hot-path ops (kept referenced
        # so they aren't garbage-collected before they run).
        self._tasks: set = set()

    def _client(self) -> aioredis.Redis:
        """Return a Redis client bound to the currently running event loop."""
        loop = asyncio.get_running_loop()
        key = id(loop)
        client = self._clients.get(key)
        if client is None:
            client = aioredis.from_url(
                self._redis_url, encoding="utf-8", decode_responses=True
            )
            self._clients[key] = client
        return client

    async def _guard(self, coro) -> None:
        """Run a Redis coroutine with a timeout, swallowing every failure."""
        try:
            await asyncio.wait_for(coro, timeout=_REDIS_TIMEOUT_SECONDS)
        except Exception as e:  # noqa: BLE001 - isolation: never surface to caller
            self._logger.debug(f"progress counter op skipped: {e}")

    def _fire(self, coro) -> None:
        """Fire-and-forget a guarded Redis op — NEVER blocks the caller.

        The hot-path hooks (status_changed / record_discovered / record_deleted /
        connector_removed) are called from inside the indexing pipeline, sometimes
        within an open DB transaction. Awaiting Redis there would hold that
        transaction open (and, on a stall, risk its TTL expiring and the real
        write rolling back). Scheduling the op as a background task makes the hook
        return in microseconds; the Redis work (with its own timeout) runs later,
        off the transaction's critical path.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()  # no running loop; drop it (best-effort)
            return
        task = loop.create_task(self._guard(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def record_discovered(
        self, org: str, conn: str | None, conn_name: str | None = None
    ) -> None:
        conn = conn or KB_CONNECTOR_FALLBACK
        if not org:
            return
        self._fire(self._record_discovered(org, conn, conn_name))

    async def _record_discovered(
        self, org: str, conn: str, conn_name: str | None
    ) -> None:
        c = self._client()
        await c.sadd(ACTIVE_ORGS_KEY, org)
        new_conn = await c.sadd(connectors_key(org), conn)
        key = counts_key(org, conn)
        await c.hincrby(key, "QUEUED", 1)
        if conn_name:
            # Provisional name (connector type) stored in-hash so the widget never
            # shows a raw id before the reconcile fills the unique instance name.
            await c.hset(key, mapping={NAME_FIELD: conn_name})
        if new_conn:
            # First time this connector is seen this session → flag the org so the
            # query-service reconcile pulls the full workspace (all connectors)
            # promptly instead of showing only this one until the next full cycle.
            await c.sadd(DIRTY_ORGS_KEY, org)

    async def pop_dirty_orgs(self) -> list[str]:
        """Return and clear the set of orgs needing a prompt reconcile."""
        try:
            c = self._client()
            members = await asyncio.wait_for(
                c.smembers(DIRTY_ORGS_KEY), timeout=_REDIS_TIMEOUT_SECONDS
            )
            if members:
                await c.delete(DIRTY_ORGS_KEY)
            return list(members or [])
        except Exception as e:  # noqa: BLE001
            self._logger.debug(f"progress pop_dirty_orgs skipped: {e}")
            return []

    async def status_changed(
        self, org: str, conn: str | None, old: str | None, new: str | None
    ) -> None:
        conn = conn or KB_CONNECTOR_FALLBACK
        if not org or not new or old == new:
            return
        self._fire(self._status_changed(org, conn, old, new))

    async def _status_changed(
        self, org: str, conn: str, old: str | None, new: str
    ) -> None:
        c = self._client()
        # Register the org/connector so a first-seen transition (e.g. a recovered
        # record with no prior discovery event) still surfaces in the bar.
        await c.sadd(ACTIVE_ORGS_KEY, org)
        await c.sadd(connectors_key(org), conn)
        key = counts_key(org, conn)
        if old:
            await c.hincrby(key, old, -1)
        await c.hincrby(key, new, 1)

    async def record_deleted(
        self, org: str, conn: str | None, old_status: str | None
    ) -> None:
        conn = conn or KB_CONNECTOR_FALLBACK
        if not org or not old_status:
            return
        self._fire(self._record_deleted(org, conn, old_status))

    async def _record_deleted(self, org: str, conn: str, old_status: str) -> None:
        await self._client().hincrby(counts_key(org, conn), old_status, -1)

    async def connector_removed(self, org: str, conn: str | None) -> None:
        """Drop a whole connector from the bar — used when a connector instance is
        deleted (a bulk graph delete that bypasses the per-record delete hook)."""
        conn = conn or KB_CONNECTOR_FALLBACK
        if not org:
            return
        self._fire(self._connector_removed(org, conn))

    async def _connector_removed(self, org: str, conn: str) -> None:
        c = self._client()
        await c.delete(counts_key(org, conn))  # name lives in this hash too
        await c.srem(connectors_key(org), conn)

    async def seed_org(self, org: str, rows: list[dict]) -> None:
        """Overwrite every connector hash for ``org`` from a count-by-status
        result set (``[{connectorId, connectorName, indexingStatus, cnt}, ...]``).
        Idempotent."""
        if not org:
            return
        await self._guard(self._seed_org(org, rows))

    # reconcile uses identical overwrite semantics.
    reconcile_org = seed_org

    async def _seed_org(self, org: str, rows: list[dict]) -> None:
        by_conn: dict[str, dict[str, int]] = {}
        names: dict[str, str] = {}
        for row in rows or []:
            conn = row.get("connectorId") or KB_CONNECTOR_FALLBACK
            status = row.get("indexingStatus")
            cnt = int(row.get("cnt") or 0)
            name = row.get("connectorName")
            if name:
                names[conn] = name
            if not status:
                continue
            by_conn.setdefault(conn, {})[status] = cnt

        c = self._client()
        await c.sadd(ACTIVE_ORGS_KEY, org)

        # Remove connectors that no longer have any records in the DB (deleted
        # connector/collection, or records purged while the service was down).
        # The counts hash holds the name too, so deleting it drops the name as well.
        fresh = set(by_conn.keys())
        existing = await c.smembers(connectors_key(org)) or set()
        for dead in set(existing) - fresh:
            await c.delete(counts_key(org, dead))
            await c.srem(connectors_key(org), dead)

        for conn, counts in by_conn.items():
            key = counts_key(org, conn)
            mapping: dict[str, object] = dict(counts)
            if names.get(conn):
                # Unique instance name stored alongside the counts (no extra key).
                mapping[NAME_FIELD] = names[conn]
            # Overwrite: drop the old hash, then write truth. Any concurrent
            # funnel write racing this is healed on the next reconcile.
            await c.delete(key)
            if mapping:
                await c.hset(key, mapping=mapping)
            await c.sadd(connectors_key(org), conn)

    async def write_heartbeat(self, now_ms: int) -> None:
        await self._guard(self._write_heartbeat(now_ms))

    async def _write_heartbeat(self, now_ms: int) -> None:
        await self._client().set(HEARTBEAT_KEY, str(now_ms))

    async def get_active_orgs(self) -> list[str]:
        try:
            members = await asyncio.wait_for(
                self._client().smembers(ACTIVE_ORGS_KEY),
                timeout=_REDIS_TIMEOUT_SECONDS,
            )
            return list(members or [])
        except Exception as e:  # noqa: BLE001
            self._logger.debug(f"progress get_active_orgs skipped: {e}")
            return []


_counter: ProgressCounter | None = None


def init_progress_counter(redis_url: str, logger: logging.Logger) -> ProgressCounter:
    """Install the per-process singleton. Called once from each service startup."""
    global _counter
    _counter = ProgressCounter(redis_url, logger)
    logger.info("✅ Progress counter initialized")
    return _counter


def get_progress_counter() -> ProgressCounter | None:
    return _counter


async def bump_status(doc: dict | None, old_status: str | None, new_status: str) -> None:
    """Funnel: record an ``indexingStatus`` transition into the progress counter.

    ``doc`` is the record document (supplies ``orgId`` / ``connectorId``);
    ``old_status`` MUST be captured before the doc is mutated in place. Best-effort
    and fully isolated — never raises into the indexing path.
    """
    counter = _counter
    if counter is None or doc is None:
        return
    try:
        org = doc.get("orgId")
        conn = doc.get("connectorId")
        await counter.status_changed(org, conn, old_status, new_status)
    except Exception:  # noqa: BLE001 - isolation
        pass
