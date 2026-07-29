"""ConnectorCatalog: id ↔ name/type mapping for the current request.

Built once per request from three sources (in priority order):
1. ``state["available_connectors"]`` — set by bridge.py for chat modes.
2. ``state["agent_knowledge"]`` — per-agent knowledge config.
3. Lazy fallback: ``get_knowledge_hub_filter_options`` (user-scoped graph query).

Cached in ``state["_connector_catalog"]`` so navigate + lookup_record in
the same request share the same view without a second DB round-trip.

The catalog is the single source of truth for:
- Mapping connector type/name hints to connector UUIDs (``filter()``).
- Building the connector-types summary shown in capability_summary.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

_STATE_KEY = "_connector_catalog"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ConnectorInfo(BaseModel):
    """Minimal identity info for one connector/app."""

    id: str
    name: str          # "Jira Cloud", "Confluence Connector", …
    type: str          # "JIRA", "CONFLUENCE", "KB", "DRIVE", …


# ---------------------------------------------------------------------------
# Type-matching helpers
# ---------------------------------------------------------------------------


def _normalize_token(s: str) -> str:
    """Uppercase, strip underscores/hyphens/spaces for loose comparison."""
    return re.sub(r"[\s_\-]", "", s).upper()


def _types_overlap(connector_type: str, hint: str) -> bool:
    """Return True when *hint* is a fuzzy match for *connector_type*.

    Handles cases like:
    - hint "GOOGLE_DRIVE" vs type "DRIVE" or "GOOGLEDRIVE"
    - hint "JIRA" vs type "JIRA CLOUD" or "JIRA_SERVER"
    - exact match after normalization
    """
    ct = _normalize_token(connector_type)
    h = _normalize_token(hint)
    return ct == h or ct in h or h in ct


# ---------------------------------------------------------------------------
# Main catalog class
# ---------------------------------------------------------------------------


class ConnectorCatalog:
    """Immutable snapshot of all connectors visible to the current user."""

    def __init__(self, connectors: list[ConnectorInfo]) -> None:
        self.connectors = connectors

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def build(
        cls,
        state: dict[str, Any],
        *,
        graph_provider: "IGraphDBProvider",
        user_key: str,
        org_id: str,
    ) -> "ConnectorCatalog":
        """Build (or return the cached) catalog for this request.

        Priority order:
        1. ``state["available_connectors"]`` (list[dict] with id/name/type).
        2. ``state["agent_knowledge"]`` entries.
        3. Lazy ``get_knowledge_hub_filter_options`` fallback.

        Failures at step 3 are silently swallowed and return an empty catalog
        so callers degrade gracefully rather than crash.
        """
        cached = state.get(_STATE_KEY)
        if cached is not None and isinstance(cached, cls):
            return cached

        infos: list[ConnectorInfo] = []
        seen_ids: set[str] = set()

        def _add(cid: str, name: str, ctype: str) -> None:
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                infos.append(ConnectorInfo(id=cid, name=name or ctype, type=(ctype or "").upper()))

        # Source 1: pre-fetched by bridge (chat modes)
        for c in (state.get("available_connectors") or []):
            if isinstance(c, dict):
                _add(c.get("id", ""), c.get("name", ""), c.get("type", ""))

        # Source 2: agent_knowledge (agent template path)
        for k in (state.get("agent_knowledge") or []):
            if isinstance(k, dict):
                _add(
                    k.get("connectorId", ""),
                    k.get("name") or k.get("displayName") or "",
                    k.get("type", ""),
                )

        # Source 3: lazy fallback — user-scoped filter options
        if not infos:
            try:
                options = await graph_provider.get_knowledge_hub_filter_options(
                    user_key=user_key, org_id=org_id,
                )
                for app in (options or {}).get("apps") or []:
                    if isinstance(app, dict):
                        _add(app.get("id", ""), app.get("name", ""), app.get("type", ""))
            except Exception as exc:
                logger.warning("ConnectorCatalog: filter_options fallback failed: %s", exc)

        catalog = cls(infos)
        state[_STATE_KEY] = catalog
        return catalog

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def filter(
        self,
        *,
        hint: str | None = None,
        ids: list[str] | None = None,
        prefer_ids: list[str] | None = None,
    ) -> list[ConnectorInfo]:
        """Return connectors matching *hint* (type or name, loose) and/or *ids*.

        - If *hint* is provided, restrict to connectors whose type or name
          loosely matches. If the hint matches nothing, **widen back to all**
          (hint prioritises, never dead-ends).
        - If *ids* is provided, further restrict to those specific ids.
        - If *prefer_ids* is provided, matching connectors are sorted first.
        """
        base = list(self.connectors)

        if ids is not None:
            id_set = set(ids)
            base = [c for c in base if c.id in id_set]

        if hint:
            matched = [c for c in base if _types_overlap(c.type, hint) or _types_overlap(c.name, hint)]
            if matched:
                base = matched
            # else: hint matched nothing → keep full base (widen, not dead-end)

        if prefer_ids:
            prefer_set = set(prefer_ids)
            preferred = [c for c in base if c.id in prefer_set]
            rest = [c for c in base if c.id not in prefer_set]
            base = preferred + rest

        return base

    def connector_ids(
        self,
        *,
        hint: str | None = None,
        ids: list[str] | None = None,
        prefer_ids: list[str] | None = None,
    ) -> list[str]:
        """Convenience: ids from ``filter()``."""
        return [c.id for c in self.filter(hint=hint, ids=ids, prefer_ids=prefer_ids)]

    def types(self) -> list[str]:
        """Deduplicated connector types present in the catalog."""
        seen: set[str] = set()
        out: list[str] = []
        for c in self.connectors:
            if c.type and c.type not in seen:
                seen.add(c.type)
                out.append(c.type)
        return out

    def names_by_type(self, hint: str) -> list[str]:
        """Connector display names matching *hint*."""
        return [c.name for c in self.filter(hint=hint)]

    def is_empty(self) -> bool:
        return not self.connectors

    def __len__(self) -> int:
        return len(self.connectors)

    def __repr__(self) -> str:
        types = self.types()
        return f"ConnectorCatalog({len(self.connectors)} connectors, types={types})"
