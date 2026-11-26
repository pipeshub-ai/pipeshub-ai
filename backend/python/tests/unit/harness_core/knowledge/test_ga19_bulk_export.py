"""GA-19 (G-35, C20.36): a 10k-record export runs in the background, within its allowance, onto the run's knowledge
shelf, and is journaled.

BE-1 (GA-19) the start answers at once with a task id while the records are still being read; the status waits for
     it and names the JSONL file on the shelf, one line per record the source gave (10,000), in the source's order.
BE-2 the start reserves what it may write (``started``); the first status that reports the end journals ``finished``
     once, giving back what was not written; a status before a sandbox holds the shelf says to call again.
BE-3 the host's allowance caps the export (and is said); with none left nothing starts; a refusal of the
     arguments is the model's to read.
BE-4 a failed page ends it ``failed`` with nothing kept; the attempt closing cancels it; past the byte ceiling it
     stops ``limited``.
BE-5 the catalog lists the two tools beside the run's own, as reads into the run's own sandbox.
BE-6 the start tool tells the model the default limit, and names no setting the host may not have.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from harness_core.effects.types import ToolContext
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.knowledge.bulk_export import (
    BulkExports,
    BulkExportTools,
    ExportingCatalog,
    ExportRefusal,
    ExportToolNames,
    RecordPage,
    ShelfRelay,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ToolEffect, ToolExecution
    from harness_core.kernel.core.tool_schema import ToolSchema

NAMES = ExportToolNames(start="knowledgegraph__export_records", status="knowledgegraph__export_status", source="knowledgegraph")


class Source:
    """``total`` records the person may read, paged; ``gate`` holds the first page until it is set."""

    def __init__(self, total: int, *, fail_at: int | None = None, gate: asyncio.Event | None = None) -> None:
        self.total, self.fail_at, self.gate = total, fail_at, gate
        self.pages = 0

    async def __call__(self, cursor: str | None, limit: int) -> RecordPage:
        if self.gate is not None:
            await self.gate.wait()
        start = int(cursor or 0)
        self.pages += 1
        if self.fail_at is not None and start >= self.fail_at:
            raise ConnectionError("graph down")
        end = min(self.total, start + limit)
        rows = tuple({"id": f"r-{i}", "name": f"Ticket {i}", "recordType": "TICKET"} for i in range(start, end))
        return RecordPage(rows, str(end) if end < self.total else None)


class Endpoint:
    """A turn's endpoint whose sandbox holds the shelf."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    async def shelve(self, name: str, text: str, *, suffix: str = ".md") -> str | None:
        self.files[f"{name}{suffix}"] = text
        return f"/pipeshub/knowledge/{name}{suffix}"


class Catalog:
    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        return None

    def schemas(self) -> list[ToolSchema] | None:
        return None

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        raise AssertionError("not the run's own tool")


def tools(source: Source | ExportRefusal, *, allowance: int = 10_000, shelf: ShelfRelay | None = None,
          max_bytes: int = 48 * 1024 * 1024) -> tuple[BulkExportTools, ShelfRelay, Endpoint]:
    relay = shelf or ShelfRelay()
    endpoint = Endpoint()
    if shelf is None:
        relay.attach(endpoint)

    async def pages_for(arguments: Mapping[str, JsonValue]) -> Source | ExportRefusal:
        del arguments
        return source

    def event(data: dict[str, JsonValue]) -> NewEvent:
        return NewEvent("x.pipeshub.knowledge.exported", data, critical=False)

    return (
        BulkExportTools(
            NAMES, BulkExports(relay, max_bytes=max_bytes), pages_for, lambda: allowance, event,
            shelf_path="/pipeshub/knowledge",
        ),
        relay,
        endpoint,
    )


def _ctx(invocation: str) -> ToolContext:
    return ToolContext("run-1", "org-1", invocation, "attempt-1", f"key:{invocation}")


async def _start(t: BulkExportTools, arguments: dict[str, JsonValue] | None = None) -> tuple[ToolExecution, dict]:
    executed = await t.execute(ToolCall(id="inv-1", name=NAMES.start, arguments=arguments or {}), _ctx("inv-1"))
    return executed, json.loads(str(executed.content)) if executed.status == "ok" else {}


async def _status(t: BulkExportTools, task_id: str, wait: int = 5) -> tuple[ToolExecution, dict]:
    executed = await t.execute(
        ToolCall(id="inv-s", name=NAMES.status, arguments={"task_id": task_id, "wait_seconds": wait}), _ctx("inv-s")
    )
    return executed, json.loads(str(executed.content)) if executed.status == "ok" else {}


async def test_be1_ga19_ten_thousand_records_export_in_the_background_onto_the_shelf() -> None:
    gate = asyncio.Event()
    source = Source(25_000, gate=gate)
    t, _, endpoint = tools(source)
    started, answer = await _start(t, {"limit": 10_000})
    # Answered before a single page was read.
    assert started.status == "ok"
    assert source.pages == 0
    assert answer["status"] == "working"
    assert answer["file"] == f"/pipeshub/knowledge/export-{answer['taskId']}.jsonl"
    gate.set()
    _, status = await _status(t, answer["taskId"], wait=30)
    assert status["status"] == "limited"
    assert status["records"] == 10_000
    assert status["file"] == answer["file"]
    lines = endpoint.files[f"export-{answer['taskId']}.jsonl"].splitlines()
    assert len(lines) == 10_000
    assert json.loads(lines[0]) == {"id": "r-0", "name": "Ticket 0", "recordType": "TICKET"}
    assert json.loads(lines[-1])["id"] == "r-9999"


