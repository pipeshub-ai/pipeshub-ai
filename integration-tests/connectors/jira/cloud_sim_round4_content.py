# pyright: ignore-file

"""Helpers of the round-4 content suite: simulator faults that change an answer's JSON (``shape``) and the page-token
fault with its page size, and a look at what the simulator's fault log says fired."""

from __future__ import annotations

from typing import Any

from connectors.jira.cloud_sim_world import JiraCloudWorld, _rule

SEARCH = r"^/rest/api/3/search/jql$"
SEARCH_OR_BULK = r"^/rest/api/3/(search/jql|issue/bulkfetch)$"


def set_(path: str, value: Any) -> dict[str, Any]:
    return {"path": path, "value": value}


def delete(path: str) -> dict[str, Any]:
    return {"path": path, "delete": True}


def shape(path_regex: str, *ops: dict[str, Any], method: str | None = None, **trigger: float) -> dict[str, Any]:
    """The answer to requests of this path passes through with these fields set or deleted (Jira's shape of an answer
    the product must survive: a warning, a missing field)."""
    return _rule({"type": "shape", "ops": list(ops)}, path_regex, method, trigger)


def empty_pages(path_regex: str, *, repeat_token: bool = False) -> dict[str, Any]:
    """Pages of at most 7 issues, and the second page of every walk an empty one that is not the last: with a fresh token
    for the same place, or (``repeat_token``) with the caller's own token again."""
    effect: dict[str, Any] = {"type": "page_token", "mode": "empty", "page_size": 2 if repeat_token else 7}
    return _rule(effect, path_regex, None, {})


def fired(w: JiraCloudWorld) -> list[dict[str, Any]]:
    """What the simulator's fault log says was injected so far."""
    return list(w.sim.call("GET", "/_sim/faults/log") or [])


def assert_shaped(w: JiraCloudWorld, what: str) -> None:
    """The shape fault really changed an answer (the simulator logs ``shape-not-applied-...`` for a body it could not)."""
    kinds = [str(e.get("effect") or e.get("kind") or e) for e in fired(w)]
    assert any("shape" in k and "not-applied" not in k for k in kinds), f"{what}: no answer was reshaped: {kinds[:5]}"
