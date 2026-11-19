"""Every code-graph tool call is counted by tool and outcome, whichever surface made it."""
import inspect
from typing import NoReturn

import pytest

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
from app.agents.actions.code_graph.code_graph import CodeGraph
from app.telemetry.backend import METRICS_BACKEND

from .conftest import CONN, ORG, USER, FakeGraphProvider

pytestmark = pytest.mark.asyncio

TARGET = {"connector_id": CONN, "file_path": "src/b.py", "qualified_name": "function:target"}
REGISTERED = sorted(n for n in dir(CodeGraph) if hasattr(getattr(CodeGraph, n), TOOL_META_ATTR))


def _count(tool: str, outcome: str) -> float:
    line = f'pipeshub_code_graph_tool_calls_total{{outcome="{outcome}",tool="{tool}"}} '
    hits = [row for row in METRICS_BACKEND.serialize().splitlines() if row.startswith(line)]
    return float(hits[0].rsplit(" ", 1)[1]) if hits else 0.0


@pytest.fixture
def tool() -> CodeGraph:
    METRICS_BACKEND.reset()
    return CodeGraph({"org_id": ORG, "user_id": USER, "graph_provider": FakeGraphProvider(),
                      "blob_store": object()})


async def test_each_call_increments_its_tools_ok_count(tool) -> None:
    for expected in (1.0, 2.0):
        ok, _ = await tool.impact(**TARGET, edge_types=["INHERITS"])
        assert ok
        assert _count("impact", "ok") == expected
    assert _count("get_neighbour", "ok") == 0.0


async def test_a_refused_call_counts_as_an_error(tool) -> None:
    ok, _ = await tool.impact(**TARGET, edge_types=["NOPE"])
    assert not ok
    assert _count("impact", "error") == 1.0 and _count("impact", "ok") == 0.0


async def test_a_call_refused_before_the_walk_is_counted_too(tool) -> None:
    """A missing anchor returns before the graph is read; it is still a call the agent made."""
    ok, _ = await tool.read_code(connector_id=CONN)
    assert not ok
    assert _count("read_code", "error") == 1.0


async def test_an_exception_counts_as_an_error_and_still_propagates(tool, monkeypatch) -> None:
    async def boom(*_args, **_kwargs) -> NoReturn:
        raise RuntimeError("no graph")

    monkeypatch.setattr(tool, "_anchor_path", boom)
    with pytest.raises(RuntimeError, match="no graph"):
        await tool.outline(connector_id=CONN, file_path="src/b.py")
    assert _count("outline", "error") == 1.0


@pytest.mark.parametrize("name", REGISTERED)
async def test_every_tool_is_counted_and_keeps_its_metadata_and_signature(tool, name) -> None:
    method = getattr(CodeGraph, name)
    assert hasattr(method, TOOL_META_ATTR), name
    assert "connector_id" in inspect.signature(method).parameters, name
    await getattr(tool, name)(connector_id="not-this-connector", **_required(method))
    assert _count(name, "ok") + _count(name, "error") == 1.0, name


def _required(method) -> dict[str, str]:
    """Placeholder values for a tool's required arguments other than connector_id."""
    params = inspect.signature(method).parameters.values()
    return {p.name: "x" for p in params
            if p.default is inspect.Parameter.empty and p.name not in ("self", "connector_id")
            and p.kind is not inspect.Parameter.VAR_KEYWORD}
