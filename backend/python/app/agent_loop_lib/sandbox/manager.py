from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.agent_loop_lib.core.exceptions import AgentLoopError

if TYPE_CHECKING:
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
    "SandboxLifecycle",
]

logger = logging.getLogger(__name__)


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
    `max_concurrent` for its type."""


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


class SandboxManager:
    """Tracks `(SandboxType, sandbox_id) -> backend` and owns the full
    lifecycle: creation via registered factories, concurrency/lifetime
    limits, and teardown."""

    def __init__(self, *, governor: "SandboxResourceGovernor | None" = None) -> None:
        self._factories: dict[SandboxType, _FactoryEntry] = {}
        self._instances: dict[tuple[SandboxType, str], _InstanceRecord] = {}
        self._governor = governor
        self._lock = asyncio.Lock()
        # Creations that have reserved a slot but not yet finished
        # provisioning. `_instances` alone cannot bound concurrency: a
        # backend is only recorded there once `provision()` returns, and
        # provisioning is exactly where the awaits are, so N callers would
        # otherwise all see an empty map and all proceed.
        self._in_flight: dict[SandboxType, int] = {}

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

    async def get_or_create(
        self,
        type: SandboxType,
        sandbox_id: str | None = None,
        *,
        ctx: "SandboxContext | None" = None,
    ) -> tuple[str, Any]:
        """Resolve an existing sandbox by id, or create a new one when
        `sandbox_id` is `None`. Raises `UnknownSandboxError` for an
        unrecognized id, `SandboxLimitExceeded` if creating a new one would
        exceed `max_concurrent`, and `ValueError` if no factory is
        registered for `type`.

        Both the per-manager `max_concurrent` and the process-wide governor
        slot are reserved under `self._lock` BEFORE the provision await, and
        given back if provision fails. Checking either against
        `self._instances` alone would be a TOCTOU: a backend only lands
        there after `provision()` returns, and provisioning is where the
        awaits are, so parallel tool calls would all pass an empty check."""
        if sandbox_id is not None:
            record = self._instances.get((type, sandbox_id))
            if record is None:
                raise UnknownSandboxError(f"no {type.value!r} sandbox with id {sandbox_id!r}")
            record.last_used_at = time.monotonic()
            return sandbox_id, record.backend

        entry = self._factories.get(type)
        if entry is None:
            raise ValueError(f"no backend factory registered for sandbox type {type.value!r}")

        effective_ctx = ctx or entry.default_ctx
        lease = None
        async with self._lock:
            await self._sweep_stale(type, entry.limits)
            self._check_concurrency(type, entry.limits)

            if self._governor is not None:
                org_id = effective_ctx.org_id if effective_ctx else None
                lease = await self._governor.acquire(org_id=org_id)

            try:
                backend = self._create_backend(entry, effective_ctx)
            except Exception:
                await self._release_lease(lease)
                raise
            self._in_flight[type] = self._in_flight.get(type, 0) + 1

        started = time.monotonic()
        try:
            backend = await self._provision(backend, entry.limits, type)
        except Exception:
            await self._release_lease(lease)
            raise
        finally:
            self._in_flight[type] = max(0, self._in_flight.get(type, 0) - 1)

        new_id = backend.sandbox_id or str(uuid.uuid4())
        now = time.monotonic()
        record = _InstanceRecord(
            backend=backend, created_at=now, last_used_at=now,
            governor_lease=lease,
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
        await self._safe_destroy(record.backend)
        await self._release_lease(record.governor_lease)
        for other_key in [k for k, v in self._instances.items() if v is record]:
            del self._instances[other_key]

    async def destroy_all(self) -> None:
        """Tear down every tracked sandbox — called from `ControlPlane.stop()`.
        Never raises; failures are logged and swallowed so one broken
        backend can't block the rest of shutdown."""
        seen: set[int] = set()
        for record in list(self._instances.values()):
            if id(record) in seen:
                continue
            seen.add(id(record))
            await self._safe_destroy(record.backend)
            await self._release_lease(record.governor_lease)
        self._instances.clear()

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

    def _check_concurrency(self, type: SandboxType, limits: SandboxLimits) -> None:
        if limits.max_concurrent is None:
            return
        in_use = self.active_count(type) + self._in_flight.get(type, 0)
        if in_use >= limits.max_concurrent:
            raise SandboxLimitExceeded(
                f"max_concurrent={limits.max_concurrent} reached for sandbox "
                f"type {type.value!r}; retry once a sandbox is released"
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
        seen: set[int] = set()
        for key in stale_keys:
            record = self._instances.get(key)
            if record is None or id(record) in seen:
                continue
            seen.add(id(record))
            await self._safe_destroy(record.backend)
            await self._release_lease(record.governor_lease)
            for other_key in [k for k, v in self._instances.items() if v is record]:
                del self._instances[other_key]

    async def _safe_destroy(self, backend: SandboxLifecycle) -> None:
        """Never raises: one wedged backend must not block the teardown of
        the rest, and teardown runs on paths (request abort, shutdown) that
        have nowhere useful to propagate an error to."""
        try:
            await backend.destroy()
        except Exception:
            logger.warning("sandbox teardown failed for %r", backend, exc_info=True)
