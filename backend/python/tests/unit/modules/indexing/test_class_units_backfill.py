"""Class-units backfill: code records indexed before class header and residue units existed gain them once.

It reads each record's stored blob and embeds only those units: no source fetch, no parse, no reconcile,
no block point, no pipeline stage (a connector reindex would force the LLM stages). Selection is exact: a
record is embedded only when the vector store has no residue unit for it.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import org_context
from app.models.blocks import BlocksContainer
from app.modules.code_graph.version import EMBED_UNITS_VERSION
from app.modules.indexing import class_units_backfill as backfill
from app.modules.parsers.code_parser import CodeFileParser
from app.modules.pipeline.fingerprint import content_facts
from app.modules.transformers.vectorstore import VectorStore
from app.services.vector_db.membership import set_membership_context
from app.services.vector_db.models import ScrollResult, VectorPoint
from tests.unit.modules.transformers.test_vectorstore_deep import _make_vectorstore

pytestmark = pytest.mark.asyncio

WITH_CLASSES = b'class Client:\n    """Talks to the API."""\n\n    def fetch(self, key):\n        return key\n'
NO_CLASSES = b"def helper():\n    return 1\n"
FLAG = f"{backfill.FLAG_PREFIX}{EMBED_UNITS_VERSION}"


def _container(src: bytes) -> BlocksContainer:
    return CodeFileParser().parse_to_blocks(src, "client.py", "src/client.py", "python")


class Graph:
    """Records, paged as the provider pages them (equality filters, keyset on _key)."""

    def __init__(self, records: dict[str, tuple[bytes, str]], record_type: str = "CODE_FILE") -> None:
        # record id -> (source, indexingStatus)
        self.records = {rid: {"_key": rid, "orgId": "org-1", "virtualRecordId": f"vr-{rid}", "connectorId": "conn-1",
                              "connectorName": "GITHUB", "recordName": "client.py",
                              "externalRecordId": "src/client.py", "recordType": record_type,
                              "indexingStatus": status} for rid, (_, status) in records.items()}
        self.pages: list[str | None] = []
        self.read_as: list[tuple[str, str | None]] = []  # (org asked for, org bound) per page read
        self.fail_on_page: int | None = None

    def org_ids(self) -> list[str]:
        return sorted({r["orgId"] for r in self.records.values()})

    async def get_documents_paginated(self, collection, limit=50, filters=None, sort_field=None, *,
                                      after_key=None, return_fields=None, raise_on_error=False, **_) -> list[dict]:
        assert collection == "records" and sort_field == "_key"
        assert {k: v for k, v in filters.items() if k != "orgId"} == {"recordType": "CODE_FILE",
                                                                       "indexingStatus": "COMPLETED"}
        # A store that keeps every row under an org (PostgreSQL) reads none under a system scope.
        self.read_as.append((filters["orgId"], org_context.current_org_id()))
        self.pages.append(after_key)
        if self.fail_on_page is not None and len(self.pages) == self.fail_on_page:
            raise RuntimeError("graph went away")
        rows = sorted((r for r in self.records.values()
                       if all(r.get(k) == v for k, v in filters.items()) and (after_key is None or r["_key"] > after_key)),
                      key=lambda r: r["_key"])
        return rows[:limit]


class Blobs:
    """Stored blobs: each record's parse is kept, so its group ids are the same on every read until
    ``reindex`` stores a new parse (new ids), as a reconcile does."""

    def __init__(self, records: dict[str, bytes | None | Exception]) -> None:
        self.records = records
        self.reads: list[str] = []
        self.lookups: list[list[str]] = []
        self.writes = 0
        self._stored: dict[str, dict] = {}
        self.reindex_after_reads: dict[str, int] = {}

    def reindex(self, virtual_record_id: str) -> None:
        self._stored.pop(virtual_record_id, None)

    async def get_document_ids_by_virtual_record_ids(self, virtual_record_ids) -> dict:
        self.lookups.append(list(virtual_record_ids))
        return {v: {"documentId": f"doc-{v}"} for v in virtual_record_ids if v in self.records}

    async def get_record_from_storage(self, virtual_record_id, org_id, lookup_result=None) -> dict | None:
        self.reads.append(virtual_record_id)
        stored = self.records.get(virtual_record_id)
        if isinstance(stored, Exception):
            raise stored
        if stored is None:
            return None
        if self.reindex_after_reads.get(virtual_record_id) == self.reads.count(virtual_record_id) - 1:
            self.reindex(virtual_record_id)
        if virtual_record_id not in self._stored:
            self._stored[virtual_record_id] = _container(stored).model_dump(mode="json")
        return {"virtual_record_id": virtual_record_id, "block_containers": self._stored[virtual_record_id]}

    async def store_record(self, *args, **kwargs) -> None:  # pragma: no cover - must never be reached
        self.writes += 1


class Config:
    def __init__(self, flag=None) -> None:
        self.values = {} if flag is None else {FLAG: flag}

    async def get_config(self, key, default=None, use_cache=True) -> object:
        return self.values.get(key, default)

    async def set_config(self, key, value) -> bool:
        self.values[key] = value
        return True


def _store(have_units: set[str]) -> VectorStore:
    """The real VectorStore methods; the vector DB holds residue units (and so class units) for *have_units*."""
    vs = _make_vectorstore()
    vs.get_embedding_model_instance = AsyncMock(return_value=False)
    vs.embedding_size = 8
    vs.dense_embeddings = object()
    vs._ensure_collection = AsyncMock(return_value="records")
    vs._bind_membership = AsyncMock(side_effect=lambda *_a, **_k: set_membership_context(["conn-1"], [], []))
    vs._process_document_chunks = AsyncMock()
    vs._resync_membership_after_write = AsyncMock()
    vs.vector_db_service.filter_collection = AsyncMock(side_effect=lambda **kw: kw)

    async def scroll(collection_name, scroll_filter, limit, offset=None, with_payload=None) -> ScrollResult:
        wanted, block_ids = scroll_filter["must"]["virtualRecordId"], scroll_filter["must"]["blockId"]
        return ScrollResult(points=[VectorPoint(id=v, payload={"metadata": {"virtualRecordId": v}})
                                    for v in wanted if v in have_units and f"{v}_code_residue" in block_ids])
    vs.vector_db_service.scroll = AsyncMock(side_effect=scroll)
    return vs


def _embedded(vs) -> list:
    return [d for call in vs._process_document_chunks.call_args_list for d in call.args[0]]


async def _run(graph, vs, blobs, config, **kwargs) -> backfill.BackfillCounts | None:
    return await backfill.backfill_class_units(graph_provider=graph, vector_store=vs, blob_store=blobs,
                                               config_service=config, logger=SimpleNamespace(
                                                   info=lambda *a, **k: None, warning=lambda *a, **k: None),
                                               org_ids=kwargs.pop("org_ids", None) or graph.org_ids(),
                                               pause_seconds=0, **kwargs)


@pytest.fixture(autouse=True)
def _switch_on(monkeypatch) -> None:
    monkeypatch.setenv("CODE_GRAPH_BACKFILL", "true")


async def test_only_completed_code_records_whose_classes_lack_units_are_embedded() -> None:
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED"), "b": (WITH_CLASSES, "COMPLETED"),
                   "c": (NO_CLASSES, "COMPLETED"), "d": (WITH_CLASSES, "IN_PROGRESS")})
    vs = _store(have_units={"vr-b"})
    blobs = Blobs({"vr-a": WITH_CLASSES, "vr-b": WITH_CLASSES, "vr-c": NO_CLASSES})
    counts = await _run(graph, vs, blobs, Config())

    docs = _embedded(vs)
    assert {d.metadata["virtualRecordId"] for d in docs} == {"vr-a"}
    assert all(d.metadata["unitKind"] == "class" for d in docs)  # no block point is written
    assert docs[0].page_content.split("\n")[0] == "client.py — src/client.py · class:Client"
    # b already has its units; c is read and has nothing to add; d is not indexed yet (left out by the query).
    # a is read, then re-read after the write.
    assert blobs.reads == ["vr-a", "vr-a", "vr-c"]
    assert blobs.lookups == [["vr-a", "vr-c"]]  # one batched lookup for the page's records that need reading
    assert (counts.records, counts.embedded, counts.units, counts.skipped, counts.failed) == (3, 1, 1, 2, 0)


async def test_the_existence_check_is_one_query_per_page_not_per_class() -> None:
    graph = Graph({f"r{i}": (WITH_CLASSES, "COMPLETED") for i in range(5)})
    vs = _store(have_units=set())
    await _run(graph, vs, Blobs({f"vr-r{i}": WITH_CLASSES for i in range(5)}), Config(), page_size=100)
    assert vs.vector_db_service.scroll.await_count == 1


async def test_a_second_run_does_nothing() -> None:
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED")})
    config, blobs = Config(), Blobs({"vr-a": WITH_CLASSES})
    vs = _store(have_units=set())
    await _run(graph, vs, blobs, config)
    assert config.values[FLAG]["done"] is True

    again = _store(have_units=set())
    assert await _run(graph, again, blobs, config) is None  # the version's flag says done: no scan at all
    assert again._process_document_chunks.await_count == 0 and len(graph.pages) == 1

    rescan = _store(have_units={"vr-a"})  # the flag lost: the vector store still knows
    await _run(graph, rescan, blobs, Config())
    assert rescan._process_document_chunks.await_count == 0


async def test_a_missing_or_unreadable_blob_is_failed_and_skipped_never_fetched_from_the_source() -> None:
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED"), "b": (WITH_CLASSES, "COMPLETED"),
                   "c": (WITH_CLASSES, "COMPLETED")})
    blobs = Blobs({"vr-a": None, "vr-b": RuntimeError("storage down"), "vr-c": WITH_CLASSES})
    vs = _store(have_units=set())
    counts = await _run(graph, vs, blobs, Config())
    assert (counts.failed, counts.embedded) == (2, 1)
    assert {d.metadata["virtualRecordId"] for d in _embedded(vs)} == {"vr-c"}
    assert sorted(blobs.reads) == ["vr-a", "vr-b", "vr-c", "vr-c"]  # the stored blob only; the backfill has no source


async def test_no_block_is_re_embedded_and_no_llm_stage_input_changes() -> None:
    """classify and entities are fingerprinted on text_digest; the backfill writes no blob, so it is unchanged,
    and it hands nothing to the pipeline (it holds no ingress or coordinator)."""
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED")})
    blobs = Blobs({"vr-a": WITH_CLASSES})
    before = content_facts(_container(WITH_CLASSES))
    vs = _store(have_units=set())
    await _run(graph, vs, blobs, Config())
    after = content_facts(_container(WITH_CLASSES))
    assert before.text_digest == after.text_digest
    assert blobs.writes == 0
    assert {d.metadata.get("unitKind") for d in _embedded(vs)} == {"class"}


async def test_a_run_cut_off_resumes_after_the_last_finished_page() -> None:
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED"), "b": (WITH_CLASSES, "COMPLETED")})
    graph.fail_on_page = 2
    config, blobs = Config(), Blobs({"vr-a": WITH_CLASSES, "vr-b": WITH_CLASSES})
    with pytest.raises(RuntimeError):
        await _run(graph, _store(have_units=set()), blobs, config, page_size=1)
    assert config.values[FLAG] == {"done": False, "org": "org-1", "after": "a", "pages": 1, "records": 1,
                                   "embedded": 1, "units": 1, "skipped": 0, "failed": 0}

    graph.fail_on_page = None
    vs = _store(have_units=set())
    counts = await _run(graph, vs, blobs, config, page_size=1)
    assert graph.pages[2] == "a"  # resumed after the saved cursor
    assert {d.metadata["virtualRecordId"] for d in _embedded(vs)} == {"vr-b"}
    assert counts.embedded == 2 and config.values[FLAG]["done"] is True  # counts carry over


async def test_units_written_for_a_record_reindexed_meanwhile_are_removed_again() -> None:
    """A reconcile between the read and the write gives the classes new ids; units under the old ids
    would be named by no stored metadata, so nothing else would ever remove them."""
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED")})
    blobs = Blobs({"vr-a": WITH_CLASSES})
    blobs.reindex_after_reads = {"vr-a": 1}
    vs = _store(have_units=set())
    vs.delete_blocks_by_ids = AsyncMock()
    counts = await _run(graph, vs, blobs, Config())
    written = {d.metadata["blockId"] for d in _embedded(vs)}
    assert written and vs.delete_blocks_by_ids.await_args.args[:2] == (written, "vr-a")
    assert (counts.embedded, counts.units, counts.skipped) == (0, 0, 1)


async def test_a_re_read_that_keeps_some_class_ids_removes_only_the_replaced_ones() -> None:
    """A reconcile that changed one class keeps the other's id; that one's unit is the current class's, and
    no reconcile would re-embed it."""
    two_classes = WITH_CLASSES + b"\n\nclass Other:\n    def run(self):\n        return 2\n"
    graph = Graph({"a": (two_classes, "COMPLETED")})
    blobs = Blobs({"vr-a": two_classes})
    original = blobs.get_record_from_storage

    async def read(vrid, org_id, lookup_result=None) -> dict | None:
        out = await original(vrid, org_id, lookup_result)
        if blobs.reads.count(vrid) == 2:  # the re-check: the second class was replaced meanwhile
            out = {**out, "block_containers": {**out["block_containers"], "block_groups": [
                out["block_containers"]["block_groups"][0],
                {**out["block_containers"]["block_groups"][1], "id": "replaced"}]}}
        return out
    blobs.get_record_from_storage = read
    vs = _store(have_units=set())
    vs.delete_blocks_by_ids = AsyncMock()
    counts = await _run(graph, vs, blobs, Config())
    kept, replaced = (g["id"] for g in blobs._stored["vr-a"]["block_groups"])
    assert vs.delete_blocks_by_ids.await_args.args[:2] == ({replaced}, "vr-a")
    assert (counts.embedded, counts.units) == (1, 2)  # the kept class's unit and the unchanged residue


async def test_a_residue_written_from_an_older_blob_is_replaced_by_the_current_one() -> None:
    old, new = b"import os\nRATE = 1\n", b"import os\nRATE = 2\n"
    graph = Graph({"a": (old, "COMPLETED")})
    blobs = Blobs({"vr-a": old})
    original = blobs.get_record_from_storage

    async def read(vrid, org_id, lookup_result=None) -> dict | None:
        if blobs.reads.count(vrid) == 1:  # the re-check: a reconcile stored the new text meanwhile
            blobs.records[vrid] = new
            blobs.reindex(vrid)
        return await original(vrid, org_id, lookup_result)
    blobs.get_record_from_storage = read
    vs = _store(have_units=set())
    vs.delete_blocks_by_ids = AsyncMock()
    await _run(graph, vs, blobs, Config())
    assert vs.delete_blocks_by_ids.await_args.args[:2] == ({"vr-a_code_residue"}, "vr-a")
    assert ["RATE = 2" in d.page_content for d in _embedded(vs)] == [False, True]


async def test_a_record_without_classes_gains_its_residue_unit() -> None:
    """Short top-level lines are in no symbol's unit; a file without a class needs the backfill too."""
    src = b"import os\nRATE = 0.07\n\n\ndef helper():\n    return RATE * 2\n"
    graph = Graph({"a": (src, "COMPLETED")})
    vs = _store(have_units=set())
    counts = await _run(graph, vs, Blobs({"vr-a": src}), Config())
    [residue] = _embedded(vs)
    assert residue.metadata["unitKind"] == "residue" and residue.metadata["blockId"] == "vr-a_code_residue"
    assert "import os" in residue.page_content and "RATE = 0.07" in residue.page_content
    assert "def helper" not in residue.page_content  # the function is its own unit, already embedded
    assert (counts.records, counts.embedded, counts.units) == (1, 1, 1)


