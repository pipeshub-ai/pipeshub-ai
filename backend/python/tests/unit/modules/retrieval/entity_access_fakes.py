"""Stand-ins for the two provider calls the entity permission layer makes,
built from one candidate fixture.

``get_permitted_entity_records`` applies the query's contract: a ref's
candidates are the fixture rows in the ref's connectors, the window is walked
in order, and a row comes back when its connector is in
``app_level_connector_ids``, up to the limit. No role path offers anything
here, so a record-level row reaches the access check only when the caller
passes its connector.

``check_access`` is the decision: a row of an app-level connector is
readable, a record-level row only when listed in ``permitted``, and
``denied`` refuses even those. It knows a row's connector from what the query
returned. An id that never came back as a row (a record group) is admitted
unless denied.

``get_record_taxonomy_links`` (``record_spellings``) spells each entity a
returned row was found under by ``names[entity_id]``, by default the entity
id (the name these fixtures give a hit).
"""
from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.common.utils import PermittedEntityRows
from app.services.graph_db.interface.graph_db_provider import AccessCheck

CandidateBuilder = Callable[..., dict]

# What the fake hands out as the user's resolved grants; the access check must be given this object.
RESOLVED_ACCESS: dict[str, Any] = {"grantee_ids": ["ukey"], "gated_app_ids": [], "by_connector": {}}


class EntityGraphFake:
    """``build(refs, org_id, record_types=, limit_per_entity=, offset=)``
    returns candidates keyed by entity id or ``(type, id)``; it is asked for
    one window (``limit_per_entity`` is the window size)."""

    def __init__(
        self,
        build: CandidateBuilder,
        *,
        app_level: Iterable[str] = (),
        permitted: Iterable[str] = (),
        denied: Iterable[str] = (),
    ) -> None:
        self._build = build
        self._app_level = set(app_level)
        self._permitted = set(permitted)
        self._denied = set(denied)
        self._connector_of: dict[str, str] = {}
        self.returned: dict[str, set[str]] = {}

    async def permitted_records(
        self,
        refs: list[dict],
        org_id: str,
        user_key: str,
        *,
        app_level_connector_ids: list[str],
        record_types: list[str] | None = None,
        limit_per_entity: int = 20,
        offset: int = 0,
        window: int = 200,
        timeout_seconds: float | None = None,
    ) -> dict[tuple[str, str], PermittedEntityRows]:
        types = {str(ref["id"]): ref.get("type", "") for ref in refs}
        scopes = {(ref.get("type", ""), str(ref["id"])): set(ref.get("connectorIds") or []) for ref in refs}
        passing = set(app_level_connector_ids)
        out: dict[tuple[str, str], PermittedEntityRows] = {}
        built = self._build(refs, org_id, record_types=record_types, limit_per_entity=window, offset=offset)
        if inspect.isawaitable(built):
            built = await built
        for key, rows in built.items():
            entity = key if isinstance(key, tuple) else (types.get(key, ""), key)
            scope = scopes.get(entity, set())
            win = [row for row in rows if row.get("connectorId") in scope][:window]
            hits = []
            for pos, row in enumerate(win):
                if row["connectorId"] in passing:
                    hits.append({"pos": pos, "row": row})
                    self._connector_of[row["_key"]] = row["connectorId"]
                    self.returned.setdefault(str(row["_key"]), set()).add(entity[1])
                    if len(hits) == limit_per_entity:
                        break
            out[entity] = PermittedEntityRows.from_window(
                hits, limit=limit_per_entity, window_size=len(win),
                capped=bool(getattr(rows, "capped", False)),
            )
        return out

    async def check_access(
        self, user_key: str, org_id: str, *, node_ids: Iterable[str] = (), **_: object,
    ) -> AccessCheck:
        return AccessCheck(node_ids=frozenset(i for i in node_ids if self._readable(i)))

    def _readable(self, node_id: str) -> bool:
        if node_id in self._denied:
            return False
        connector = self._connector_of.get(node_id)
        return connector is None or connector in self._app_level or node_id in self._permitted


def record_spellings(
    fake: EntityGraphFake,
    names: Mapping[str, str] | None = None,
) -> Callable[..., Any]:
    """``get_record_taxonomy_links`` for the records ``fake`` returned."""
    spelled = dict(names or {})

    async def _links(record_keys: list[str], transaction: str | None = None) -> list[dict]:
        return [
            {
                "recordId": key, "collection": "topics", "entityId": entity_id,
                "name": spelled.get(entity_id, entity_id), "canonical": True,
                "extractedName": spelled.get(entity_id, entity_id), "migrated": False,
            }
            for key in record_keys
            for entity_id in sorted(fake.returned.get(key, ()))
        ]

    return _links


def entity_graph(
    build: CandidateBuilder,
    *,
    app_level: Iterable[str] = (),
    permitted: Iterable[str] = (),
    denied: Iterable[str] = (),
    names: Mapping[str, str] | None = None,
) -> MagicMock:
    """A graph provider mock whose calls are answered by one ``EntityGraphFake``."""
    fake = EntityGraphFake(build, app_level=app_level, permitted=permitted, denied=denied)
    graph = MagicMock()
    graph.get_permitted_entity_records = AsyncMock(side_effect=fake.permitted_records)
    graph.check_access = AsyncMock(side_effect=fake.check_access)
    graph.get_knowledge_hub_access_v3 = AsyncMock(return_value=RESOLVED_ACCESS)
    graph.get_record_taxonomy_links = AsyncMock(side_effect=record_spellings(fake, names))
    return graph
