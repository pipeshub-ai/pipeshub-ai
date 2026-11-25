"""The readers besides `check_access` that resolve a user's grants (the context role, the record access details, the search scope), held to the
access rule in worlds that link a user to a source account.

A linked account adds its own and its groups' grants inside its connector, and nothing else: no organisation. So every reader must answer the
same when the linked accounts' organisation memberships are removed (`graph.without_link_source_orgs`), and none may give an ability on a node
the access check refuses. (The search scope is a prefilter: retrieval keeps only the hits ``check_access`` admits, so it may be wider.)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.org_context import for_org

if TYPE_CHECKING:
    from .graph import Namespace
    from .model import World

ABILITIES = ("canUpload", "canCreateFolders", "canEdit", "canDelete", "canManagePermissions")


def linked_worlds(generated: int) -> list[tuple[str, World]]:
    """Every scenario that links a user to a source account, and ``generated`` generated worlds of each generator that do."""
    from . import gen
    from .scenario import load_all

    return [*((s.id, s.final_world()) for s in load_all() if s.final_world().links), *gen.linked(generated)]


def linked_viewers(world: World) -> list[str]:
    """The viewers a link can change anything for: those linked to a source account."""
    return sorted({link.user for link in world.links})


async def snapshot(provider: Any, world: World, viewer: str, ns: Namespace, org: str) -> dict[Any, Any]:  # noqa: ANN401
    """What the readers answer the viewer, with the store's ids mapped back to the world's, inside ``org`` (the store's id of the world's org) as a
    request binds it."""
    with for_org(org, job="permission-conformance"):
        return await _snapshot(provider, world, viewer, ns, org)


async def _snapshot(provider: Any, world: World, viewer: str, ns: Namespace, org: str) -> dict[Any, Any]:  # noqa: ANN401
    out: dict[Any, Any] = {}
    for n in world.nodes:
        if n.kind not in ("record", "group"):
            continue
        ctx = await provider.get_knowledge_hub_context_permissions(
            ns.id(viewer), org, ns.id(n.id), parent_type="record" if n.kind == "record" else "recordGroup")
        out[("context", n.id)] = {k: ctx.get(k) for k in ("role", *ABILITIES)}
        if n.kind == "record":
            details = await provider.check_record_access_with_details(ns.id(viewer), org, ns.id(n.id))
            # the role only: which path is named for it among equal roles is the store's choice
            out[("details", n.id)] = None if not details else sorted(p.get("relationship") for p in details.get("permissions") or [])
    found = await provider.get_accessible_virtual_record_ids(ns.id(viewer), org)
    out["search"] = sorted(ns.local(v) for v in found or {})      # the virtual record ids: which record stands for a shared one may differ
    return out


def findings(name: str, viewer: str, plain: dict, without: dict, admitted: set[str]) -> list[str]:
    """What differs between the two snapshots, and every ability on a node the access check refuses."""
    out = [f"{name} {viewer} {key}: {plain[key]} with the account's organisation, {without.get(key)} without"
           for key in plain if plain[key] != without.get(key)]
    for key, value in plain.items():
        if isinstance(key, tuple) and key[0] == "context" and key[1] not in admitted and any(value.get(a) for a in ABILITIES):
            out.append(f"{name} {viewer} {key[1]}: abilities {value} on a node the access check refuses")
    return out