async def test_each_orgs_records_are_embedded_under_that_orgs_context() -> None:
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED"), "b": (WITH_CLASSES, "COMPLETED")})
    graph.records["b"]["orgId"] = "org-2"
    vs = _store(have_units=set())
    seen: list[tuple[str, str]] = []
    original = vs.index_class_units

    async def index(record_id, vrid, org_id, *args, **kwargs) -> int:
        seen.append((org_id, org_context.current().org_id))
        return await original(record_id, vrid, org_id, *args, **kwargs)
    vs.index_class_units = index
    await _run(graph, vs, Blobs({"vr-a": WITH_CLASSES, "vr-b": WITH_CLASSES}), Config())
    assert seen == [("org-1", "org-1"), ("org-2", "org-2")]


async def test_records_are_paged_under_each_org_and_a_cut_off_run_resumes_in_its_org() -> None:
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED"), "b": (WITH_CLASSES, "COMPLETED"),
                   "c": (WITH_CLASSES, "COMPLETED")})
    graph.records["a"]["orgId"] = graph.records["b"]["orgId"] = "org-2"
    graph.fail_on_page = 4  # org-1: c, then its end; org-2: a, then the read after a fails
    config, blobs = Config(), Blobs({"vr-a": WITH_CLASSES, "vr-b": WITH_CLASSES, "vr-c": WITH_CLASSES})
    with pytest.raises(RuntimeError):
        await _run(graph, _store(have_units=set()), blobs, config, page_size=1)
    assert graph.read_as == [("org-1", "org-1"), ("org-1", "org-1"), ("org-2", "org-2"), ("org-2", "org-2")]
    assert (config.values[FLAG]["org"], config.values[FLAG]["after"]) == ("org-2", "a")

    graph.fail_on_page, graph.read_as, graph.pages = None, [], []
    vs = _store(have_units=set())
    await _run(graph, vs, blobs, config, page_size=1)
    assert graph.read_as[0] == ("org-2", "org-2") and graph.pages[0] == "a"  # org-1 is not read again
    assert {d.metadata["virtualRecordId"] for d in _embedded(vs)} == {"vr-b"}
    assert config.values[FLAG]["done"] is True


