"""Where browse lists a node shared on its own below a gap.

A chain-top is a node the user holds a direct OPEN grant on that has no hierarchy
parent the user can open. Browse lists it under each of its own record groups the
user can open; failing that, directly under the App, so every chain-top the user
may open is listed, and under the App only when nothing else lists it. A record
group's own group is its parent group.
"""

from collections import defaultdict
from typing import Any


def place_chain_tops(
    candidates: list[dict[str, Any]], admitted: frozenset[str] | set[str], app_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Candidates by the node browse lists them under (``app_id`` for the App).

    Each candidate is ``{id, parents, ownGroups: [{id, ...}]}``: ``parents`` are
    its hierarchy parents (the App included when it hangs off the App), and
    ``admitted`` the ids among parents and own groups the user can open. A
    candidate with an openable parent is listed under that parent by the
    ordinary walk, so it is not placed here.
    """
    placed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        parents = set(candidate.get("parents") or ())
        if app_id in parents or parents & admitted:
            continue
        own = {g["id"] for g in candidate.get("ownGroups") or () if g.get("id") and g["id"] != candidate["id"]}
        for group_id in sorted(own & admitted) or [app_id]:
            placed[group_id].append(candidate)
    return dict(placed)
