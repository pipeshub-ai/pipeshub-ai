"""Apply a source-side change (`Mutation`) to a world, returning a new world.

Each kind is one small pure function. The oracle then answers for the changed
world, which is what a scenario's expectations describe.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .model import MUTATION_KINDS, Grant, Membership, Mutation, Parent, World

if TYPE_CHECKING:
    from collections.abc import Callable


def _grant_added(w: World, m: Mutation) -> World:
    grant = Grant(m.ref, m.node, m.value or "USER")
    return replace(w, grants=(*w.grants, grant))


def _grant_removed(w: World, m: Mutation) -> World:
    kept = tuple(g for g in w.grants if not (g.grantee == m.ref and g.node == m.node))
    if len(kept) == len(w.grants):
        raise ValueError(f"grant_removed: no grant {m.ref} -> {m.node}")
    return replace(w, grants=kept)


def _member_added(w: World, m: Mutation) -> World:
    return replace(w, memberships=(*w.memberships, Membership(m.ref, m.node)))


def _member_removed(w: World, m: Mutation) -> World:
    kept = tuple(x for x in w.memberships if not (x.member == m.ref and x.group == m.node))
    if len(kept) == len(w.memberships):
        raise ValueError(f"member_removed: no membership {m.ref} -> {m.node}")
    return replace(w, memberships=kept)


def _rule_set(w: World, m: Mutation) -> World:
    return w.with_node(replace(w.node(m.node), rule=m.value or None))


def _inherit_set(w: World, m: Mutation) -> World:
    node = w.node(m.node)
    others = tuple(i for i in node.inherits if i != m.ref)
    inherits = (*others, m.ref) if m.value == "true" else others
    return w.with_node(replace(node, inherits=inherits))


def _moved(w: World, m: Mutation) -> World:
    node = w.node(m.node)
    old = node.parents[0].id if node.parents else None
    inherits = tuple(m.ref if i == old else i for i in node.inherits)
    belongs = (m.value,) if m.value else node.belongs
    rel = node.parents[0].rel if node.parents else "PARENT_CHILD"
    return w.with_node(replace(node, parents=(Parent(m.ref, rel),), inherits=inherits, belongs=belongs))


def _deleted(w: World, m: Mutation) -> World:
    return w.with_node(replace(w.node(m.node), deleted=True))


def _user_removed(w: World, m: Mutation) -> World:
    who = m.node
    return replace(
        w,
        principals=tuple(replace(p, org_member=False) if p.id == who else p for p in w.principals),
        grants=tuple(g for g in w.grants if g.grantee != who),
        memberships=tuple(x for x in w.memberships if x.member != who),
        gates=tuple(g for g in w.gates if g.who != who),
        links=tuple(link for link in w.links if who not in (link.user, link.source)),
    )


def _user_deactivated(w: World, m: Mutation) -> World:
    """What a connector writes when the source drops a person: the member lists it re-reads lose them, and nothing
    else changes (their org membership, direct grants, gates and links stay)."""
    return replace(w, memberships=tuple(x for x in w.memberships if x.member != m.node))


def _gate_withdrawn(w: World, m: Mutation) -> World:
    """What a connector writes when it finds an account inactive: it removes the person's relation to its app."""
    return replace(w, gates=tuple(g for g in w.gates if g.who != m.node))


_APPLY: dict[str, Callable[[World, Mutation], World]] = {
    "grant_added": _grant_added,
    "grant_removed": _grant_removed,
    "member_added": _member_added,
    "member_removed": _member_removed,
    "rule_set": _rule_set,
    "inherit_set": _inherit_set,
    "moved": _moved,
    "deleted": _deleted,
    "user_removed": _user_removed,
    "user_deactivated": _user_deactivated,
    "gate_withdrawn": _gate_withdrawn,
}
assert set(_APPLY) == set(MUTATION_KINDS)


def apply(world: World, mutation: Mutation) -> World:
    try:
        return _APPLY[mutation.kind](world, mutation)
    except KeyError as exc:
        raise ValueError(f"unknown mutation kind or id in {mutation}: {exc}") from exc


def apply_all(world: World, mutations: tuple[Mutation, ...]) -> World:
    for m in mutations:
        world = apply(world, m)
    return world