async def test_be2_ga19_the_reserve_is_journaled_at_start_and_settled_once_at_the_end() -> None:
    t, _, _ = tools(Source(9_880))
    started, answer = await _start(t)
    [reserve] = started.events
    assert reserve.type == "x.pipeshub.knowledge.exported"
    assert reserve.critical is False
    assert reserve.data == {
        "taskId": answer["taskId"], "phase": "started", "records": 10_000, "requested": 10_000,
        "argsDigest": reserve.data["argsDigest"],
    }
    first, status = await _status(t, answer["taskId"])
    assert status["status"] == "completed"
    [settled] = first.events
    assert settled.data == {
        "taskId": answer["taskId"], "phase": "finished", "records": -120, "requested": 10_000, "exported": 9_880,
        "outcome": "completed", "argsDigest": reserve.data["argsDigest"],
    }
    again, _ = await _status(t, answer["taskId"])
    assert again.events == ()


async def test_be2_a_status_before_the_shelf_is_attached_says_to_call_again_then_writes_it() -> None:
    relay = ShelfRelay()
    t, relay, endpoint = tools(Source(50), shelf=relay)
    _, answer = await _start(t)
    early, status = await _status(t, answer["taskId"])
    assert status["status"] == "working"
    assert "Call again" in status["note"]
    assert early.events == ()
    relay.attach(endpoint)
    later, status = await _status(t, answer["taskId"])
    assert status["status"] == "completed"
    assert status["file"].endswith(".jsonl")
    assert len(later.events) == 1
    relay.detach(endpoint)
    assert await relay.write("x", "y") is None


async def test_be3_ga19_the_allowance_caps_the_export_and_none_left_starts_nothing() -> None:
    t, _, _ = tools(Source(5_000), allowance=1_200)
    started, answer = await _start(t, {"limit": 4_000})
    assert answer["limit"] == 1_200
    assert "Limited to 1,200 records" in answer["note"]
    assert started.events[0].data["records"] == 1_200
    _, status = await _status(t, answer["taskId"])
    assert (status["status"], status["records"]) == ("limited", 1_200)

    none_left, _, _ = tools(Source(5), allowance=0)
    refused, _ = await _start(none_left)
    assert refused.status == "error"
    assert "No more records may be exported" in str(refused.content)
    assert refused.events == ()

    scoped, _, _ = tools(ExportRefusal("This conversation is limited to items the user selected"))
    refused, _ = await _start(scoped)
    assert refused.status == "error"
    assert "limited to items the user selected" in str(refused.content)
    assert refused.events == ()


async def test_be4_a_failed_page_a_closed_attempt_and_the_byte_ceiling_end_an_export() -> None:
    failing, _, endpoint = tools(Source(1_000, fail_at=400))
    _, answer = await _start(failing)
    settled, status = await _status(failing, answer["taskId"])
    assert (status["status"], status["records"]) == ("failed", 0)
    assert settled.events[0].data["records"] == -10_000
    assert settled.events[0].data["outcome"] == "failed"
    assert endpoint.files == {}

    gate = asyncio.Event()
    t, _, _ = tools(Source(100, gate=gate))
    _, answer = await _start(t)
    await t.exports.close()
    cancelled, status = await _status(t, answer["taskId"], wait=0)
    assert status["status"] == "cancelled"
    assert cancelled.events[0].data["outcome"] == "cancelled"

    small, _, endpoint = tools(Source(1_000), max_bytes=4_000)
    _, answer = await _start(small)
    _, status = await _status(small, answer["taskId"])
    assert status["status"] == "limited"
    assert 0 < status["records"] < 1_000
    assert len(endpoint.files[f"export-{answer['taskId']}.jsonl"].encode()) <= 4_000

    unknown, _ = await _status(t, "exp-nope")
    assert unknown.status == "error"
    assert "ended with the run that started it" in str(unknown.content)


async def test_be5_the_catalog_lists_the_export_tools_as_reads_into_the_runs_own_sandbox() -> None:
    t, _, _ = tools(Source(1))
    catalog = ExportingCatalog(Catalog(), t)
    assert [s.name for s in catalog.schemas() or []] == [NAMES.start, NAMES.status]
    effect = catalog.describe_call(ToolCall(id="c", name=NAMES.start, arguments={}))
    assert effect is not None
    assert (effect.tool_id, effect.effect, effect.sink.egress) == ("knowledgegraph@default/export_records", "read", "none")
    assert effect.risk == "read"
    assert catalog.describe_call(ToolCall(id="c", name="other", arguments={})) is None


def test_be6_the_start_tool_states_the_default_limit_and_names_no_setting() -> None:
    t, _, _ = tools(Source(1))
    start = next(s for s in t.schemas() if s.name == NAMES.start)
    assert "up to the export limit, 10,000 by default" in start.description
    assert "admin" not in start.description.lower()
    assert "organization's" not in start.description
