"""RecordResolver: identifier → permission-checked record.

Resolution is driven by a declarative STRATEGY_TABLE that maps each
``RefKind`` to an ordered list of ``LookupStrategy`` objects. This makes
the resolver generic: adding a new connector never requires editing this
file — at most one extractor regex in ``identifiers.py`` and, if the
connector needs a special lookup method, one row in the table.

Resolution order for each identifier:
1. ``resolve_canonical_ref`` → ``RefKind`` + optional connector hint.
2. Walk ``STRATEGY_TABLE[kind]`` in order; execute each strategy over the
   catalog-filtered connector set; stop on first non-empty result.
3. If the input was a URL and canonical strategies all missed, fall back to
   ``get_record_by_weburl(normalized)`` then ``(raw)`` — so connectors with
   no dedicated extractor still resolve via their indexed ``webUrl``.
4. Bare non-URL inputs that miss every strategy fall back to
   ``get_record_by_external_id`` across the full catalog.
5. Every candidate passes ``get_knowledge_hub_node_access`` (org + user ACL).

Lookup scope is the **org-wide user-accessible catalog** (not restricted to
the agent/filter scope) so a Jira key found inside a Confluence page can
resolve even when the session filter is scoped to Confluence only.
Agent-scoped connectors are sorted first for performance; out-of-scope
matches are labelled by connector name in the output.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.models.entities import Record
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

from .catalog import ConnectorCatalog, ConnectorInfo
from .identifiers import CanonicalRef, RefKind, normalize_weburl, resolve_canonical_ref
from .models import LookupMatch, LookupResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LookupStrategy:
    """One step in the resolution chain for a given RefKind.

    method:
        "external_id" — get_record_by_external_id(connector_id, value)
        "issue_key"   — get_record_by_issue_key(connector_id, value)
        "weburl"      — get_record_by_weburl(value, org_id)
        "slack_ts"    — find_slack_burst_record_by_ts(connector_id, channel, ts)

    connector_types:
        frozenset of connector type strings to restrict to (e.g. {"JIRA"}).
        None means "all connectors in the catalog".
        An empty frozenset means the strategy is skipped (never match).
    """

    method: str
    connector_types: frozenset[str] | None


# The table. Each kind gets an ordered list of strategies; the resolver
# walks them until a hit, then gates the result with the ACL check.
#
# Design rules:
# - external_id is the universal key — every connector indexes it.
# - issue_key / slack_ts are per-type exceptions (provider methods are shaped
#   specifically for those connectors).
# - The final weburl strategy with None types acts as the universal URL fallback
#   and is also inserted automatically for URL inputs by _fetch_candidates.
_JIRA_TYPES: frozenset[str] = frozenset({"JIRA"})
_LINEAR_TYPES: frozenset[str] = frozenset({"LINEAR"})
_SLACK_TYPES: frozenset[str] = frozenset({"SLACK"})
_DRIVE_TYPES: frozenset[str] = frozenset({"DRIVE", "GOOGLE_DRIVE", "GOOGLE_WORKSPACE", "GOOGLEDRIVE"})

STRATEGY_TABLE: dict[RefKind, list[LookupStrategy]] = {
    RefKind.ISSUE_KEY: [
        # Jira: provider method matches webUrl /browse/{key} — Jira-specific
        LookupStrategy("issue_key", _JIRA_TYPES),
        # Linear: stores key as externalRecordId
        LookupStrategy("external_id", _LINEAR_TYPES),
        # Last resort: maybe stored by webUrl on any connector
        LookupStrategy("weburl", None),
    ],
    RefKind.DRIVE_FILE: [
        # Try Drive-type connectors first, then widen to all
        LookupStrategy("external_id", _DRIVE_TYPES),
        LookupStrategy("external_id", None),
    ],
    RefKind.EXTERNAL_ID: [
        # Hinted connectors first, then all — handled in _fetch_by_external_id
        LookupStrategy("external_id", None),
    ],
    RefKind.WEB_URL: [
        LookupStrategy("weburl", None),
    ],
    RefKind.SLACK_TS: [
        LookupStrategy("slack_ts", _SLACK_TYPES),
    ],
}


# ---------------------------------------------------------------------------
# Match building
# ---------------------------------------------------------------------------


def _record_to_match(
    record: Any, identifier_used: str, frontend_url: str | None = None,
) -> LookupMatch | None:
    """Convert a record dict/object to a LookupMatch; None on bad data.

    Provider lookups (``get_record_by_issue_key``/``get_record_by_external_id``/
    ``get_record_by_weburl``) return the Pydantic ``Record`` model from
    ``app.models.entities``, which is snake_case throughout — this branches
    on ``isinstance(record, Record)`` first (rather than the previous
    duck-typed ``hasattr(record, "id")``, which matched but then read
    nonexistent camelCase attributes). ``record_type``/``connector_name``
    are enums and need ``.value``; ``context_block`` reuses the polymorphic
    ``to_llm_context()`` so ticket/file/mail-specific fields (Status,
    Priority, Assignee, ...) show up automatically for every record
    subtype without this function needing to know about them.
    """
    if record is None:
        return None
    if isinstance(record, Record):
        return LookupMatch(
            id=record.id or "",
            name=record.record_name or "",
            record_type=record.record_type.value if record.record_type else None,
            connector_name=record.connector_name.value if record.connector_name else None,
            web_url=record.weburl,
            indexing_status=record.indexing_status,
            identifier_used=identifier_used,
            external_id=record.external_record_id or None,
            context_block=record.to_llm_context(frontend_url=frontend_url),
        )
    if isinstance(record, dict):
        return LookupMatch(
            id=record.get("id") or record.get("_key") or "",
            name=record.get("recordName") or record.get("name") or "",
            record_type=record.get("recordType"),
            connector_name=record.get("connectorName"),
            web_url=record.get("webUrl"),
            indexing_status=record.get("indexingStatus"),
            identifier_used=identifier_used,
            external_id=record.get("externalRecordId") or record.get("external_record_id"),
        )
    return None


# ---------------------------------------------------------------------------
# RecordResolver
# ---------------------------------------------------------------------------


class RecordResolver:
    """Resolve one or more identifier strings to accessible LookupMatch objects.

    All results are:
    - Scoped to ``org_id`` (via catalog which uses user-scoped filter_options).
    - Filtered through user ACL (``get_knowledge_hub_node_access``).
    - Cross-connector: a Jira key found inside a Confluence doc resolves even
      when the agent scope is filtered to Confluence.
    """

    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        catalog: ConnectorCatalog,
        org_id: str,
        user_id: str,
        user_key: str,
        folder_mime_types: list[str],
        agent_connector_ids: list[str] | None = None,
        connector_name_hint: str | None = None,
        frontend_url: str | None = None,
    ) -> None:
        self._graph = graph_provider
        self._catalog = catalog
        self._org_id = org_id
        self._user_id = user_id
        self._user_key = user_key
        self._folder_mime_types = folder_mime_types
        # agent-scoped ids: tried first within each strategy for lower latency
        self._agent_ids: list[str] = agent_connector_ids or []
        self._connector_hint: str | None = (connector_name_hint or "").strip().upper() or None
        # Threaded into Record.to_llm_context() so relative `weburl` values
        # resolve against the deployment's real frontend instead of the
        # hardcoded localhost default (see entities.py::Record.to_llm_context).
        self._frontend_url = frontend_url

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def resolve_many(self, identifiers: list[str]) -> LookupResult:
        """Resolve a list of identifier strings in parallel."""
        tasks = [self._resolve_one(ident) for ident in identifiers]
        per_ident = await asyncio.gather(*tasks, return_exceptions=True)

        matches: list[LookupMatch] = []
        ambiguous = False
        not_found: list[str] = []
        searched_connectors: dict[str, list[str]] = {}  # ident → type names searched

        seen_ids: set[str] = set()
        for ident, result in zip(identifiers, per_ident):
            if isinstance(result, BaseException):
                logger.warning("resolve_one failed for %r: %s", ident, result)
                not_found.append(ident)
                continue

            candidates, searched = result
            searched_connectors[ident] = searched

            unique = [c for c in candidates if c.id not in seen_ids]
            for c in unique:
                seen_ids.add(c.id)

            if len(unique) > 1:
                ambiguous = True
                matches.extend(unique)
            elif unique:
                matches.extend(unique)
            else:
                not_found.append(ident)

        return LookupResult(
            matches=matches,
            ambiguous=ambiguous,
            not_found_identifiers=not_found,
            searched_connectors=searched_connectors,
        )

    # ------------------------------------------------------------------
    # Single-identifier resolution
    # ------------------------------------------------------------------

    async def _resolve_one(self, raw: str) -> tuple[list[LookupMatch], list[str]]:
        """Return (accessible matches, type-names searched) for one identifier."""
        raw = raw.strip()
        if not raw:
            return [], []

        ref = resolve_canonical_ref(raw)
        if ref is None:
            logger.debug("resolve_one(%r): no canonical ref produced", raw)
            return [], []

        logger.debug("resolve_one(%r): ref=%s/%s hint=%s", raw, ref.kind, ref.value, ref.connector_hint)
        candidates, searched = await self._fetch_candidates(raw, ref)
        logger.debug("resolve_one(%r): %d candidate(s), searched=%s", raw, len(candidates), searched)

        gated: list[LookupMatch] = []
        for match in candidates:
            node = await self._graph.get_knowledge_hub_node_access(
                node_id=match.id,
                user_key=self._user_key,
                org_id=self._org_id,
                folder_mime_types=self._folder_mime_types,
            )
            if node:
                if not match.record_type:
                    match.record_type = node.get("recordType")
                if not match.connector_name:
                    match.connector_name = node.get("connector")
                if not match.web_url:
                    match.web_url = node.get("webUrl")
                if not match.name:
                    match.name = node.get("name", "")
                gated.append(match)
            else:
                logger.debug(
                    "resolve_one(%r): candidate %s denied/missing by ACL", raw, match.id,
                )

        return gated, searched

    async def _fetch_candidates(
        self, raw: str, ref: CanonicalRef
    ) -> tuple[list[LookupMatch], list[str]]:
        """Walk the strategy table for *ref*; return (matches, searched-type-names)."""
        is_url = raw.startswith(("http://", "https://"))
        strategies = STRATEGY_TABLE.get(ref.kind, [])

        # Merge the caller's connector_name_hint into the ref hint
        effective_hint = self._connector_hint or ref.connector_hint

        all_matches: list[LookupMatch] = []
        searched_types: list[str] = []

        for strategy in strategies:
            connectors = self._resolve_connectors(strategy, effective_hint)
            if strategy.connector_types is not None and not connectors:
                # Typed strategy but no matching connectors in catalog — skip
                continue

            type_label = (
                "/".join(sorted(strategy.connector_types))
                if strategy.connector_types
                else "all"
            )
            searched_types.append(type_label)

            matches = await self._execute_strategy(strategy.method, connectors, ref, raw)
            all_matches.extend(matches)
            if all_matches:
                break  # first successful strategy wins

        # Universal URL fallback: if input was a URL and nothing found yet
        if is_url and not all_matches and ref.kind != RefKind.WEB_URL:
            searched_types.append("weburl-fallback")
            url_matches = await self._by_weburl(ref.value if ref.kind == RefKind.WEB_URL else raw)
            all_matches.extend(url_matches)

        # Bare external-id fallback: if non-URL input missed its canonical path
        if not is_url and not all_matches and ref.kind == RefKind.EXTERNAL_ID:
            # Already covered by the strategy table; this is a no-op guard
            pass

        return all_matches, searched_types

    def _resolve_connectors(
        self, strategy: LookupStrategy, hint: str | None,
    ) -> list[ConnectorInfo]:
        """Return catalog connectors for *strategy*, with agent ids sorted first."""
        from .catalog import _types_overlap as _to

        types_filter = strategy.connector_types  # frozenset | None
        hint_for_filter = hint if hint else None

        if types_filter is None:
            # "all connectors" — apply hint only (hint narrows but never dead-ends)
            return self._catalog.filter(
                hint=hint_for_filter,
                prefer_ids=self._agent_ids,
            )

        # Type-restricted strategy: first restrict to the required types,
        # then optionally narrow by the hint.
        all_sorted = self._catalog.filter(prefer_ids=self._agent_ids)
        by_type = [c for c in all_sorted if any(_to(c.type, t) for t in types_filter)]

        if hint_for_filter:
            narrowed = [c for c in by_type if _to(c.type, hint_for_filter) or _to(c.name, hint_for_filter)]
            by_type = narrowed if narrowed else by_type  # widen if hint matches nothing

        return by_type

    # ------------------------------------------------------------------
    # Strategy executors
    # ------------------------------------------------------------------

    async def _execute_strategy(
        self,
        method: str,
        connectors: list[ConnectorInfo],
        ref: CanonicalRef,
        raw: str,
    ) -> list[LookupMatch]:
        if method == "issue_key":
            return await self._by_issue_key(ref.value, connectors, raw)
        if method == "external_id":
            return await self._by_external_id(ref.value, connectors, raw)
        if method == "weburl":
            return await self._by_weburl(ref.value if ref.kind == RefKind.WEB_URL else raw)
        if method == "slack_ts":
            return await self._by_slack_ts(ref, connectors)
        return []

    async def _by_issue_key(
        self, key: str, connectors: list[ConnectorInfo], raw: str
    ) -> list[LookupMatch]:
        """``get_record_by_issue_key`` — Jira-shaped (webUrl /browse/{key})."""
        if not connectors:
            return []
        tasks = [self._graph.get_record_by_issue_key(c.id, key) for c in connectors]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        matches = []
        for i, rec in enumerate(results):
            if isinstance(rec, Exception):
                logger.debug("_by_issue_key(%r): connector %s raised: %s", key, connectors[i].id, rec)
                continue
            m = _record_to_match(rec, raw, self._frontend_url)
            if m and m.id:
                matches.append(m)
        return matches

    async def _by_external_id(
        self, external_id: str, connectors: list[ConnectorInfo], raw: str
    ) -> list[LookupMatch]:
        """``get_record_by_external_id`` across the given connectors."""
        if not connectors:
            return []
        tasks = [self._graph.get_record_by_external_id(c.id, external_id) for c in connectors]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        matches = []
        for rec in results:
            if isinstance(rec, Exception) or rec is None:
                continue
            m = _record_to_match(rec, raw, self._frontend_url)
            if m and m.id:
                matches.append(m)
        return matches

    async def _by_weburl(self, weburl: str) -> list[LookupMatch]:
        """``get_record_by_weburl``: normalized first, then raw."""
        normalized = normalize_weburl(weburl)
        candidates_to_try = [normalized]
        if normalized != weburl:
            candidates_to_try.append(weburl)

        for url in candidates_to_try:
            try:
                rec = await self._graph.get_record_by_weburl(url, org_id=self._org_id)
            except Exception as exc:
                logger.debug("_by_weburl(%r): %s", url, exc)
                continue
            if rec:
                m = _record_to_match(rec, weburl, self._frontend_url)
                if m and m.id:
                    return [m]
        return []

    async def _by_slack_ts(
        self, ref: CanonicalRef, connectors: list[ConnectorInfo]
    ) -> list[LookupMatch]:
        """``find_slack_burst_record_by_ts`` across Slack-type connectors."""
        if not connectors:
            logger.debug("No Slack connectors in catalog — skipping Slack ts lookup")
            return []
        tasks = [
            self._graph.find_slack_burst_record_by_ts(
                connector_id=c.id,
                channel_id=ref.channel_id or "",
                ts=ref.value,
            )
            for c in connectors
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        matches = []
        for rec in results:
            if isinstance(rec, Exception) or rec is None:
                continue
            m = _record_to_match(rec, ref.value, self._frontend_url)
            if m and m.id:
                matches.append(m)
        return matches