async def test_a_lease_lost_during_a_page_stops_before_the_next_record_and_writes_no_cursor() -> None:
    """A page can outlast the lease: it is renewed before every record, and a lost one stops the run without
    moving the cursor another replica now owns."""
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED"), "b": (WITH_CLASSES, "COMPLETED"),
                   "c": (WITH_CLASSES, "COMPLETED")})
    leases = iter([True, True, False])  # the page read, record a, then lost before record b
    vs = _store(have_units=set())
    config = Config()
    counts = await _run(graph, vs, Blobs({"vr-a": WITH_CLASSES, "vr-b": WITH_CLASSES, "vr-c": WITH_CLASSES}),
                        config, keep_lease=AsyncMock(side_effect=lambda: next(leases, False)))
    assert {d.metadata["virtualRecordId"] for d in _embedded(vs)} == {"vr-a"}
    assert counts.embedded == 1 and FLAG not in config.values


async def test_the_units_and_the_re_read_residue_are_built_off_the_loop(monkeypatch) -> None:
    """Building units tokenizes the whole file; the backfill runs on the consumers' loop, so every build
    runs in a thread, the residue of the re-read blob too."""
    import asyncio

    from app.modules.transformers import vectorstore as vectorstore_module
    threaded: list[str] = []
    real = asyncio.to_thread

    async def to_thread(func, *args, **kwargs) -> object:
        threaded.append(func.__name__)
        return await real(func, *args, **kwargs)
    monkeypatch.setattr(vectorstore_module.asyncio, "to_thread", to_thread)
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED")})
    await _run(graph, _store(have_units=set()), Blobs({"vr-a": WITH_CLASSES}), Config())
    assert threaded == ["code_documents_for_record", "code_residue_documents"]


