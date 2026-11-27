from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar, runtime_checkable

from app.agent_loop_lib.core.exceptions import AgentLoopError
from app.agent_loop_lib.core.tasks import wait_owned
from app.core.org_context import spawn_in_request

if TYPE_CHECKING:
    from app.agent_loop_lib.core.context import CancellationToken
    from app.agent_loop_lib.sandbox.capacity import HoldReason
    from app.agent_loop_lib.sandbox.coding.base import SandboxContext, SandboxRef
    from app.agent_loop_lib.sandbox.coding.registry import SandboxBackendFactory
    from app.agent_loop_lib.sandbox.governor import SandboxResourceGovernor

"""`SandboxManager`: one generic, type-aware manager for the whole sandbox
taxonomy (coding/os/db/browser), formalizing what was previously four
independently-wired sandboxes in `ControlPlane`.

Phase 1 hardening (this revision):
- ``asyncio.Lock`` around check+reserve in ``get_or_create`` (TOCTOU fix)
- ``SandboxResourceGovernor`` integration (process-wide limits)
- ``register_backend(factory, ...)`` alongside the legacy ``register_backend_factory``
- ``SandboxContext`` threading through ``get_or_create``
- ``refs()`` for future session persistence
- ``asyncio.wait_for`` provision timeout
- ``SandboxLifecycle`` protocol to replace duck-typing

Scope of this implementation: only `SandboxType.CODING` is actually routed
through this manager (backed by `LocalCodingSandbox` — see
`sandbox/coding/`). The existing os/db/browser sandboxes keep their
pre-existing direct `ControlPlane` wiring untouched; migrating them onto
this manager is documented follow-up work, not part of this change. The
type system and `capabilities` mechanism below are still real (not
speculative dead code) — they're what lets a *future* all-in-one remote
backend (E2B/Daytona/AIO Sandbox: one VM serving shell + code + browser +
files) register ONE factory across multiple `SandboxType`s and have every
type resolve to the SAME shared instance instead of provisioning one VM
per type.
"""

__all__ = [
    "SandboxType",
    "SandboxLimits",
    "SandboxManager",
    "SandboxManagerError",
    "UnknownSandboxError",
    "SandboxLimitExceeded",
    "SandboxCallStopped",
    "SandboxLifecycle",
]

logger = logging.getLogger(__name__)

T = TypeVar("T")

# How long a stopped call waits for the backend to clean up after its cancel:
# the Docker backend's own 30 s bound for removing the container, plus a margin.
_STOP_DRAIN_S = 35.0
# Longest the slot-wait note may take to show or clear: a stalled event stream must
# neither stretch the slot wait nor hold a granted slot.
_SLOT_NOTE_S = 5.0


class SandboxType(str, Enum):
    CODING = "coding"
    OS = "os"
    DB = "db"
    BROWSER = "browser"


class SandboxManagerError(AgentLoopError):
    """Base for `SandboxManager` infrastructure failures."""


class UnknownSandboxError(SandboxManagerError):
    """Raised by `get()`/`get_or_create(sandbox_id=...)` for an id the
    manager has no record of (never created, or already destroyed)."""


class SandboxLimitExceeded(SandboxManagerError):
    """Raised when creating a new sandbox would exceed the configured
    `max_concurrent` for its type, or no governor slot came free in time.
    `reason` is the governor's `HoldReason` (`None` for `max_concurrent`)."""

    def __init__(self, *args: object, reason: "HoldReason | None" = None) -> None:
        super().__init__(*args)
        self.reason = reason


class SandboxCallStopped(SandboxManagerError):
    """The run was stopped by its user while this sandbox call was in flight."""


