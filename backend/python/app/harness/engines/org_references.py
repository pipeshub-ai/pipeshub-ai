"""An organisation's references for engine runs (C20.18): an extension point with no default in this build.

What an environment's setup left, kept for the next session, and shared read-only caches a run mounts are references
of the organisation, never part of a session's workspace. Neither is in this build: an environment's setup runs in
every session and a run mounts no shared cache. An edition supplies ``OrgReferencesProvider`` through
``edition_services.harness_edition()``; it is read with ``supplied``, so ``org_references()`` is ``None`` here.

- ``for_attempt``: the two controller ports (``EnvironmentSnapshotter``, ``SharedCacheMounts``) for one attempt, through
  that attempt's own Node route, so an attempt reads only its own organisation's references.
- ``background``: work a worker that runs engines carries beside its runs, or ``None``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app import edition_services

if TYPE_CHECKING:
    from app.harness.engines.worker import EnginePlan
    from app.harness.journal_node import AttemptRoute, NodeJournal
    from harness_core.clock import Clock
    from harness_core.engines.cache_mounts import SharedCacheMounts
    from harness_core.engines.environments import EnvironmentSnapshotter
    from harness_core.workspace.ports import BinaryObjectStore


@dataclass(frozen=True, slots=True)
class AttemptReferences:
    """The controller ports one attempt uses; ``None`` leaves that feature off for it."""

    environment_snapshots: EnvironmentSnapshotter | None = None
    shared_caches: SharedCacheMounts | None = None


NO_ATTEMPT_REFERENCES = AttemptReferences()


class BackgroundJob(Protocol):
    def start(self) -> None: ...

    async def stop(self) -> None: ...


class OrgReferencesProvider(ABC):
    @abstractmethod
    def for_attempt(self, route: AttemptRoute, objects: BinaryObjectStore) -> AttemptReferences:
        """The references of the attempt ``route`` serves; ``objects`` is the deployment's object store."""

    @abstractmethod
    def background(self, plan: EnginePlan, journal: NodeJournal, worker_id: str, clock: Clock) -> BackgroundJob | None:
        """Work the worker runs beside its runs, started after the claimer and stopped with it; ``None``: none."""


def org_references() -> OrgReferencesProvider | None:
    """The edition's provider; ``None`` in this build."""
    return edition_services.harness_edition().supplied(OrgReferencesProvider)


def attempt_references(route: AttemptRoute, objects: BinaryObjectStore) -> AttemptReferences:
    provider = org_references()
    return NO_ATTEMPT_REFERENCES if provider is None else provider.for_attempt(route, objects)


__all__ = [
    "NO_ATTEMPT_REFERENCES",
    "AttemptReferences",
    "BackgroundJob",
    "OrgReferencesProvider",
    "attempt_references",
    "org_references",
]
