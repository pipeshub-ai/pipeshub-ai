"""Where browse lists a node shared on its own below a gap.

A chain-top is a node the user holds a direct OPEN grant on that has no hierarchy
parent the user can open. Browse lists it under each of its own record groups the
user can open; failing that, directly under the App, but only when it has no own
group or one of them is structurally under that App and not deleted. A record
group's own group is its parent group.
"""

from collections import defaultdict
from typing import Any


def place_chain_tops(
    candidates: list[dict[str, Any]], admitted: frozenset[str] | set[str], app_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Candidates by the node browse lists them under (``app_id`` for the App).

    Each candidate is ``{id, parents, ownGroups: [{id, deleted, underApp}]}``:
    ``parents`` are its hierarchy parents (the App included when it hangs off
    the App), and ``admitted`` the ids among parents and own groups the user can
    open. A candidate with an openable parent is listed under that parent by the
    ordinary walk, so it is not placed here.
    """
    placed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        parents = set(candidate.get("parents") or ())
        if app_id in parents or parents & admitted:
            continue
        own = [g for g in candidate.get("ownGroups") or () if g.get("id") and g["id"] != candidate["id"]]
        openable = sorted({g["id"] for g in own if g["id"] in admitted})
        if openable:
            for group_id in openable:
                placed[group_id].append(candidate)
        elif not own or any(g.get("underApp") and not g.get("deleted") for g in own):
            placed[app_id].append(candidate)
    return dict(placed)