class _RequestSlots:
    """The governor slots one request's calls hold, and its calls waiting
    for a turn, oldest first. A turn given back passes straight to the
    oldest waiter, so a newer call cannot overtake it."""

    def __init__(self) -> None:
        self.held = 0
        self._waiters: deque[asyncio.Future[None]] = deque()

    async def take(
        self, limit: int, wait_s: float, on_wait: Callable[[], Awaitable[None]],
    ) -> None:
        """One turn, waiting up to *wait_s* (NaN or negative: not at all);
        `SandboxLimitExceeded` past it."""
        if self.held < limit and not self._waiters:
            self.held += 1
            return
        if not wait_s > 0:
            raise self._refusal(limit)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(future)
        deadline = time.monotonic() + wait_s
        try:
            try:
                await on_wait()
            except Exception:
                logger.debug("sandbox manager: on_wait callback failed", exc_info=True)
            # A turn handed over by an earlier `give_back`, or one freed since
            # the limit was read (it can only have risen).
            while not future.done():
                if self.held < limit and self._waiters and self._waiters[0] is future:
                    self._waiters.popleft()
                    self.held += 1
                    future.set_result(None)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.wait({future}, timeout=None if math.isinf(remaining) else remaining)
        except BaseException:
            self._leave(future)
            raise
        if not future.done():
            self._leave(future)
            raise self._refusal(limit)

    def _leave(self, future: asyncio.Future[None]) -> None:
        """*future* gives up its place; a turn it was already handed passes on."""
        if future.done() and not future.cancelled():
            self.give_back()
            return
        future.cancel()
        with contextlib.suppress(ValueError):
            self._waiters.remove(future)

    def give_back(self) -> None:
        while self._waiters:
            future = self._waiters.popleft()
            if not future.done():
                future.set_result(None)  # the turn moves on; `held` is unchanged
                return
        self.held = max(0, self.held - 1)

    @staticmethod
    def _refusal(limit: int) -> SandboxLimitExceeded:
        if limit == 1:
            text = "this chat already has a code run going, the most one request may run at once; retry once it ends"
        else:
            text = (
                f"this chat already has {limit} code runs going at once, the most one request "
                f"may run; retry once one of them ends"
            )
        return SandboxLimitExceeded(text)


class _RequestLease:
    """A governor lease that also gives back its request's turn, once."""

    def __init__(self, lease: Any, slots: _RequestSlots) -> None:
        self._lease = lease
        self._slots: _RequestSlots | None = slots

    def __getattr__(self, name: str) -> Any:
        return getattr(self._lease, name)

    async def release(self) -> None:
        slots, self._slots = self._slots, None
        try:
            await self._lease.release()
        finally:
            if slots is not None:
                slots.give_back()


@runtime_checkable
class SandboxLifecycle(Protocol):
    """Minimal protocol for anything the manager tracks. Everything the
    manager needs from a backend is here, so os/db/browser sandboxes can
    migrate onto this manager without it growing coding-specific probes."""

    @property
    def sandbox_id(self) -> str: ...
    async def provision(self) -> Any: ...
    async def destroy(self) -> None: ...


def type_name(exc: BaseException) -> str:
    return type(exc).__name__


@dataclass(frozen=True)
class SandboxLimits:
    """Per-`SandboxType` limits, enforced HERE (not in middleware) because
    they need manager state (how many are currently live, how long ago
    each was created) that middleware doesn't have access to."""

    max_concurrent: int | None = None
    max_lifetime_s: float | None = None
    provision_timeout_s: float | None = 60.0
    # The most seconds one code run may take. Applied by the run_code tool
    # (see `SandboxManager.run_timeout_cap`); `None` means no cap.
    max_run_timeout_s: float | None = None


@dataclass
class _FactoryEntry:
    factory: Callable[[], Any] | None
    backend_factory: Any | None  # SandboxBackendFactory when using registry path
    limits: SandboxLimits
    capabilities: set[SandboxType]
    # Context registered alongside the backend. The tools call
    # `get_or_create(type, sandbox_id)` with no context of their own, so
    # without this the governor would never see an org and `max_per_org`
    # could never fire.
    default_ctx: "SandboxContext | None" = None


@dataclass
class _InstanceRecord:
    backend: Any
    created_at: float
    last_used_at: float
    governor_lease: Any | None = None  # GovernorLease
    # Calls running in it now (`acquire`/`release`): one in use is never evicted to make room. Only a sandbox
    # created through `acquire` may be evicted: a `get_or_create` caller says nothing about when it is done.
    busy: int = 0
    evictable: bool = False
    org_id: str | None = None


def _holds_slot_for_life(backend: Any) -> bool:
    """A metered backend (a remote VM) costs for as long as it lives, so it
    keeps its governor slot from creation to destroy. Any other backend
    only uses resources while a call runs (Docker starts a container per
    call), so it takes a slot per call in `SandboxManager.slot()`."""
    caps = getattr(backend, "capabilities", None)
    return getattr(caps, "is_metered", True)


