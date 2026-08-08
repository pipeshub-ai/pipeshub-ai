"""`FilterVocabularyService`: the single cached access point for filter
vocabulary — record groups, people, user groups, roles.

`list_filter_values`, `people_search`, the PRE_TOOL_USE value-resolution
hook, and the prompt preloader all read through this one implementation
instead of each querying `IGraphDBProvider` independently — see the
"Weaknesses of the Previous Plan This Rewrite Fixes" section of the design
doc (scattered vocabulary lookups was weakness #3).

Caching is per `(org_id, connector_id, dimension, query)` with a short TTL:
vocabulary changes only as often as a connector re-syncs, but must not be
stale across an org for longer than that TTL once someone renames a
project/space.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.entities import RecordGroupType
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SECONDS = 120
_ROLES_NOT_TRACKED_CONNECTORS = frozenset({"CONFLUENCE", "CONFLUENCE_DATA_CENTER"})


@dataclass
class RecordGroupVocabEntry:
    name: str
    key: str
    external_id: str
    group_type: str
    is_stub: bool = False


@dataclass
class PersonVocabEntry:
    display_name: str
    email: str
    source_user_id: str
    user_id: str


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class FilterVocabularyService:
    """Wraps `IGraphDBProvider` for the four vocabulary dimensions the
    filter-search tools need. One instance per request is fine — the cache
    is keyed by org/connector so it's safe to share across requests too."""

    def __init__(self, graph_provider: "IGraphDBProvider", ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._graph = graph_provider
        self._ttl = ttl_seconds
        self._cache: dict[tuple, _CacheEntry] = {}

    def _get_cached(self, key: tuple) -> Any | None:  # noqa: ANN401
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            self._cache.pop(key, None)
            return None
        return entry.value

    def _set_cached(self, key: tuple, value: Any) -> None:  # noqa: ANN401
        self._cache[key] = _CacheEntry(value=value, expires_at=time.monotonic() + self._ttl)

    async def record_groups(
        self,
        org_id: str,
        connector_id: str,
        group_types: list[RecordGroupType] | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> list[RecordGroupVocabEntry]:
        """Record groups for one connector, defaulting to top-level containers.

        Tolerates stub groups (created by `_handle_record_group` before the
        real sync populates `short_name`) by falling back to matching on
        `name` and flagging the entry `is_stub=True` rather than raising or
        silently dropping it — a stub is still a real, addressable group,
        just not yet safely usable as a native-query key.
        """
        cache_key = ("record_groups", org_id, connector_id, tuple(sorted(g.value for g in group_types or [])), query, limit)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            raw = await self._graph.list_record_groups(
                org_id=org_id,
                connector_id=connector_id,
                group_types=[g.value for g in group_types] if group_types else None,
                query=query,
                limit=limit,
            )
        except Exception:
            logger.exception("FilterVocabularyService.record_groups failed for connector=%s", connector_id)
            return []

        entries: list[RecordGroupVocabEntry] = []
        for rg in raw:
            short_name = rg.get("shortName")
            external_id = rg.get("externalGroupId") or ""
            is_stub = not short_name
            key = short_name or rg.get("groupName") or external_id
            entries.append(
                RecordGroupVocabEntry(
                    name=rg.get("groupName") or key,
                    key=key,
                    external_id=external_id,
                    group_type=rg.get("groupType") or "",
                    is_stub=is_stub,
                )
            )
        self._set_cached(cache_key, entries)
        return entries

    async def people(
        self,
        org_id: str,
        connector_id: str,
        query: str | None = None,
        limit: int = 20,
    ) -> list[PersonVocabEntry]:
        """People for one connector via the `userAppRelation` edge.

        Coverage is inherently partial — see `FilterCapabilityDescriptor.
        people_coverage_note` for connector-specific gaps (Slack Individual
        scope, Atlassian private emails) that callers should surface rather
        than treat a miss here as "user does not exist".
        """
        cache_key = ("people", org_id, connector_id)
        cached = self._get_cached(cache_key)
        if cached is None:
            try:
                raw = await self._graph.get_app_users(org_id=org_id, connector_id=connector_id)
            except Exception:
                logger.exception("FilterVocabularyService.people failed for connector=%s", connector_id)
                return []
            cached = [
                PersonVocabEntry(
                    display_name=u.get("fullName") or u.get("email") or "",
                    email=u.get("email") or "",
                    source_user_id=u.get("sourceUserId") or "",
                    user_id=u.get("_key") or u.get("id") or "",
                )
                for u in raw
                if u.get("sourceUserId")
            ]
            self._set_cached(cache_key, cached)

        if not query:
            return cached[:limit]
        needle = query.strip().lower()
        matched = [
            p for p in cached
            if needle in p.display_name.lower() or needle in p.email.lower()
        ]
        return matched[:limit]

    async def user_groups(self, org_id: str, connector_id: str) -> list[dict]:
        cache_key = ("user_groups", org_id, connector_id)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        try:
            groups = await self._graph.get_user_groups(connector_id=connector_id, org_id=org_id)
        except Exception:
            logger.exception("FilterVocabularyService.user_groups failed for connector=%s", connector_id)
            return []
        result = [{"name": g.name, "key": g.source_user_group_id, "external_id": g.source_user_group_id} for g in groups]
        self._set_cached(cache_key, result)
        return result

    async def roles(self, org_id: str, connector_id: str, connector_type: str) -> list[dict] | None:
        """Returns `None` (not `[]`) when this connector type is known not to
        track roles — `list_filter_values` must render that distinctly from
        "no roles configured"."""
        if connector_type.upper() in _ROLES_NOT_TRACKED_CONNECTORS:
            return None
        cache_key = ("roles", org_id, connector_id)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        try:
            roles = await self._graph.list_roles(org_id=org_id, connector_id=connector_id)
        except Exception:
            logger.exception("FilterVocabularyService.roles failed for connector=%s", connector_id)
            return []
        result = [{"name": r.get("name"), "key": r.get("externalRoleId"), "external_id": r.get("externalRoleId")} for r in roles]
        self._set_cached(cache_key, result)
        return result

    def invalidate(self, org_id: str, connector_id: str) -> None:
        """Drop all cached entries for one connector — call after a resync."""
        stale = [k for k in self._cache if len(k) >= 3 and k[1] == org_id and k[2] == connector_id]
        for k in stale:
            self._cache.pop(k, None)


__all__ = ["FilterVocabularyService", "RecordGroupVocabEntry", "PersonVocabEntry"]
