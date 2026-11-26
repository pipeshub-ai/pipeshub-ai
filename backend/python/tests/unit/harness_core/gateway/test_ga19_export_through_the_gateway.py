"""GA-19 through the Tool Gateway, as an engine reaches it: ``RunToolEndpoint`` over the run's catalog with the
export tools beside it.

GE-1 (GA-19) the engine sees the export tools in ``tools/list``; a 10k export starts, its status names the JSONL file
     on the shelf, and the outcome of each carries its export event (the journal's, so the audit chain's).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harness_core.effects import EffectRunner, RequestEffectLog
from harness_core.gateway.endpoint import RunToolEndpoint
from harness_core.gateway.memory import InMemoryCitationSink
from harness_core.invoker import InvocationScope
from harness_core.journal.types import NewEvent
from harness_core.kernel.durable import classify_tool_effect
from harness_core.knowledge.bulk_export import (
    BulkExports,
    BulkExportTools,
    ExportingCatalog,
    ExportToolNames,
    RecordPage,
    ShelfRelay,
)
from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from harness_core.testing.ids import SeededIds
from harness_core.tools.base import ParameterType, Tag, ToolParameter
from harness_core.tools.decorators import tool
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.toolset import ToolsetBuilder

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue


NAMES = ExportToolNames("knowledgegraph__export_records", "knowledgegraph__export_status", "knowledgegraph")
_Q = [ToolParameter(name="query", type=ParameterType.STRING, description="Query", required=True)]


class Knowledge:
    @tool(path="/tools/knowledgegraph/search", short_description="Search", description="Search.", parameters=_Q)
    async def search(self, query: str) -> str:
        return f"results for {query}"


async def pages(cursor: str | None, limit: int) -> RecordPage:
    start = int(cursor or 0)
    end = min(12_000, start + limit)
    return RecordPage(tuple({"id": f"r-{i}"} for i in range(start, end)), str(end) if end < 12_000 else None)


class Shelf:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    async def shelve(self, name: str, text: str, *, suffix: str = ".md") -> str | None:
        self.files[name + suffix] = text
        return f"/pipeshub/knowledge/{name}{suffix}"


# The most records one export writes when no gate narrows it (``app.harness.knowledge.access_gate``).
MAX_EXPORT_RECORDS = 10_000


def endpoint() -> tuple[RunToolEndpoint, RequestEffectLog, Shelf]:
    registry = ToolRegistry()
    ToolsetBuilder(Knowledge(), name="knowledgegraph", description="Knowledge", path_prefix="/tools/knowledgegraph").register_into(registry)
    port = RegistryToolPort(registry, {"knowledgegraph__search": classify_tool_effect("knowledgegraph__search", [Tag("category", "read")])})
    relay, shelf = ShelfRelay(), Shelf()
    relay.attach(shelf)

    async def pages_for(arguments: Mapping[str, JsonValue]) -> object:
        del arguments
        return pages

    tools = BulkExportTools(
        NAMES, BulkExports(relay), pages_for,  # type: ignore[arg-type]
        lambda: MAX_EXPORT_RECORDS,
        lambda data: NewEvent("x.pipeshub.knowledge.exported", data, critical=False),
    )
    log = RequestEffectLog()
    run = RunToolEndpoint(
        binding=BINDING,
        catalog=ExportingCatalog(port, tools),
        effects=EffectRunner(log, lambda call, effect: "allow"),
        scope=InvocationScope(BINDING.run_id, BINDING.tenant, BINDING.attempt_id),
        ids=SeededIds(),
        citations=InMemoryCitationSink(),
        hold_s=0.01,
    )
    return run, log, shelf


async def test_ge1_ga19_an_engine_exports_ten_thousand_records_through_the_gateway() -> None:
    run, log, shelf = endpoint()
    listed = {t.name for t in await run.list_tools(claims_for(BINDING))}
    assert {NAMES.start, NAMES.status, "knowledgegraph__search"} <= listed
    started = await run.call_tool(claims_for(BINDING), NAMES.start, {})
    assert not started.is_error, started.text
    task = json.loads(started.text)["taskId"]
    status = await run.call_tool(claims_for(BINDING), NAMES.status, {"task_id": task, "wait_seconds": 30})
    answer = json.loads(status.text)
    assert (answer["status"], answer["records"]) == ("limited", 10_000)
    assert len(shelf.files[f"export-{task}.jsonl"].splitlines()) == 10_000
    phases = [e.data["phase"] for o in log.outcomes() for e in o.events if e.type == "x.pipeshub.knowledge.exported"]
    assert phases == ["started", "finished"]

