"""What an admin sees of one connector's permission freshness: a pure function of its app document, so the panel cannot disagree with what a search does.

It reads through `scope_view.connector_view` (pending roots, writes in flight, a failing sweep) and `currency.app_is_served`; nothing here
re-derives them. The answer holds counts, codes and times only: no record, container or user name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.connectors.core.constants import ConnectorStateKeys as K
from app.services.permissions.currency import RUNNING, app_is_current, app_is_served
from app.services.permissions.pending import PendingUnreadable, parse
from app.services.permissions.structure_generation import (
    PENDING_KEY,
    structure_generation_of,
    whole_number,
)
from app.services.vector_db.scope_backfill import BACKFILL_KEYS
from app.services.vector_db.scope_view import connector_view

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

#: Written by a connector at the end of a sync: `[{code, items, container?, fix?}]`. No connector writes it yet; absent reads as none.
WARNINGS_KEY = "permissionWarnings"
#: `scope_view` lines that are not "attention" for the card: the roots have their own row.
_NOT_LISTED = {"pending_roots"}


def _iso(ms: object) -> str | None:
    """The sync's finish time as ISO text. A store may hand the number back as a float or a string; anything else reads as no time."""
    try:
        n = int(float(str(ms)))
        return datetime.fromtimestamp(n / 1000, UTC).isoformat(timespec="seconds") if n > 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def _warnings(raw: object) -> list[dict[str, object]]:
    """The connector's own list, kept to the fields the card words; an entry that is not a mapping with a code is dropped."""
    return [{"code": str(w["code"]), "items": w.get("items"), "container": w.get("container"), "fix": w.get("fix")}
            for w in (raw if isinstance(raw, list) else []) if isinstance(w, dict) and w.get("code")]


def status(app: Mapping[str, Any], *, reach_minutes: int | None = None, now: datetime | None = None, store_time: float | None = None) -> dict[str, Any]:
    """``state`` is the one a person reads first: running, never (no sync has ended), failed, overflow (the structure moved past the stamped
    generation and no root was registered: a change too large to list as subtrees, so the whole connector is rebuilt), pending (subtrees
    being rewritten), else healthy. Anything else that keeps the stamps from being current (a failed back-fill, a version skew, an unreadable
    roots list) is an ``attention`` code, not a state: a connector that is not current is read by the legacy filter, which hides nothing."""
    view = connector_view("", app, store_time=store_time)
    codes = [a.split(":", 1)[0] for a in view.attention]
    try:
        roots = parse(app.get(PENDING_KEY))
        unreadable = False
    except PendingUnreadable:
        roots, unreadable = {}, True
    generation = structure_generation_of(app)
    stamped = whole_number(app.get(BACKFILL_KEYS["structure_generation"]))
    whole_connector = not roots and not unreadable and generation is not None and stamped is not None and generation > stamped
    finished = _iso(app.get(K.LAST_SYNC_FINISHED_AT))
    running = app.get("status") in RUNNING
    outcome = "running" if running else str(app.get(K.LAST_SYNC_OUTCOME) or "succeeded") if finished else "never"
    if running:
        state = "running"
    elif not finished:
        state = "never"
    elif outcome == "failed":
        state = "failed"
    elif whole_connector:
        state = "overflow"
    elif roots:
        state = "pending"
    else:
        state = "healthy"
    backfilled = app.get(BACKFILL_KEYS["state"]) is not None
    return {
        "asOf": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "state": state,
        "reachWithinMinutes": reach_minutes,
        # shortcut: no `exact` flag exists on the app document yet; add it beside `served` when a pass records it
        "serving": {"current": app_is_current(app), "served": app_is_served(app)},
        "lastPermissionSync": {"finishedAt": finished, "outcome": outcome},
        # the generation counts structural changes (it is not a time), so the age is "changes ago", as `scope_view` says it
        "pending": {"roots": len(roots), "unreadable": unreadable,
                    "oldestChangesAgo": generation - min(roots.values()) if roots and generation is not None else None},
        "coverage": {"records": whole_number(app.get(BACKFILL_KEYS["records"])), "stale": whole_number(app.get(BACKFILL_KEYS["stale"])) or 0} if backfilled else None,
        "attention": [c for c in codes if c not in _NOT_LISTED],
        "warnings": _warnings(app.get(WARNINGS_KEY)),
    }


def notices(apps: Iterable[Mapping[str, Any]], *, empty: bool = False) -> list[dict[str, Any]]:
    """What a search tells the person who ran it, as codes (the client words them): `permissions_updating` while subtrees of a connector await a
    rewrite (or the list of them cannot be read), `connector_syncing` while it syncs and either it has never finished a sync (its content is
    still arriving) or the search found nothing (`empty`): a routine resync of an established connector changes nothing the person sees. The
    caller passes only the connectors that person is gated into, so nothing here names a connector they cannot already see, and nothing names a
    record, container or person."""
    out: list[dict[str, str]] = []
    for app in apps:
        cid = str(app.get("id") or app.get("_key") or "")
        label = {"connectorName": str(app["name"])} if app.get("name") else {}
        try:
            updating = bool(parse(app.get(PENDING_KEY)))
        except PendingUnreadable:
            updating = True
        if updating:
            out.append({"code": "permissions_updating", "connectorId": cid, **label})
        first = not app.get(K.LAST_SYNC_FINISHED_AT)
        if app.get("status") in RUNNING and (empty or first):
            # `firstSync`: it has never finished a sync, so there is nothing to search yet (a resync of an established connector has).
            out.append({"code": "connector_syncing", "connectorId": cid, **label, **({"firstSync": True} if first else {})})
    return out


async def read_apps(graph: IGraphDBProvider, connector_ids: Iterable[str]) -> list[dict[str, Any]]:
    """One batched read of the connectors' app documents. A failed read gives no notices: it must not fail the search."""
    ids = sorted(set(connector_ids))
    if not ids:
        return []
    try:
        return list(await graph.get_nodes_by_field_in("apps", "id", ids, raise_on_error=True) or [])
    except Exception:
        return []