async def test_a_record_whose_embed_fails_is_counted_failed_and_the_run_goes_on() -> None:
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED"), "b": (WITH_CLASSES, "COMPLETED")})
    vs = _store(have_units=set())
    vs._process_document_chunks = AsyncMock(side_effect=[RuntimeError("embedding down"), None, None])
    counts = await _run(graph, vs, Blobs({"vr-a": WITH_CLASSES, "vr-b": WITH_CLASSES}), Config())
    assert (counts.failed, counts.embedded) == (1, 1)


async def test_it_is_off_unless_the_code_graph_backfill_switch_is_on(monkeypatch) -> None:
    monkeypatch.delenv("CODE_GRAPH_BACKFILL")
    graph = Graph({"a": (WITH_CLASSES, "COMPLETED")})
    assert await _run(graph, _store(have_units=set()), Blobs({}), Config()) is None
    assert graph.pages == []


class AnyCode:
    """Answers whether any code record exists, as the startup check asks it."""

    def __init__(self, keys: list[str]) -> None:
        self.keys, self.asked = keys, []

    async def get_documents_paginated(self, collection, limit=50, filters=None, **_) -> list[dict]:
        self.asked.append((collection, limit, filters, org_context.current_org_id()))
        return [{"_key": k} for k in self.keys][:limit]


