from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.agent_loop_lib.core.exceptions import AgentLoopError

"""`SandboxManager`: one generic, type-aware manager for the whole sandbox
taxonomy (coding/os/db/browser), formalizing what was previously four
independently-wired sandboxes in `ControlPlane`.

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

__all__ = ["SandboxType", "SandboxLimits", "SandboxManager", "SandboxManagerError", "UnknownSandboxError", "SandboxLimitExceeded"]

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


@dataclass(frozen=True)
class SandboxLimits:
    """Per-`SandboxType` limits, enforced HERE (not in middleware) because
    they need manager state (how many are currently live, how long ago
    each was created) that middleware doesn't have access to."""

    max_concurrent: int | None = None
    max_lifetime_s: float | None = None


@dataclass
class _FactoryEntry:
    factory: Callable[[], Any]
    limits: SandboxLimits
    capabilities: set[SandboxType]


@dataclass
class _InstanceRecord:
    backend: Any
    created_at: float
    last_used_at: float


class SandboxManager:
    """Tracks `(SandboxType, sandbox_id) -> backend` and owns the full
    lifecycle: creation via registered factories, concurrency/lifetime
    limits, and teardown."""

    def __init__(self) -> None:
        self._factories: dict[SandboxType, _FactoryEntry] = {}
        self._instances: dict[tuple[SandboxType, str], _InstanceRecord] = {}

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
        entry = _FactoryEntry(factory=factory, limits=limits or SandboxLimits(), capabilities=capabilities or {type})
        for t in entry.capabilities:
            self._factories[t] = entry

    def is_registered(self, type: SandboxType) -> bool:
        return type in self._factories

    async def get_or_create(self, type: SandboxType, sandbox_id: str | None = None) -> tuple[str, Any]:
        """Resolve an existing sandbox by id, or create a new one when
        `sandbox_id` is `None`. Raises `UnknownSandboxError` for an
        unrecognized id, `SandboxLimitExceeded` if creating a new one would
        exceed `max_concurrent`, and `ValueError` if no factory is
        registered for `type`."""
        if sandbox_id is not None:
            record = self._instances.get((type, sandbox_id))
            if record is None:
                raise UnknownSandboxError(f"no {type.value!r} sandbox with id {sandbox_id!r}")
            record.last_used_at = time.monotonic()
            return sandbox_id, record.backend

        entry = self._factories.get(type)
        if entry is None:
            raise ValueError(f"no backend factory registered for sandbox type {type.value!r}")

        await self._sweep_stale(type, entry.limits)
        self._check_concurrency(type, entry.limits)

        backend = entry.factory()
        provision = getattr(backend, "provision", None)
        if callable(provision):
            await provision()

        new_id = getattr(backend, "sandbox_id", None) or str(uuid.uuid4())
        now = time.monotonic()
        record = _InstanceRecord(backend=backend, created_at=now, last_used_at=now)
        for t in entry.capabilities:
            self._instances[(t, new_id)] = record
        return new_id, backend

    def get(self, type: SandboxType, sandbox_id: str) -> Any:
        """Synchronous lookup of an already-created sandbox — raises
        `UnknownSandboxError` if it doesn't exist (never auto-creates)."""
        record = self._instances.get((type, sandbox_id))
        if record is None:
            raise UnknownSandboxError(f"no {type.value!r} sandbox with id {sandbox_id!r}")
        return record.backend

    async def destroy(self, type: SandboxType, sandbox_id: str) -> None:
        """No-op if the id doesn't exist — destroy is idempotent."""
        key = (type, sandbox_id)
        record = self._instances.pop(key, None)
        if record is None:
            return
        await self._safe_destroy(record.backend)
        # A multi-capability backend is tracked under several (type, id)
        # keys pointing at the SAME record — drop all of them together so
        # a later get()/get_or_create() under a sibling type can't resolve
        # an already-destroyed backend.
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
        self._instances.clear()

    def active_count(self, type: SandboxType) -> int:
        """Number of distinct live backend instances for `type` (dedups
        multi-capability backends tracked under several type keys)."""
        return len({id(v.backend) for k, v in self._instances.items() if k[0] == type})

    def _check_concurrency(self, type: SandboxType, limits: SandboxLimits) -> None:
        if limits.max_concurrent is None:
            return
        if self.active_count(type) >= limits.max_concurrent:
            raise SandboxLimitExceeded(
                f"max_concurrent={limits.max_concurrent} reached for sandbox type {type.value!r}"
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
            for other_key in [k for k, v in self._instances.items() if v is record]:
                del self._instances[other_key]

    async def _safe_destroy(self, backend: Any) -> None:
        teardown = getattr(backend, "destroy", None) or getattr(backend, "close", None)
        if not callable(teardown):
            return
        try:
            await teardown()
        except Exception:
            logger.warning("sandbox teardown failed for %r", backend, exc_info=True)
