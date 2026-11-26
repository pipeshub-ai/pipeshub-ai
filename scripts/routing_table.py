"""The routing table (contracts/harness/vectors/routing.json) in Python, for the e2e oracle and its tests.

An input is one level per factor that satisfies the file's constraints; its answer is the `then` of the first rule
whose `when` matches. The file is the single source: Node holds every RoutingDecider adapter to it
(tests/modules/agent-harness/support/routing-vectors.ts), and this module is how the oracle reads it.
Standard library only.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "contracts/harness/vectors/routing.json"


@lru_cache(maxsize=1)
def table() -> dict:
    return json.loads(VECTORS.read_text())


def factors() -> dict[str, list[str]]:
    return {f["id"]: f["levels"] for f in table()["factors"]}


def _matches(inp: Mapping[str, str], selector: Mapping[str, list[str]]) -> bool:
    return all(inp[factor] in levels for factor, levels in selector.items())


def input_of(partial: Mapping[str, str]) -> dict[str, str]:
    return {**table()["defaults"], **partial}


def is_valid(inp: Mapping[str, str]) -> bool:
    levels = factors()
    return set(inp) == set(levels) and all(inp[f] in levels[f] for f in levels) and all(
        not _matches(inp, c["if"]) or _matches(inp, c["then"]) for c in table()["constraints"]
    )


def answer(inp: Mapping[str, str]) -> tuple[str, dict] | None:
    """(rule id, answer) of the first matching rule; None when no rule answers."""
    for rule in table()["rules"]:
        if _matches(inp, rule["when"]):
            return rule["id"], rule["then"]
    return None


def e2e_input(row: Mapping[str, str]) -> dict[str, str]:
    """The routing input of an e2e matrix row (or a partial one: an unassigned engine is none, a trigger interactive)."""
    e2e = table()["e2e"]
    return input_of({
        **e2e["modes"][row["mode"]],
        **e2e["paths"][row["path"]],
        **e2e["engines"][row.get("engine", "none")],
        **e2e["triggers"][row.get("trigger", "interactive")],
    })


def e2e_route(row: Mapping[str, str]) -> tuple[str, str]:
    """The oracle's (route, reason): an engine run is a durable route; e2e rows are never refused."""
    found = answer(e2e_input(row))
    if found is None or found[1]["route"] == "refused":
        raise ValueError(f"the routing table has no e2e route for {dict(row)}: {found}")
    then = found[1]
    return ("legacy", then["reason"]) if then["route"] == "legacy" else ("durable", "")


def e2e_modes(*, kind: str | None = None) -> set[str]:
    """The e2e modes the table routes durably on the default path (of one kind, when given)."""
    return {
        mode for mode, inp in table()["e2e"]["modes"].items()
        if (kind is None or inp["kind"] == kind) and e2e_route({"mode": mode, "path": "durable"})[0] == "durable"
    }


def e2e_engine_modes() -> set[str]:
    """The e2e modes whose turn can run an engine: the table admits an engine on them (an agent's mode, the assistant's
    Agent mode) and routes them durably on the default path, as a default-chat job is since ASSIST1."""
    engine = next(e for e in table()["e2e"]["engines"] if e != "none")
    out: set[str] = set()
    for mode in table()["e2e"]["modes"]:
        row = {"mode": mode, "path": "durable", "engine": engine}
        if is_valid(e2e_input(row)) and e2e_route(row)[0] == "durable":
            out.add(mode)
    return out
