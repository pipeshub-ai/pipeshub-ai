"""Client-side gate in front of the bundled embedding server's bulk slots.

The server runs a few encodes at once and keeps one slot for search queries. Indexing sends far more
bulk batches than that, so the rest queue inside the HTTP request, where the per-attempt timeout counts
the queueing as if it were embedding. This gate holds the extra batches in the indexing process, at the
width the server reports, so that an attempt's timeout covers only work the server is doing.

Waiting is not failing. A waiter gives up early only when the server shows it is not making progress
(see ``BulkGate._failfast_if_server_stuck``); otherwise it waits as long as the holders ahead of it take.

The gate is shared by every event loop of the process (each consumer runs its own), so its state is
guarded by a thread lock and a waiter is woken on its own loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from app.services.resource_governor.feedback import get_default_downstream_feedback
from app.utils.embedding_retry import is_retriable_embedding_error
from app.utils.indexing_workers import indexing_worker_count
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from app.modules.indexing.record_progress import RecordProgress

logger = create_logger("embedding_gate")

#: Env var for the number of bulk batches that may be in flight at the server, summed over the indexing
#: workers. ``0`` removes the gate; empty or unset lets the server's own report decide.
CONCURRENCY_ENV = "EMBEDDING_INDEXING_CONCURRENCY"
#: How long the server's report is trusted before it is read again, and the shortest wait after a failed read.
WIDTH_REFRESH_SECONDS = 60.0
WIDTH_RETRY_SECONDS = 5.0
#: The server answers ``/health`` from memory; a slower answer is itself a symptom.
HEALTH_TIMEOUT_SECONDS = 2.0
#: A waiter fails fast when the oldest encode in a server slot is older than this many attempt timeouts.
#: One attempt's worth would misfire: a healthy encode that outlives the client's timeout by a little
#: (the server cannot cancel a running encode) reads about one timeout right after the holder gave up.
STUCK_AGE_ATTEMPTS = 2.0
#: A permit granted to a waiter is taken back if the waiter's loop has not completed the grant by then
#: (the loop was stopped without being closed). Loops run grants within milliseconds.
GRANT_DELIVERY_SECONDS = 5.0
#: A "the server is fine" answer from ``/health`` is reused by the waiters of one generation for this long.
VERDICT_FRESH_SECONDS = 5.0


class HealthUnreachable(Exception):
    """The server's ``/health`` did not answer, or answered with a server error."""


class GateFailFast(TimeoutError):
    """A waiter queued behind a holder that failed, and the server shows it is not making progress.

    A ``TimeoutError`` so the retry loop treats it as a retriable attempt failure and backs off.
    """


