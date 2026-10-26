"""Group membership for the person a model call is made for.

OSS ships only the protocol and a no-op resolver; an edition that captures IdP
groups provides the real one through the ``get_group_membership_resolver``
edition hook.

Resolution is asynchronous and can be slow, while the forwarding hook runs on
the request path. ``GroupsHandle`` bridges the two: the boundary starts a
prefetch next to retrieval, and the hook waits for what is left of a small
budget, then goes on with an incomplete result instead of holding the request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.services.model_identity import metrics
from app.services.model_identity.principal import Principal, service_principal

logger = logging.getLogger(__name__)

DEFAULT_WAIT_BUDGET_SECONDS = 0.3


@dataclass(frozen=True, slots=True)
class GroupMembership:
    """Group object ids, with where they came from and whether they are all of them.

    ``complete`` is False when the list was truncated, timed out or could only
    be read partly. A gateway can then treat a missing group as "unknown" and
    not as "not a member".
    """

    ids: tuple[str, ...] = ()
    source: str = "none"
    synced_at: float | None = None
    complete: bool = True


EMPTY_MEMBERSHIP = GroupMembership()


@runtime_checkable
class GroupMembershipResolver(Protocol):
    async def resolve(self, org_id: str, subject: Principal) -> GroupMembership: ...


class NoopGroupResolver:
    """Knows no groups. The OSS default."""

    async def resolve(self, org_id: str, subject: Principal) -> GroupMembership:
        return EMPTY_MEMBERSHIP


def _degraded(source: str) -> GroupMembership:
    return GroupMembership(ids=(), source=source, complete=False)


class GroupsHandle:
    """One lazy, shared resolution of a principal's groups.

    Cheap to create and safe to share between the tasks of one request: the
    underlying lookup runs once however many callers wait on it.
    """

    def __init__(
        self,
        resolver: GroupMembershipResolver,
        principal: Principal,
        *,
        budget_seconds: float = DEFAULT_WAIT_BUDGET_SECONDS,
        preset: GroupMembership | None = None,
    ) -> None:
        self._resolver = resolver
        self._principal = principal
        self._budget = budget_seconds
        self._preset = preset
        self._task: "asyncio.Task[GroupMembership]" | None = None
        self._started_at: float | None = None
        self._timed_out = False

    @classmethod
    def resolved(cls, membership: GroupMembership) -> "GroupsHandle":
        """A handle whose answer is already known (tests, cached results)."""
        return cls(NoopGroupResolver(), service_principal(), preset=membership)

    def prefetch(self) -> None:
        """Start the lookup now. A no-op when already started or no loop runs."""
        if self._task is not None or self._preset is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._started_at = time.monotonic()
        self._task = loop.create_task(self._run())

    async def _run(self) -> GroupMembership:
        try:
            return await self._resolver.resolve(self._principal.org_id, self._principal)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("group resolution failed", exc_info=True)
            return _degraded("error")

    async def wait(self, budget_seconds: float | None = None) -> GroupMembership:
        """The groups, waiting at most ``budget_seconds`` (default: the handle's).

        Never raises: a slow or failing resolver gives an incomplete, empty
        membership, so a request is never failed by a group lookup.
        """
        if self._preset is not None:
            return self._preset
        self.prefetch()
        task = self._task
        if task is None:
            return _degraded("unavailable")
        if self._timed_out:
            # One slow lookup must not cost every later call of the request the
            # budget again: use the answer if it has arrived, else go without.
            return self.result_nowait() or _degraded("timeout")
        budget = self._budget if budget_seconds is None else budget_seconds
        try:
            result = await asyncio.wait_for(asyncio.shield(task), timeout=max(budget, 0.0))
        except asyncio.TimeoutError:
            self._timed_out = True
            metrics.inc(metrics.IDENTITY_MISSING, "groups_timeout")
            return _degraded("timeout")
        except asyncio.CancelledError:
            raise
        except Exception:
            return _degraded("error")
        self._record_duration()
        return result

    def result_nowait(self) -> GroupMembership | None:
        """The groups if already known, else None. For synchronous hooks."""
        if self._preset is not None:
            return self._preset
        task = self._task
        if task is None or not task.done() or task.cancelled():
            return None
        if task.exception() is not None:
            return _degraded("error")
        return task.result()

    def _record_duration(self) -> None:
        if self._started_at is not None:
            metrics.observe(
                metrics.GROUP_RESOLUTION_SECONDS,
                value=time.monotonic() - self._started_at,
            )
            self._started_at = None