class SandboxManager:
    """Tracks `(SandboxType, sandbox_id) -> backend` and owns the full
    lifecycle: creation via registered factories, concurrency/lifetime
    limits, and teardown."""

    def __init__(
        self,
        *,
        governor: "SandboxResourceGovernor | None" = None,
        slot_wait_s: float = 0.0,
        on_slot_wait: Callable[..., Awaitable[None]] | None = None,
        stop_token: "CancellationToken | None" = None,
        max_slots_per_request: int | Literal["env"] | None = None,
    ) -> None:
        self._factories: dict[SandboxType, _FactoryEntry] = {}
        self._instances: dict[tuple[SandboxType, str], _InstanceRecord] = {}
        self._governor = governor
        # How long a call waits for a free governor slot before it fails, and
        # what it runs as (tool name, waiting) when it starts waiting and when
        # the wait ends (e.g. show and clear a note for the user).
        self._slot_wait_s = slot_wait_s
        self._on_slot_wait = on_slot_wait
        # The run's Stop signal. Calls made through `unless_stopped` end as
        # soon as it is set, instead of running to completion.
        self._stop_token = stop_token
        self._lock = asyncio.Lock()
        # Creations that have reserved a slot but not yet finished
        # provisioning. `_instances` alone cannot bound concurrency: a
        # backend is only recorded there once `provision()` returns, and
        # provisioning is exactly where the awaits are, so N callers would
        # otherwise all see an empty map and all proceed.
        self._in_flight: dict[SandboxType, int] = {}
        # The most governor slots this manager's calls hold at once (`slot()`),
        # so one chat's parallel calls cannot take every slot of the
        # container. Opt-in, because it counts per manager: set it only where
        # a manager serves one request (and its sub-agents), as the chat path
        # does. A manager shared by many requests (the library's
        # `ControlPlane`) would otherwise cap the whole server. `None`: no
        # such limit; ``"env"`` (or an int <= 0): SANDBOX_MAX_PER_REQUEST,
        # read on each call.
        self._max_slots_per_request = max_slots_per_request
        self._request_slots = _RequestSlots()

    def register_backend_factory(
        self,
        type: SandboxType,
        factory: Callable[[], Any],
        *,
        limits: SandboxLimits | None = None,
        capabilities: set[SandboxType] | None = None,
    ) -> None:
        """Register `factory` (a zero-arg callable producing a fresh backend
        instance) for `type`. `capabilities`, when given, registers the SAME
        factory for every type in the set — `get_or_create` will then route
        all of those types to one shared backend instance per `sandbox_id`
        (the all-in-one remote backend case). Defaults to `{type}` (the
        common one-factory-per-type case used by this implementation's
        single `SandboxType.CODING` registration).
        """
        entry = _FactoryEntry(
            factory=factory,
            backend_factory=None,
            limits=limits or SandboxLimits(),
            capabilities=capabilities or {type},
        )
        for t in entry.capabilities:
            self._factories[t] = entry

    def register_backend(
        self,
        type: SandboxType,
        backend_factory: "SandboxBackendFactory",
        *,
        limits: SandboxLimits | None = None,
        capabilities: set[SandboxType] | None = None,
        ctx: "SandboxContext | None" = None,
    ) -> None:
        """Registry-aware registration: uses ``SandboxBackendFactory.create(ctx)``
        instead of a zero-arg callable."""
        entry = _FactoryEntry(
            factory=None,
            backend_factory=backend_factory,
            limits=limits or SandboxLimits(),
            capabilities=capabilities or {type},
            default_ctx=ctx,
        )
        for t in entry.capabilities:
            self._factories[t] = entry

    def is_registered(self, type: SandboxType) -> bool:
        return type in self._factories

    def reaches_network(self, type: SandboxType) -> bool:
        """Whether code in ``type``'s registered backend can reach the network whatever it was asked for (its
        ``capabilities().supports_network``: a host subprocess always can). False when nothing is registered yet or a
        zero-arg factory is: neither declares capabilities, and with nothing registered no code runs."""
        entry = self._factories.get(type)
        if entry is None or entry.backend_factory is None:
            return False
        return bool(entry.backend_factory.capabilities().supports_network)

    async def acquire(
        self,
        type: SandboxType,
        sandbox_id: str | None = None,
        *,
        ctx: "SandboxContext | None" = None,
        tool: str = "run_code",
    ) -> tuple[str, Any]:
        """`get_or_create`, held for the caller until `release`: a held sandbox is never evicted to make room."""
        return await self.get_or_create(type, sandbox_id, ctx=ctx, hold=True, tool=tool)

    def release(self, type: SandboxType, sandbox_id: str) -> None:
        record = self._instances.get((type, sandbox_id))
        if record is not None:
            record.busy = max(0, record.busy - 1)
            record.last_used_at = time.monotonic()

    def run_timeout_cap(self, type: SandboxType) -> float | None:
        """The most seconds one run on a `type` sandbox may take, from the
        registered limits, or `None` when unregistered or uncapped."""
        entry = self._factories.get(type)
        return entry.limits.max_run_timeout_s if entry is not None else None

    async def get_or_create(
        self,
        type: SandboxType,
        sandbox_id: str | None = None,
        *,
        ctx: "SandboxContext | None" = None,
        hold: bool = False,
        tool: str = "run_code",
    ) -> tuple[str, Any]:
        """Resolve an existing sandbox by id, or create a new one when
        `sandbox_id` is `None`. Raises `UnknownSandboxError` for an
        unrecognized id, `SandboxLimitExceeded` if creating a new one would
        exceed `max_concurrent`, and `ValueError` if no factory is
        registered for `type`.

        The per-manager `max_concurrent` is reserved under `self._lock` and a
        metered backend's governor slot (waited for outside it, shown as *tool*
        waiting) BEFORE the provision await; both are given back if provision fails. Checking either against
        `self._instances` alone would be a TOCTOU: a backend only lands
        there after `provision()` returns, and provisioning is where the
        awaits are, so parallel tool calls would all pass an empty check."""
        if sandbox_id is not None:
            record = self._instances.get((type, sandbox_id))
            if record is None:
                raise UnknownSandboxError(f"no {type.value!r} sandbox with id {sandbox_id!r}")
            record.last_used_at = time.monotonic()
            if hold:
                record.busy += 1
            return sandbox_id, record.backend

        entry = self._factories.get(type)
        if entry is None:
            raise ValueError(f"no backend factory registered for sandbox type {type.value!r}")

        effective_ctx = ctx or entry.default_ctx
        org_id = effective_ctx.org_id if effective_ctx else None
        lease = None
        async with self._lock:
            await self._sweep_stale(type, entry.limits)
            await self._evict_idle(type, entry.limits)
            self._check_concurrency(type, entry.limits)

            backend = self._create_backend(entry, effective_ctx)
            self._in_flight[type] = self._in_flight.get(type, 0) + 1

        try:
            # Outside the manager lock: a creation waiting for a slot must not
            # hold up this chat's other sandbox calls.
            if self._governor is not None and _holds_slot_for_life(backend):
                lease = await self._acquire_slot(org_id, tool)
            started = time.monotonic()
            backend = await self._provision(backend, entry.limits, type)
        except asyncio.CancelledError:
            # A cancel during the slot wait or the provision would otherwise
            # leak the half-made backend (a remote VM keeps running) and its slot.
            # One shielded task, so a second cancel during the destroy cannot
            # skip the release.
            await asyncio.shield(self._destroy_then_release(backend, lease))
            raise
        except Exception:
            await self._release_lease(lease)
            raise
        finally:
            self._in_flight[type] = max(0, self._in_flight.get(type, 0) - 1)

        new_id = backend.sandbox_id or str(uuid.uuid4())
        now = time.monotonic()
        record = _InstanceRecord(
            backend=backend, created_at=now, last_used_at=now,
            governor_lease=lease, busy=1 if hold else 0, evictable=hold, org_id=org_id,
        )
        for t in entry.capabilities:
            self._instances[(t, new_id)] = record

        logger.info(
            "sandbox created: type=%s id=%s backend=%s org_id=%s "
            "conversation_id=%s duration_ms=%.1f outcome=ok",
            type.value, new_id, backend.__class__.__name__,
            effective_ctx.org_id if effective_ctx else None,
            effective_ctx.conversation_id if effective_ctx else None,
            (now - started) * 1000,
        )
        return new_id, backend

    async def _provision(
        self, backend: SandboxLifecycle, limits: SandboxLimits, type: SandboxType,
    ) -> SandboxLifecycle:
        """Provision within `provision_timeout_s`, tearing the backend down
        on any failure.

        A timed-out provision has usually already created the remote
        resource (the container is running, the micro-VM is billing); the
        call just hasn't returned. Dropping the object without `destroy()`
        leaks it for as long as its own TTL allows — which for a backend
        with no provider-side TTL is forever.
        """
        timeout = limits.provision_timeout_s
        try:
            if timeout is not None:
                await asyncio.wait_for(backend.provision(), timeout=timeout)
            else:
                await backend.provision()
            return backend
        except (asyncio.TimeoutError, TimeoutError) as exc:
            await self._safe_destroy(backend)
            raise SandboxManagerError(
                f"sandbox provision for type {type.value!r} exceeded "
                f"provision_timeout_s={timeout}"
            ) from exc
        except Exception as exc:
            await self._safe_destroy(backend)
            raise SandboxManagerError(
                f"sandbox provision failed for type {type.value!r}: "
                f"{type_name(exc)}: {exc}"
            ) from exc

    @asynccontextmanager
    async def slot(self, type: SandboxType, sandbox_id: str, *, tool: str = "run_code") -> AsyncIterator[None]:
        """Hold a governor slot while one call (of tool *tool*) runs on the sandbox.

        Waits for a free slot (see `slot_wait_s`), then raises
        `SandboxLimitExceeded`. With a per-request limit, this manager's
        calls hold at most `request_slot_limit()` slots at once; its other
        calls wait for one of those first, within the same `slot_wait_s`. A no-op for a sandbox that keeps its slot for
        life (metered backends), an unknown id, or a manager with no governor.

        The slot is released when the body returns or raises. So a backend
        must not return or raise from a cancelled call while its compute (a
        container, a subprocess) is still running: it stops that first, and
        only then lets the cancel through.
        """
        record = self._instances.get((type, sandbox_id))
        if record is None or self._governor is None or record.governor_lease is not None:
            yield
            return
        lease = await self._acquire_slot(
            record.org_id, tool, per_request=self._max_slots_per_request is not None,
        )
        try:
            yield
        finally:
            # Shielded: a cancel landing while the release waits for the
            # governor's lock must not leave the slot counted.
            await asyncio.shield(self._release_lease(lease))

    def request_slot_limit(self) -> int | None:
        """The most slots this request's calls hold at once: the setting,
        never above the governor's ceiling. ``None`` when this manager has
        no per-request limit (it may be shared by many requests)."""
        limit = self._max_slots_per_request
        if limit is None:
            return None
        if not isinstance(limit, int) or limit <= 0:
            from app.agent_loop_lib.sandbox.capacity import max_per_request

            limit = max_per_request()
        capacity = getattr(self._governor, "capacity", None)
        ceiling = capacity.ceiling() if capacity is not None else None
        return max(1, min(limit, ceiling) if isinstance(ceiling, int) else limit)

    async def _acquire_slot(self, org_id: str | None, tool: str, *, per_request: bool = False) -> Any:
        """A governor slot, waiting up to `slot_wait_s`; the wait note is
        shown when the wait starts and cleared once the slot is granted.

        With *per_request*, the call first takes one of this request's
        `request_slot_limit()` turns, waiting for it in arrival order. That
        wait holds no governor slot, so it costs other chats nothing, and it
        counts against the same `slot_wait_s`; past it the call is refused
        like a full governor."""
        notify = self._on_slot_wait
        shown = False

        async def on_wait() -> None:
            nonlocal shown
            if shown:
                return
            shown = True
            if notify is not None:
                try:
                    await asyncio.wait_for(notify(tool, waiting=True), _SLOT_NOTE_S)
                except TimeoutError:
                    logger.warning("showing the slot-wait note took over %gs; going on", _SLOT_NOTE_S)

        lease = None
        turn = False
        try:
            wait_s = self._slot_wait_s
            if per_request:
                started = time.monotonic()
                await self._request_slots.take(self.request_slot_limit() or 1, wait_s, on_wait)
                turn = True
                if wait_s >= 0:
                    wait_s = max(0.0, wait_s - (time.monotonic() - started))
            lease = await self._governor.acquire(org_id=org_id, wait_s=wait_s, on_wait=on_wait)
            if turn:
                lease = _RequestLease(lease, self._request_slots)
            return lease
        except BaseException:
            if turn:
                self._request_slots.give_back()
            raise
        finally:
            # Granted, timed out or cancelled: the note is taken down either way.
            if shown and notify is not None:
                try:
                    # Bounded: a stalled event stream must not hold a granted slot.
                    await asyncio.wait_for(notify(tool, waiting=False), _SLOT_NOTE_S)
                except TimeoutError:
                    logger.warning("clearing the slot-wait note took over %gs; going on", _SLOT_NOTE_S)
                except asyncio.CancelledError:
                    # The slot was granted but the caller will never see it.
                    if lease is not None:
                        await asyncio.shield(self._release_lease(lease))
                    raise
                except Exception:
                    logger.warning("clearing the slot-wait note failed", exc_info=True)

    async def unless_stopped(self, fn: Callable[[], Awaitable[T]], *, tool: str) -> T:
        """Run `fn()` until it finishes or the run is stopped, whichever is first.

        On Stop, `fn` gets one asyncio cancel. Backends turn that into
        "stop the container or process, release the slot, re-raise", and
        this waits for that cleanup (see `_drain`) before raising
        `SandboxCallStopped`. If `fn` finished in the same tick as the Stop,
        its real result (or exception) wins. A Stop that was already set
        means `fn` is never started. A cancel of the calling task itself
        (a disconnect, shutdown) still cancels `fn` and raises
        `CancelledError`, as before.

        `fn` is a callable rather than a coroutine so that a call that never
        starts leaves no un-awaited coroutine behind.
        """
        token = self._stop_token
        if token is None:
            return await fn()
        if token.is_cancelled:
            raise SandboxCallStopped(tool)
        async def call_fn() -> T:
            return await fn()

        call = spawn_in_request(call_fn())
        stop = spawn_in_request(token.wait())
        try:
            await wait_owned({call, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop.cancel()
        if call.done():
            return call.result()
        loop = asyncio.get_running_loop()
        started = loop.time()
        call.cancel()
        await self._drain(call)
        logger.info(
            "sandbox call stopped by the user: tool=%s cleanup_s=%.1f", tool, loop.time() - started,
        )
        raise SandboxCallStopped(tool)

    @staticmethod
    async def _drain(call: asyncio.Future[Any]) -> None:
        """Wait for a cancelled call to finish its cleanup, at most
        `_STOP_DRAIN_S`. A second cancel of this task is remembered and
        re-raised at the end: it must not let the tool return while the
        call still holds its slot."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _STOP_DRAIN_S
        cancelled_again = False
        while not call.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.warning("sandbox call did not finish within %gs of Stop", _STOP_DRAIN_S)
                break
            try:
                await asyncio.wait({call}, timeout=remaining)
            except asyncio.CancelledError:
                cancelled_again = True
        if call.done():
            if not call.cancelled() and call.exception() is not None:
                logger.debug("stopped sandbox call ended with an error", exc_info=call.exception())
        else:
            call.add_done_callback(lambda f: f.cancelled() or f.exception())
        if cancelled_again:
            raise asyncio.CancelledError

    async def _release_lease(self, lease: Any | None) -> None:
        if lease is None:
            return
        try:
            await lease.release()
        except Exception:
            logger.warning("governor lease release failed", exc_info=True)

    def _create_backend(
        self, entry: _FactoryEntry, ctx: "SandboxContext | None",
    ) -> SandboxLifecycle:
        if entry.backend_factory is not None:
            from app.agent_loop_lib.sandbox.coding.base import SandboxContext as _SC

            return entry.backend_factory.create(ctx or _SC())
        elif entry.factory is not None:
            return entry.factory()
        raise ValueError("factory entry has neither factory nor backend_factory")

    def get(self, type: SandboxType, sandbox_id: str) -> Any:
        """Synchronous lookup of an already-created sandbox — raises
        `UnknownSandboxError` if it doesn't exist (never auto-creates)."""
        record = self._instances.get((type, sandbox_id))
        if record is None:
            raise UnknownSandboxError(f"no {type.value!r} sandbox with id {sandbox_id!r}")
        return record.backend

    async def destroy(self, type: SandboxType, sandbox_id: str) -> None:
        """Idempotent — no-op if the id doesn't exist."""
        key = (type, sandbox_id)
        record = self._instances.pop(key, None)
        if record is None:
            return
        for other_key in [k for k, v in self._instances.items() if v is record]:
            del self._instances[other_key]
        await self._teardown([record])

    async def destroy_all(self) -> None:
        """Tear down every tracked sandbox — called from `ControlPlane.stop()`.
        Never raises; failures are logged and swallowed so one broken
        backend can't block the rest of shutdown."""
        records = list({id(r): r for r in self._instances.values()}.values())
        self._instances.clear()
        await self._teardown(records)

    def active_count(self, type: SandboxType) -> int:
        """Number of distinct live backend instances for `type` (dedups
        multi-capability backends tracked under several type keys)."""
        return len({id(v.backend) for k, v in self._instances.items() if k[0] == type})

    def refs(self) -> list["SandboxRef"]:
        """Serializable snapshot of every tracked sandbox — the input a
        Phase 4 session store would persist and later hand to
        `SandboxBackendFactory.reconnect()`.

        Backends that predate `SandboxRef` are skipped rather than
        synthesised: a ref whose `backend` field doesn't name a registered
        factory can't be reconnected, so emitting one would be worse than
        omitting it.
        """
        seen: set[int] = set()
        result: list[SandboxRef] = []
        for record in self._instances.values():
            if id(record) in seen:
                continue
            seen.add(id(record))
            ref = getattr(record.backend, "ref", None)
            if ref is not None:
                result.append(ref)
        return result

    async def _evict_idle(self, type: SandboxType, limits: SandboxLimits) -> None:
        """At the limit, the least recently used sandbox no call is running in makes room (LONGTASK-F5).

        A manager belongs to one request, so every sandbox it tracks is that request's own: without this, a
        request that called `run_code` without a `sandbox_id` more than `max_concurrent` times starved itself,
        its idle sandboxes holding every slot until the request ended."""
        if limits.max_concurrent is None:
            return
        while self.active_count(type) + self._in_flight.get(type, 0) >= limits.max_concurrent:
            idle = {
                id(v): (k, v) for k, v in self._instances.items() if k[0] == type and v.evictable and v.busy == 0
            }
            if not idle:
                return
            key, record = min(idle.values(), key=lambda kv: kv[1].last_used_at)
            logger.info("sandbox evicted: type=%s id=%s reason=idle_at_limit", type.value, key[1])
            await self._safe_destroy(record.backend)
            await self._release_lease(record.governor_lease)
            for other_key in [k for k, v in self._instances.items() if v is record]:
                del self._instances[other_key]

    def _check_concurrency(self, type: SandboxType, limits: SandboxLimits) -> None:
        if limits.max_concurrent is None:
            return
        in_use = self.active_count(type) + self._in_flight.get(type, 0)
        if in_use >= limits.max_concurrent:
            raise SandboxLimitExceeded(
                f"all {limits.max_concurrent} {type.value} sandboxes this request may use are running code "
                f"right now; wait for one of those calls to finish, or pass the sandbox_id of one of them to "
                f"run in it next"
            )

    async def _sweep_stale(self, type: SandboxType, limits: SandboxLimits) -> None:
        """Lazily destroy sandboxes of `type` older than `max_lifetime_s`,
        called on every creation attempt so staleness is enforced without a
        background task."""
        if limits.max_lifetime_s is None:
            return
        now = time.monotonic()
        stale_keys = [
            k for k, v in self._instances.items()
            if k[0] == type and (now - v.created_at) > limits.max_lifetime_s
        ]
        stale = {id(self._instances[k]): self._instances[k] for k in stale_keys}
        for other_key in [k for k, v in self._instances.items() if id(v) in stale]:
            del self._instances[other_key]
        await self._teardown(list(stale.values()))

    async def _teardown(self, records: list[_InstanceRecord]) -> None:
        """Destroy each record's backend, then give back its slot. One
        shielded task: a cancel (a client disconnect cancels the request's
        cleanup) returns at once but the teardown still finishes, so a
        sandbox that keeps its slot for life never leaves it counted."""
        if not records:
            return

        async def run() -> None:
            for record in records:
                await self._destroy_then_release(record.backend, record.governor_lease)

        await asyncio.shield(run())

    async def _destroy_then_release(self, backend: SandboxLifecycle, lease: Any | None) -> None:
        try:
            await self._safe_destroy(backend)
        finally:
            await self._release_lease(lease)

    async def _safe_destroy(self, backend: SandboxLifecycle) -> None:
        """Never raises: one wedged backend must not block the teardown of
        the rest, and teardown runs on paths (request abort, shutdown) that
        have nowhere useful to propagate an error to."""
        try:
            await backend.destroy()
        except Exception:
            logger.warning("sandbox teardown failed for %r", backend, exc_info=True)