async def fetch_health(url: str, timeout: float) -> dict[str, Any]:
    """The server's ``/health`` body. Raises ``HealthUnreachable`` for no answer or a 5xx."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise HealthUnreachable(type(exc).__name__) from exc
    if response.status_code >= 500:
        raise HealthUnreachable(f"HTTP {response.status_code}")
    if response.status_code != 200:
        return {}
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def server_bulk_limit(health: dict[str, Any]) -> int | None:
    """The bulk concurrency a ``/health`` body implies: its own limit, else one slot less than the server runs."""
    gate = health.get("encode_gate")
    if isinstance(gate, dict):
        limit = _positive_int(gate.get("bulk_limit"))
        if limit is not None:
            return limit
    cpu = health.get("cpu")
    if isinstance(cpu, dict):
        slots = _positive_int(cpu.get("max_concurrent_embeddings"))
        if slots is not None:
            return max(1, slots - 1)
    return None


_warned_env: set[str] = set()


def _env_width() -> tuple[bool, int | None]:
    """``(set, width)`` from the env var; width ``None`` means unlimited."""
    raw = os.getenv(CONCURRENCY_ENV, "").strip()
    if not raw:
        return False, None
    try:
        value = int(raw)
    except ValueError:
        # This is read on every batch; one warning per distinct value is enough.
        if raw not in _warned_env:
            _warned_env.add(raw)
            logger.warning("Invalid %s=%r; ignoring it", CONCURRENCY_ENV, raw)
        return False, None
    return True, (value if value > 0 else None)


def _workers() -> int:
    try:
        return indexing_worker_count()
    except ValueError:
        return 1


_logged_no_gate = False


def _log_no_gate_once(url: str) -> None:
    """One line per process: without it a missing gate is invisible until queue timeouts show up."""
    global _logged_no_gate  # noqa: PLW0603
    if _logged_no_gate:
        return
    _logged_no_gate = True
    logger.info(
        "Embedding gate is off: no bulk limit could be read from %s (no encode_gate, no usable "
        "max_concurrent_embeddings, or it did not answer) and %s is not set",
        url, CONCURRENCY_ENV,
    )


def _oldest_in_use_age(health: dict[str, Any]) -> float:
    """Age of the oldest bulk encode; the query lane is reserved apart and says nothing about bulk progress."""
    gate = health.get("encode_gate")
    ages = gate.get("oldest_in_use_age_s") if isinstance(gate, dict) else None
    age = ages.get("bulk") if isinstance(ages, dict) else None
    return float(age) if isinstance(age, (int, float)) and not isinstance(age, bool) else 0.0


@dataclass(eq=False)
class _Waiter:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[bool]
    generation: int = 0
    # Set when a permit is taken for this waiter and cleared when its own loop completes the grant.
    granted_at: float | None = None
    reclaimed: bool = False


@dataclass
class _Verdict:
    generation: int
    why: str | None
    at: float


@dataclass
class _State:
    width: int | None = None
    width_known: bool = False
    holders: int = 0
    generation: int = 0
    reported_generation: int = -1
    next_refresh: float = 0.0
    verdict: _Verdict | None = None
    waiters: deque[_Waiter] = field(default_factory=deque)
    granted: list[_Waiter] = field(default_factory=list)


class _Flight:
    """One caller at a time does a piece of work; callers on any loop that arrive meanwhile get its result."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = False
        self._joiners: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[Any]]] = []

    @property
    def busy(self) -> bool:
        return self._busy

    async def run(self, work: Callable[[], Awaitable[Any]], *, join: bool = True) -> Any:  # noqa: ANN401
        """Run ``work`` unless another caller is; then wait for that caller's result (or return ``None`` if ``join`` is false).

        The wait is bounded: a leader whose loop was stopped mid-work must not hold the others for ever.
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._busy:
                if not join:
                    return None
                future: asyncio.Future[Any] = loop.create_future()
                self._joiners.append((loop, future))
            else:
                self._busy = True
                future = None  # type: ignore[assignment]
        if future is not None:
            done, _ = await asyncio.wait({future}, timeout=2 * HEALTH_TIMEOUT_SECONDS)
            if not done:
                future.cancel()
                return None
            return future.result()
        result = None
        try:
            result = await work()
            return result
        finally:
            with self._lock:
                joiners, self._joiners = self._joiners, []
                self._busy = False
            for joiner_loop, joiner in joiners:
                with contextlib.suppress(RuntimeError):
                    joiner_loop.call_soon_threadsafe(_settle, joiner, result)


def _settle(future: asyncio.Future[Any], value: object) -> None:
    if not future.done():
        future.set_result(value)


class BulkGate:
    """A counting gate for one embedding server, shared by every loop in the process."""

    def __init__(
        self,
        base_url: str,
        *,
        fetch: Callable[[str, float], Awaitable[dict[str, Any]]] = fetch_health,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # ``base_url`` is the OpenAI-style root (``.../v1``); health lives beside it.
        root = base_url.rstrip("/")
        self.health_url = f"{root[:-3] if root.endswith('/v1') else root}/health"
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._s = _State()
        self._width_flight = _Flight()
        self._verdict_flight = _Flight()

    # ---- width ------------------------------------------------------------------------------------------------------------------
    @property
    def width(self) -> int | None:
        """Batches allowed in flight from this process; ``None`` when there is no gate."""
        with self._lock:
            return self.width_locked()

    def width_locked(self) -> int | None:
        env_set, env_width = _env_width()
        if env_set:
            total = env_width
        elif self._s.width_known:
            total = self._s.width
        else:
            return None
        return None if total is None else max(1, total // _workers())

    async def _read_width(self) -> None:
        """Read the server's limit once; keep the last known width on a failed read."""
        with self._lock:
            if self._clock() < self._s.next_refresh:
                return
        failed: str | None
        limit: int | None = None
        try:
            limit = server_bulk_limit(await self._fetch(self.health_url, HEALTH_TIMEOUT_SECONDS))
            failed = None if limit is not None else "no concurrency limit in the answer"
        except Exception as exc:
            failed = type(exc).__name__
        with self._lock:
            if failed is None:
                self._s.width, self._s.width_known = limit, True
                self._s.next_refresh = self._clock() + WIDTH_REFRESH_SECONDS
                self._pump_locked()
            else:
                self._s.next_refresh = self._clock() + WIDTH_RETRY_SECONDS
            unknown = not self._s.width_known
        if failed is not None:
            logger.debug("Embedding server limit not read from %s: %s", self.health_url, failed)
            if unknown:
                _log_no_gate_once(self.health_url)

    async def _current_width(self) -> int | None:
        """The width to gate at, reading the server's limit first when it is unknown or stale."""
        if _env_width()[0]:
            return self.width
        with self._lock:
            known = self._s.width_known
            due = not known or self._clock() >= self._s.next_refresh
        if due:
            # Before the first read every caller waits for it: letting them through would run the first
            # burst of batches ungated, which is the burst the gate exists for. Once a width is known a
            # stale one is used while one caller refreshes it.
            await self._width_flight.run(self._read_width, join=not known)
        return self.width

    # ---- grants -----------------------------------------------------------------------------------------------------------------
    def _reclaim_locked(self) -> None:
        """Take back permits granted to waiters whose loop never ran the grant (stopped, not closed)."""
        now = self._clock()
        for waiter in list(self._s.granted):
            if waiter.granted_at is not None and now - waiter.granted_at > GRANT_DELIVERY_SECONDS:
                waiter.reclaimed = True
                self._s.granted.remove(waiter)
                self._s.holders = max(0, self._s.holders - 1)

    def _pump_locked(self) -> None:
        """Hand free permits to waiters. A grant is two-phase: the permit is taken here, under the lock, and
        the waiter's own loop completes it (``_deliver``), which passes the permit on if the waiter is gone."""
        self._reclaim_locked()
        s = self._s
        width = self.width_locked()
        while s.waiters and (width is None or s.holders < width):
            waiter = s.waiters.popleft()
            if waiter.loop.is_closed():
                continue
            s.holders += 1
            waiter.granted_at = self._clock()
            s.granted.append(waiter)
            try:
                waiter.loop.call_soon_threadsafe(self._deliver, waiter)
            except RuntimeError:
                s.holders -= 1
                s.granted.remove(waiter)

    def _deliver(self, waiter: _Waiter) -> None:
        with self._lock:
            if waiter in self._s.granted:
                self._s.granted.remove(waiter)
            reclaimed = waiter.reclaimed
        if reclaimed:
            # Late: the permit went back to the pool, so this waiter has to queue again.
            if not waiter.future.done():
                waiter.future.set_result(False)
        elif waiter.future.done():
            self._release()
        else:
            waiter.future.set_result(True)

    def _release(self) -> None:
        with self._lock:
            self._s.holders = max(0, self._s.holders - 1)
            self._pump_locked()

    async def _acquire(self, progress: RecordProgress | None) -> int | None:
        """Take a permit. Returns the generation the caller first queued in when it had to wait, else ``None``."""
        loop = asyncio.get_running_loop()
        first_generation: int | None = None
        while True:
            with self._lock:
                s = self._s
                width = self.width_locked()
                if first_generation is None and (width is None or (s.holders < width and not s.waiters)):
                    s.holders += 1
                    return None
                first_generation = s.generation if first_generation is None else first_generation
                waiter = _Waiter(loop, loop.create_future(), first_generation)
                s.waiters.append(waiter)
                self._pump_locked()
            async with progress.gate_wait() if progress is not None else contextlib.nullcontext():
                await self._wait_for_grant(waiter)
            if waiter.future.result():
                return first_generation

    async def _wait_for_grant(self, waiter: _Waiter) -> None:
        try:
            while True:
                done, _ = await asyncio.wait({waiter.future}, timeout=GRANT_DELIVERY_SECONDS)
                if done:
                    return
                # Nobody may be releasing: a grant that was never delivered is only found by looking.
                with self._lock:
                    self._reclaim_locked()
                    self._pump_locked()
        except asyncio.CancelledError:
            with self._lock:
                with contextlib.suppress(ValueError):
                    self._s.waiters.remove(waiter)
                granted = waiter.future.done() and not waiter.future.cancelled() and waiter.future.result()
            waiter.future.cancel()
            if granted:
                self._release()
            raise

    # ---- the hung or down server ------------------------------------------------------------------------------------------------
    async def _check_server(self, generation: int, attempt_timeout: float) -> str | None:
        try:
            health = await self._fetch(self.health_url, HEALTH_TIMEOUT_SECONDS)
            age = _oldest_in_use_age(health)
            why = f"the oldest encode has held a slot for {age:.0f}s" if age > STUCK_AGE_ATTEMPTS * attempt_timeout else None
        except Exception as exc:
            why = f"the embedding server's health check failed ({type(exc).__name__})"
        with self._lock:
            self._s.verdict = _Verdict(generation, why, self._clock())
        return why

    async def _failfast_if_server_stuck(self, queued_in: int, attempt_timeout: float, service_name: str) -> None:
        """A waiter that queued before a holder failed asks the server whether anything is moving.

        Fails when the server is unreachable or answers with a server error (down), or when an encode
        has held a slot for longer than ``STUCK_AGE_ATTEMPTS`` attempts (hung). A slow server whose
        encodes finish is left alone: its oldest encode is younger than that. One answer serves every
        waiter of a generation, so the checks do not queue up one behind another while a permit is held.
        """
        with self._lock:
            generation = self._s.generation
            if queued_in >= generation:
                return
            verdict = self._s.verdict
            fresh = (
                verdict is not None
                and verdict.generation == generation
                and (verdict.why is not None or self._clock() - verdict.at < VERDICT_FRESH_SECONDS)
            )
        if fresh:
            why = verdict.why  # type: ignore[union-attr]
        else:
            why = await self._verdict_flight.run(lambda: self._check_server(generation, attempt_timeout))
        if why is None:
            return
        with self._lock:
            first = self._s.reported_generation != generation
            self._s.reported_generation = generation
        if first:
            # Once per failure, not once per waiter: the governor would read a queue of 24 as 24 timeouts.
            get_default_downstream_feedback().report_timeout(service_name)
        raise GateFailFast(f"embedding server is not making progress: {why}")

    @contextlib.asynccontextmanager
    async def slot(
        self, progress: RecordProgress | None, *, attempt_timeout: float, service_name: str
    ) -> AsyncIterator[None]:
        """Hold one bulk permit for the duration of the block. No gate (width unknown or unlimited) holds none."""
        if await self._current_width() is None:
            yield
            return
        queued_in = await self._acquire(progress)
        try:
            if queued_in is not None:
                await self._failfast_if_server_stuck(queued_in, attempt_timeout, service_name)
            try:
                yield
            except GateFailFast:
                raise
            except Exception as exc:
                if is_retriable_embedding_error(exc):
                    with self._lock:
                        self._s.generation += 1
                raise
        finally:
            self._release()


_gates: dict[str, BulkGate] = {}
_gates_lock = threading.Lock()


def get_bulk_gate(base_url: str) -> BulkGate:
    """The gate for one server. Per server rather than per process: two servers do not share slots."""
    with _gates_lock:
        gate = _gates.get(base_url)
        if gate is None:
            gate = _gates[base_url] = BulkGate(base_url)
        return gate
