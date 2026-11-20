"""The start-up audit of indexed PDFs over the page limit."""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.modules.indexing import pdf_page_limit_audit as audit
from app.utils.user_errors import MAX_INDEXED_PDF_PAGES, PDF_PAGE_LIMIT_NOTE

BIG = 5 << 20


@pytest.fixture(autouse=True)
def _restore_page_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "PAGE_SIZE", audit.PAGE_SIZE)


def pages_json(last: int) -> bytes:
    return b'{"isCompressed": false, "record": {"blocks": [' + b",".join(
        b'{"page_number": %d}' % n for n in range(1, last + 1)
    ) + b"]}}"


class Config:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get_config(self, key: str, **_kw: object) -> object:
        return self.data.get(key)

    async def set_config(self, key: str, value: object) -> bool:
        self.data[key] = value
        return True

    async def delete_config(self, key: str) -> bool:
        self.data.pop(key, None)
        return True


class Lock:
    def __init__(self, *, acquire: bool = True) -> None:
        self.acquire, self.acquired, self.released, self.refreshes = acquire, False, False, 0

    async def try_acquire(self) -> bool:
        self.acquired = self.acquire
        return self.acquire

    async def refresh(self) -> bool:
        self.refreshes += 1
        return True

    async def release(self) -> None:
        self.released = True


class Graph:
    def __init__(self, records: list[dict], page: int = 200) -> None:
        self.records = sorted(records, key=lambda r: r["_key"])
        self.apps = [{"_key": "app-1", "orgId": "org-1"}]
        self.writes: list[tuple] = []
        audit.PAGE_SIZE = page
        self.race: dict[str, Any] = {}

    async def get_documents_paginated(self, collection, limit=50, filters=None, sort_field=None, after_key=None, **_kw) -> list[dict[str, Any]]:
        if collection == "apps":
            return [a for a in self.apps if after_key is None or a["_key"] > after_key][:limit]
        rows = [r for r in self.records if r["connectorId"] == filters["connectorId"] and r["indexingStatus"] == "COMPLETED"]
        return [dict(r) for r in rows if after_key is None or r["_key"] > after_key][:limit]

    async def update_node_fields_if_match(self, key, collection, updates, expected) -> bool:
        row = next(r for r in self.records if r["_key"] == key)
        row.update(self.race)
        if any(row.get(k) != v for k, v in expected.items()):
            return False
        row.update(updates)
        self.writes.append((key, updates, expected))
        return True


def pdf(key: str, **fields: object) -> dict:
    return {
        "_key": key, "connectorId": "app-1", "indexingStatus": "COMPLETED", "extension": "pdf", "virtualRecordId": f"v-{key}",
        "contentRev": "r", "sizeInBytes": BIG, **fields,
    }


async def run(graph: Graph, reader: object, config: Config | None = None, lock: Lock | None = None, **kw: object) -> tuple[object, Config, Lock]:
    config = config or Config()
    lock = lock or Lock()
    result = await audit.run_pdf_page_limit_audit(config, graph, reader, logging.getLogger("t"), lock=lock, **kw)
    return result, config, lock


def reader_of(bodies: dict[str, bytes]) -> AsyncMock:
    async def read(vrid: str, org_id: str, max_bytes: int) -> tuple[bytes, int] | None:
        body = bodies.get(vrid)
        return None if body is None else (body[:max_bytes], len(body))

    return AsyncMock(side_effect=read)


@pytest.mark.asyncio
async def test_a_stored_page_count_is_used_without_any_blob_read() -> None:
    graph = Graph([pdf("a", pageCount=900), pdf("b", pageCount=30)])
    reader = reader_of({})
    result, config, lock = await run(graph, reader)
    reader.assert_not_awaited()
    assert [w[0] for w in graph.writes] == ["a"]
    assert graph.writes[0][1] == {"indexingNote": PDF_PAGE_LIMIT_NOTE}
    assert result.over_limit == 1 and result.complete
    assert config.data[audit.FLAG_KEY] and audit.CURSOR_KEY not in config.data
    assert lock.released


@pytest.mark.asyncio
async def test_no_blob_is_read_below_the_size_floor() -> None:
    graph = Graph([pdf("a", sizeInBytes=audit.SIZE_FLOOR_BYTES - 1)])
    reader = reader_of({})
    await run(graph, reader)
    reader.assert_not_awaited()
    assert graph.writes == []


@pytest.mark.asyncio
async def test_the_scan_reads_at_most_the_cap_and_a_file_not_proven_is_not_checked() -> None:
    graph = Graph([pdf("a"), pdf("b")])
    reader = reader_of({"v-a": pages_json(600), "v-b": pages_json(40)})
    result, *_ = await run(graph, reader)
    assert [w[0] for w in graph.writes] == ["a"]
    assert result.not_checked == 1 and result.scanned == 2
    assert all(call.args[2] == audit.SCAN_CAP_BYTES for call in reader.await_args_list)


