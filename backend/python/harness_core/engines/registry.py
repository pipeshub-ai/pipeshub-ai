"""Engine registry: adapters by kind, refused unless the exact version and digest are allowed.

A version the release no longer lists but a successor declares it continues (allowlist ``resumes``, UP-02, ADR-OPS-01
D6) is resumed on that successor: ``resumed_on`` names it, the durable kernel moves the run's pinned profile to it, and
the adapter judges the successor's binary, never the old digest the run was pinned with."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from harness_core.engines.errors import EngineVersionNotAllowed
from harness_core.engines.types import EngineKind, EngineVersion
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from harness_core.engines.ports import EngineAdapter

_LOG = logging.getLogger(__name__)


class AllowlistEngineRegistry:
    """In-memory ``EngineRegistry``; the allowlist is release data, never edited at runtime."""

    def __init__(
        self,
        adapters: Iterable[EngineAdapter],
        allowlist: Iterable[EngineVersion],
        *,
        successors: Mapping[str, EngineVersion] | None = None,
        log: HarnessLog | None = None,
    ) -> None:
        """``successors``: ``kind@old`` to the allow-listed version that continues it (the release's declarations)."""
        by_kind: dict[EngineKind, EngineAdapter] = {}
        for adapter in adapters:
            if adapter.kind in by_kind:
                raise ValueError(f"Two adapters registered for {adapter.kind}")
            by_kind[adapter.kind] = adapter
        self._adapters: Mapping[EngineKind, EngineAdapter] = by_kind
        allowed: dict[tuple[EngineKind, str], str] = {}
        for version in allowlist:
            allowed[(version.kind, version.version)] = version.binary_digest
        self._allowed = allowed
        self._successors = {
            old: new for old, new in (successors or {}).items() if allowed.get((new.kind, new.version)) == new.binary_digest
        }
        self._log = log or HarnessLog(_LOG)

    def allowed(self) -> frozenset[EngineVersion]:
        return frozenset(
            EngineVersion(kind=kind, version=version, binary_digest=digest)
            for (kind, version), digest in self._allowed.items()
        )

    def resumed_on(self, version: EngineVersion) -> EngineVersion | None:
        """The successor a run pinned to ``version`` resumes on: only for a version the release no longer lists, and
        only one of its own kind that the release allows (a listed version always runs itself)."""
        if (version.kind, version.version) in self._allowed:
            return None
        successor = self._successors.get(version.ref)
        return successor if successor is not None and successor.kind == version.kind else None

    def adapter_for(self, version: EngineVersion) -> EngineAdapter:
        successor = self.resumed_on(version)
        if successor is not None:
            self._log.info(
                "engine.version.resumed", engineKind=version.kind, engineVersion=version.version,
                successor=successor.version,
            )
            version = successor
        adapter = self._adapters.get(version.kind)
        digest = self._allowed.get((version.kind, version.version))
        if adapter is None:
            raise self._refuse(version, "no_adapter", "no adapter is registered for this engine")
        if digest is None:
            raise self._refuse(version, "version_not_allowed", "engine version is not allowed")
        if digest != version.binary_digest:
            raise self._refuse(version, "digest_mismatch", "engine binary digest is not the pinned one")
        return adapter

    def _refuse(self, version: EngineVersion, reason: str, message: str) -> EngineVersionNotAllowed:
        self._log.warning(
            "engine.version.refused",
            reasonCode=reason,
            engineKind=version.kind,
            engineVersion=version.version,
        )
        return EngineVersionNotAllowed(message, reason=reason)


__all__ = ["AllowlistEngineRegistry"]
