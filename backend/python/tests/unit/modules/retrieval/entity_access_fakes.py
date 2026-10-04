"""A stand-in for ``IGraphDBProvider.get_permitted_entity_records`` built
from a candidate fixture, applying the provider's contract in Python: walk
the window in order, keep rows in the ref's connectors that app access, a
permission role grants, stop at the limit."""
from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from typing import Any

from app.services.graph_db.common.utils import PermittedEntityRows

CandidateBuilder = Callable[..., dict]


def permitted_records(
    build: CandidateBuilder,
    *,
    permitted: Iterable[str] = (),
) -> Callable[..., Any]:
    """``build(refs, org_id, record_types=, limit_per_entity=, offset=)``
    returns candidates keyed by entity id or ``(type, id)``; it is asked for
    one window (``limit_per_entity`` is the window size)."""
    granted = set(permitted)

    async def _permitted(
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
        app_level = set(app_level_connector_ids)
        out: dict[tuple[str, str], PermittedEntityRows] = {}
        built = build(refs, org_id, record_types=record_types, limit_per_entity=window, offset=offset)
        if inspect.isawaitable(built):
            built = await built
        for key, rows in built.items():
            entity = key if isinstance(key, tuple) else (types.get(key, ""), key)
            scope = scopes.get(entity, set())
            win = list(rows)[:window]
            hits = []
            for pos, row in enumerate(win):
                connector = row.get("connectorId")
                if connector in scope and (connector in app_level or row.get("_key") in granted):
                    hits.append({"pos": pos, "row": row})
                    if len(hits) == limit_per_entity:
                        break
            out[entity] = PermittedEntityRows.from_window(
                hits, limit=limit_per_entity, window_size=len(win),
                capped=bool(getattr(rows, "capped", False)),
            )
        return out

    return _permitted