def test_the_scan_stops_at_the_first_page_over_the_limit() -> None:
    assert audit.first_page_over_limit(pages_json(MAX_INDEXED_PDF_PAGES)) is None
    assert audit.first_page_over_limit(pages_json(MAX_INDEXED_PDF_PAGES + 1)) == MAX_INDEXED_PDF_PAGES + 1


@pytest.mark.asyncio
async def test_a_compressed_envelope_is_classified_from_its_first_bytes_and_counted_apart() -> None:
    graph = Graph([pdf("a")])
    reader = reader_of({"v-a": b'{"isCompressed": true, "record": "' + b'"page_number": 999 ' * 10})
    result, *_ = await run(graph, reader)
    assert result.compressed == 1 and result.not_checked == 0
    assert graph.writes == []
    assert "1 large records not checked" in result.line()


@pytest.mark.asyncio
async def test_a_record_that_changed_between_the_read_and_the_write_is_left_alone() -> None:
    graph = Graph([pdf("a", pageCount=900)])
    graph.race = {"contentRev": "new", "virtualRecordId": "v-new"}
    await run(graph, reader_of({}))
    assert graph.writes == [] and "indexingNote" not in graph.records[0]


@pytest.mark.asyncio
async def test_the_write_expects_the_status_vrid_and_revision_it_read() -> None:
    graph = Graph([pdf("a", pageCount=900)])
    await run(graph, reader_of({}))
    assert graph.writes[0][2] == {"indexingStatus": "COMPLETED", "virtualRecordId": "v-a", "contentRev": "r"}


@pytest.mark.asyncio
async def test_non_pdf_and_not_completed_records_are_not_touched() -> None:
    graph = Graph([pdf("a", extension="docx", pageCount=900), pdf("b", indexingStatus="FAILED", pageCount=900)])
    await run(graph, reader_of({}))
    assert graph.writes == []


@pytest.mark.asyncio
async def test_the_flag_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDF_PAGE_LIMIT_AUDIT", "false")
    graph = Graph([pdf("a", pageCount=900)])
    result, config, lock = await run(graph, reader_of({}))
    assert not result.ran and graph.writes == [] and not lock.acquired and audit.FLAG_KEY not in config.data


@pytest.mark.asyncio
async def test_without_the_lock_it_does_nothing() -> None:
    graph = Graph([pdf("a", pageCount=900)])
    result, config, _ = await run(graph, reader_of({}), lock=Lock(acquire=False))
    assert not result.ran and graph.writes == []


@pytest.mark.asyncio
async def test_a_finished_audit_does_not_run_again() -> None:
    graph = Graph([pdf("a", pageCount=900)])
    config = Config()
    await run(graph, reader_of({}), config)
    graph.writes.clear()
    again, *_ = await run(graph, reader_of({}), config)
    assert not again.ran and graph.writes == []


@pytest.mark.asyncio
async def test_the_budget_stops_a_pass_and_the_cursor_resumes_it() -> None:
    graph = Graph([pdf(f"k{n}", pageCount=900) for n in range(5)], page=2)
    config = Config()
    ticks = iter([0.0] + [100.0] * 50)
    first, *_ = await run(graph, reader_of({}), config, clock=lambda: next(ticks), budget_seconds=10.0)
    assert not first.complete and audit.FLAG_KEY not in config.data
    assert config.data[audit.CURSOR_KEY]["after"]
    done = {w[0] for w in graph.writes}
    assert 0 < len(done) < 5

    second, *_ = await run(graph, reader_of({}), config)
    assert second.complete and {w[0] for w in graph.writes} == {f"k{n}" for n in range(5)}
    assert len([w for w in graph.writes if w[0] in done]) == len(done), "a record already noted is not written twice"


@pytest.mark.asyncio
async def test_the_log_line_states_the_counts(caplog: pytest.LogCaptureFixture) -> None:
    graph = Graph([pdf("a", pageCount=900)])
    with caplog.at_level(logging.INFO, logger="t"):
        await run(graph, reader_of({}))
    assert any(
        f"At least 1 indexed PDFs have more than {MAX_INDEXED_PDF_PAGES} pages" in r.message and "scanned" in r.message
        and "not checked" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_a_failed_read_is_counted_not_fatal() -> None:
    graph = Graph([pdf("a")])
    result, *_ = await run(graph, AsyncMock(side_effect=RuntimeError("storage down")))
    assert result.not_checked == 1 and result.complete


def test_the_scan_cap_and_size_floor_are_the_documented_sizes() -> None:
    """A change to either is a decision about start-up cost, not a refactor."""
    assert audit.SCAN_CAP_BYTES == 8 << 20
    assert audit.SIZE_FLOOR_BYTES == 1 << 20


@pytest.mark.asyncio
async def test_the_reader_is_never_asked_for_more_than_eight_mib() -> None:
    graph = Graph([pdf("a")])
    reader = reader_of({"v-a": pages_json(10)})
    await run(graph, reader)
    assert [call.args[2] for call in reader.await_args_list] == [8 << 20]
