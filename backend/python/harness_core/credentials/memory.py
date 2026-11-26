"""In-memory secret source: versions per (tenant, scope), superseded ones kept for the retention."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from harness_core.credentials.types import MAX_TTL_S, Secret

if TYPE_CHECKING:
    from datetime import datetime

    from pydantic import JsonValue

    from harness_core.clock import Clock
    from harness_core.credentials.types import CredentialScope


@dataclass(slots=True)
class _Version:
    number: int
    material: JsonValue
    superseded_at: datetime | None = None


class InMemorySecretSource:
    def __init__(self, clock: Clock, *, retention_s: int = MAX_TTL_S) -> None:
        self._clock = clock
        self._retention = timedelta(seconds=retention_s)
        self._versions: dict[tuple[str, CredentialScope], list[_Version]] = {}

    def _prune(self, versions: list[_Version]) -> None:
        horizon = self._clock.now() - self._retention
        versions[:] = [v for v in versions if v.superseded_at is None or v.superseded_at > horizon]

    def delete(self, tenant: str, scope: CredentialScope) -> None:
        _ = self._versions.pop((tenant, scope), None)

    async def read(self, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None:
        versions = self._versions.get((tenant, scope))
        if not versions:
            return None
        self._prune(versions)
        found = versions[-1] if version is None else next((v for v in versions if str(v.number) == version), None)
        return None if found is None else Secret(str(found.number), copy.deepcopy(found.material))

    async def write(self, tenant: str, scope: CredentialScope, material: JsonValue) -> str:
        versions = self._versions.setdefault((tenant, scope), [])
        now = self._clock.now()
        if versions:
            versions[-1].superseded_at = now
        self._prune(versions)
        number = versions[-1].number + 1 if versions else 1
        versions.append(_Version(number, copy.deepcopy(material)))
        return str(number)


__all__ = ["InMemorySecretSource"]