_LOG = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)


async def test_a_deployment_with_no_code_yet_is_marked_done_switch_or_not(monkeypatch) -> None:
    """Fresh deployments: every code record from now on gets its units, so there is nothing to backfill, and
    a path-free search_code can be complete without CODE_GRAPH_BACKFILL."""
    monkeypatch.delenv("CODE_GRAPH_BACKFILL")
    graph, config = AnyCode([]), Config()
    assert await backfill.mark_done_without_code(graph, config, _LOG, ["org-1", "org-2"]) is True
    assert graph.asked == [("records", 1, {"recordType": "CODE_FILE", "orgId": "org-1"}, "org-1"),
                           ("records", 1, {"recordType": "CODE_FILE", "orgId": "org-2"}, "org-2")]
    assert config.values[FLAG]["done"] is True and config.values[FLAG]["failed"] == 0


async def test_the_indexing_startup_task_marks_a_fresh_deployment_done_at_once(monkeypatch) -> None:
    monkeypatch.delenv("CODE_GRAPH_BACKFILL")
    config = Config()
    container = SimpleNamespace(logger=lambda: _LOG, config_service=lambda: config)
    await backfill.run_class_units_backfill(container, AnyCode([]), AsyncMock(return_value=["org-1"]))
    assert config.values[FLAG]["done"] is True


async def test_existing_code_or_a_backfill_under_way_is_left_to_the_backfill() -> None:
    config = Config()
    assert await backfill.mark_done_without_code(AnyCode(["r1"]), config, _LOG, ["org-1"]) is False
    assert FLAG not in config.values
    under_way = Config({"done": False, "after": "r5"})
    assert await backfill.mark_done_without_code(AnyCode([]), under_way, _LOG, ["org-1"]) is False
    assert under_way.values[FLAG] == {"done": False, "after": "r5"}
    done = Config({"done": True})
    graph = AnyCode(["r1"])
    assert await backfill.mark_done_without_code(graph, done, _LOG, ["org-1"]) is True and graph.asked == []
