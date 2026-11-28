"""Which connectors a viewer reaches, outside ``check_access``, against the oracle's gate: knowledge-graph entity search
(``get_entity_access_context``) and the team connector list. Store-neutral: a caller loads the world and hands over the
store's answers.

* entity search: every app it returns is one the oracle's gate admits (R4), a limited app (``accessLimit`` set) is returned
  exactly when the gate admits it, and every record group it returns belongs to one of the apps it returns;
* team list: a limited team connector is listed exactly when it is of the request's org and its limit admits the viewer
  (``oracle._limit_admits``, membership read from the edge to the app's own org); one of another org is never listed.

The entity path reads fewer edges than the gate (no group or role edge to an app), so an unlimited app the gate admits may be
missing from it: that is not a finding. A limited app is gated by its limit alone, so there it must be exact.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from . import oracle

if TYPE_CHECKING:
    from .graph import Graph, Namespace
    from .model import World

EntityFn = Callable[[str], Awaitable[dict | None]]  # the viewer's store id -> get_entity_access_context's answer
TeamFn = Callable[[str], Awaitable[list[str]]]  # the viewer's store id -> the limited and unlimited team apps listed


def _limited(store: oracle.Store, org: str) -> set[str]:
    return {n["id"] for n in store.nodes if n["kind"] == "App" and n["props"].get("orgId") == org
            and n["props"].get("accessLimit") is not None}


def _limited_anywhere(store: oracle.Store) -> set[str]:
    return {n["id"] for n in store.nodes if n["kind"] == "App" and n["props"].get("accessLimit") is not None}


async def reach_findings(world: World, graph: Graph, ns: Namespace, entity: EntityFn, team: TeamFn | None = None) -> list[str]:
    """Every disagreement for every user of the world, as one line each (``leak`` or ``gap`` first)."""
    store = oracle.Store(graph)
    limited = _limited(store, ns.org)
    found: list[str] = []
    for viewer in world.users():
        user = ns.id(viewer)
        gated = oracle.gate(store, user, ns.org, oracle.principals(store, user))
        ctx = await entity(user)
        apps = {a["id"] for a in (ctx or {}).get("apps", [])}
        found += [f"leak entity {viewer}: app {ns.local(a)}" for a in sorted(apps - gated)]
        found += [f"gap entity {viewer}: limited app {ns.local(a)}" for a in sorted((limited & gated) - apps)]
        groups = {n["id"]: n["props"].get("connectorId") for n in store.nodes if n["kind"] == "RecordGroup"}
        found += [f"leak entity {viewer}: group {ns.local(g)} of app {ns.local(str(groups.get(g)))}"
                  for g in sorted((ctx or {}).get("record_group_ids", [])) if groups.get(g) not in apps]
        if team is not None:
            listed = set(await team(user)) & _limited_anywhere(store)
            admits = {a for a in limited if oracle._limit_admits(store, user, a)}
            found += [f"leak team {viewer}: limited app {ns.local(a)}" for a in sorted(listed - admits)]
            found += [f"gap team {viewer}: limited app {ns.local(a)}" for a in sorted(admits - listed)]
    return found
