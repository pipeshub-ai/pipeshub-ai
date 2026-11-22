"""Graph writes per index cycle, per engine: the cost of the stored-write protocol.

With both read flags off (the default) a stored write must cost the graph what it cost before
the copy-on-write protocol: the same number of writes per index, per classification and per
reconciliation. With a flag on, a stored write costs one more compare-and-set than that. The
figures are written to ``ARTIFACTS_IT_FIGURES_OUT`` (JSON) so two checkouts can be compared; the
same test file runs on both. Needs the storage service and a graph, as the other stored-write
end-to-end tests do.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
from typing import Any

import pytest

from app.models.blocks import SemanticMetadata
from app.modules.transformers.transformer import TransformContext
from tests.integration import stored_write_world
from tests.integration.stored_write_world import A, B, World, _rev

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")
FIGURES_OUT = os.environ.get("ARTIFACTS_IT_FIGURES_OUT", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(600),
    pytest.mark.skipif(not STORAGE_URL, reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

world = stored_write_world.world

WRITE_PREFIXES = (
    "batch_upsert", "batch_update", "update_", "create_", "delete_", "upsert_", "remove_", "set_", "insert_",
    "add_", "clear_", "mark_", "move_", "replace_",
)

#: Graph writes of origin/main 708b2b66b for the same sequence, flags off, measured by running this file on it.
MAIN_WRITES = {
    "first_index": 2,
    "next_index": 2,
    "classification": 1,
    "reconciliation": 1,
}


def _is_write(name: str) -> bool:
    return name.startswith(WRITE_PREFIXES)


def _count_calls(provider: Any, counts: dict[str, int]) -> list[tuple[str, Any]]:  # noqa: ANN401
    patched = []
    for name, _fn in inspect.getmembers(type(provider), inspect.iscoroutinefunction):
        if name.startswith("_"):
            continue
        bound = getattr(provider, name)

        def wrap(bound: Any = bound, name: str = name) -> Any:  # noqa: ANN401
            async def counted(*a: Any, **kw: Any) -> Any:  # noqa: ANN401
                counts[name] = counts.get(name, 0) + 1
                return await bound(*a, **kw)
            return counted

        setattr(provider, name, wrap())
        patched.append((name, bound))
    return patched


def _figures(key: str, value: Any) -> None:  # noqa: ANN401
    if not FIGURES_OUT:
        return
    data: dict[str, Any] = {}
    with contextlib.suppress(FileNotFoundError, json.JSONDecodeError), open(FIGURES_OUT) as f:
        data = json.load(f)
    data[key] = value
    with open(FIGURES_OUT, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


async def _measure(world: World, step: Any) -> dict[str, int]:  # noqa: ANN401
    counts: dict[str, int] = {}
    patched = _count_calls(world.provider, counts)
    try:
        await step()
    finally:
        for name, bound in patched:
            setattr(world.provider, name, bound)
    return counts


def _writes(counts: dict[str, int]) -> int:
    return sum(n for name, n in counts.items() if _is_write(name))


async def _cycle(world: World) -> dict[str, dict[str, int]]:
    vrid = world.vrid()
    first, second = world.record(vrid, A), world.record(vrid, B)
    await world.put_record_node(first, _rev(A))
    out: dict[str, dict[str, int]] = {}

    async def index(record: Any) -> None:  # noqa: ANN401
        await world.sink.index(TransformContext(record=record.model_copy(deep=True), settings={}))

    out["first_index"] = await _measure(world, lambda: index(first))
    await world.put_record_node(second, _rev(B))
    out["next_index"] = await _measure(world, lambda: index(second))
    out["classification"] = await _measure(
        world, lambda: world.classify(second, _rev(B), SemanticMetadata(summary="s", categories=["x"])),
    )
    out["reconciliation"] = await _measure(
        world, lambda: world.blob.save_reconciliation_metadata(world.org, second.id, vrid, {"k": 1}),
    )
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("write_flag", [False, True], ids=["write_off", "write_on"])
async def test_flags_off_graph_writes_per_index_cycle_equal_main(world: World, write_flag: bool) -> None:
    """T57. Both read flags off: the same graph writes per index, classification and reconciliation as main."""
    world.read_mode = "legacy"
    world.write_on = write_flag
    counts = await _cycle(world)

    engine = type(world.provider).__name__
    writes = {step: _writes(c) for step, c in counts.items()}
    _figures(f"graph_writes.{engine}.flags_off.write_{'on' if write_flag else 'off'}", {"writes": writes, "calls": counts})
    if write_flag:
        # With the write flag on main also writes the set and the pointer; that cost is not the protocol's.
        return
    assert writes == MAIN_WRITES, (writes, counts)


@pytest.mark.asyncio
async def test_flags_on_cost_one_more_compare_and_set_per_stored_write(world: World) -> None:
    """T57. A flag on: the copy-on-write sink costs the pointer flip and the switch, nothing else per write."""
    world.read_mode = "artifact"
    world.write_on = True
    counts = await _cycle(world)

    engine = type(world.provider).__name__
    _figures(
        f"graph_writes.{engine}.flags_on",
        {"writes": {step: _writes(c) for step, c in counts.items()}, "calls": counts},
    )
    # Switch + flip are two compare-and-sets; main's single upsert became the switch, the flip is the set's pointer.
    assert counts["next_index"].get("update_node_fields_if_match", 0) == 2
    assert counts["next_index"].get("batch_upsert_nodes", 0) == 0
