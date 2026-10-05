"""How search hits become cited context: `get_flattened_results` resolves hits
against stored records, `get_enhanced_metadata` builds the citation metadata,
and `build_message_content_array` / `record_to_message_content` turn it into
`[block|refN]` text the model cites.

Only storage is faked: `InMemoryBlobStore` answers the four `BlobStorage` calls
these functions make (config, batched virtual-record lookup, record download,
reconciliation metadata) and `InMemoryTypeDocs` the graph batch lookup.
"""

from __future__ import annotations

import base64
import struct
import zlib
from typing import Any

import pytest

from app.utils.chat_helpers import (
    CitationRefMapper,
    build_message_content_array,
    get_enhanced_metadata,
    get_flattened_results,
    record_to_message_content,
    record_to_text,
)


def _png_data_uri() -> str:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw += chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(raw).decode()


PNG = _png_data_uri()
FRONTEND = "https://app.test"


class _Config:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def get_config(self, key: str, default: Any = None, **_: Any) -> Any:
        if self.fail:
            raise RuntimeError("etcd unavailable")
        return {"frontend": {"publicEndpoint": FRONTEND}}


class InMemoryBlobStore:
    def __init__(self, records: dict[str, dict[str, Any]], *, recon: dict[str, dict] | None = None,
                 config_fails: bool = False, lookup_fails: bool = False) -> None:
        self.records = records
        self.recon = recon or {}
        self.config_service = _Config(config_fails)
        self.lookup_fails = lookup_fails
        self.downloads: list[tuple[str, Any]] = []

    async def get_document_ids_by_virtual_record_ids(self, vrids: list[str]) -> dict[str, Any]:
        if self.lookup_fails:
            raise RuntimeError("mongo timeout")
        return {v: {"documentId": f"doc-{v}"} for v in vrids if v in self.records}

    async def get_record_from_storage(self, virtual_record_id: str, org_id: str, lookup_result: Any = None) -> dict | None:
        self.downloads.append((virtual_record_id, lookup_result))
        rec = self.records.get(virtual_record_id)
        return None if rec is None else {**rec, "block_containers": {
            "blocks": [dict(b) for b in rec["block_containers"]["blocks"]],
            "block_groups": [dict(g) for g in rec["block_containers"].get("block_groups", [])],
        }}

    async def get_reconciliation_metadata(self, vrid: str, org_id: str) -> dict | None:
        value = self.recon.get(vrid)
        if isinstance(value, Exception):
            raise value
        return value


class InMemoryTypeDocs:
    """Graph stand-in for the batched type-specific lookup (`get_nodes_by_field_in`)."""

    def __init__(self, docs: dict[str, dict[str, dict]]) -> None:
        self.docs = docs
        self.batches: list[tuple[str, list[str]]] = []

    async def get_nodes_by_field_in(self, collection: str, field: str, values: list[str], **_: Any) -> list[dict]:
        self.batches.append((collection, list(values)))
        return [{**d, "id": k} for k, d in self.docs.get(collection, {}).items() if k in values]

    async def get_document(self, document_key: str, collection: str) -> dict | None:
        return self.docs.get(collection, {}).get(document_key)


def text(i: int, data: str, **extra: Any) -> dict[str, Any]:
    return {"index": i, "type": "text", "data": data, **extra}


def blob(vrid: str, blocks: list[dict], groups: list[dict] | None = None, **fields: Any) -> dict[str, Any]:
    return {"virtual_record_id": vrid, "block_containers": {"blocks": blocks, "block_groups": groups or []}, **fields}


def graph_record(key: str, **fields: Any) -> dict[str, Any]:
    return {"_key": key, "recordName": f"{key}.pdf", "recordType": "FILE", "origin": "CONNECTOR",
            "connectorName": "DRIVE", "connectorId": "conn-1", "version": 1,
            "webUrl": f"https://drive.test/{key}", "mimeType": "application/pdf", **fields}


def hit(vrid: str, index: int | None = None, *, group: bool = False, score: float = 0.5,
        content: str = "", **meta: Any) -> dict:
    metadata = {"virtualRecordId": vrid, "isBlockGroup": group, **meta}
    if index is not None:
        metadata["blockIndex"] = index
    return {"metadata": metadata, "score": score, "content": content}


async def flatten(store: InMemoryBlobStore, hits: list[dict], vmap: dict | None = None, *,
                  multimodal: bool = False, graph: Any = None, **kwargs: Any) -> tuple[list[dict], dict]:
    vr_to_result: dict[str, Any] = {}
    results = await get_flattened_results(
        hits, store, "org-1", multimodal, vr_to_result, vmap or {}, graph_provider=graph, **kwargs,
    )
    return results, vr_to_result


def _joined(contents: list[list[dict]]) -> str:
    return "".join(part.get("text", "") for content in contents for part in content)


class TestFlattenedResultsMissingBlocks:
    async def test_hits_pointing_past_the_stored_blocks_are_dropped(self) -> None:
        store = InMemoryBlobStore({"v1": blob("v1", [text(0, "only block")])})
        results, _ = await flatten(store, [hit("v1", 0), hit("v1", 7), hit("v1", 3, group=True)],
                                   {"v1": graph_record("r1")})
        assert [(r["block_index"], r["content"]) for r in results] == [(0, "only block")]

    async def test_block_id_resolves_through_reconciliation_metadata(self) -> None:
        store = InMemoryBlobStore(
            {"v1": blob("v1", [text(0, "a"), text(1, "b")])},
            recon={"v1": {"block_id_to_index": {"blk-b": 1}, "hash_to_block_ids": {}}},
        )
        results, _ = await flatten(store, [hit("v1", blockId="blk-b")], {"v1": graph_record("r1")}, from_tool=True)
        assert [(r["block_index"], r["content"]) for r in results] == [(1, "b")]

    async def test_unresolvable_block_id_is_dropped_not_fatal(self) -> None:
        store = InMemoryBlobStore(
            {"v1": blob("v1", [text(0, "a")]), "v2": blob("v2", [text(0, "kept")])},
            recon={"v1": RuntimeError("recon store down")},
        )
        results, _ = await flatten(
            store, [hit("v1", blockId="blk-x"), hit("v1"), hit("v2", 0)],
            {"v1": graph_record("r1"), "v2": graph_record("r2")}, from_tool=True,
        )
        assert [r["content"] for r in results] == ["kept"]

    async def test_record_missing_from_storage_drops_only_its_hits(self) -> None:
        store = InMemoryBlobStore({"v2": blob("v2", [text(0, "visible")])})
        results, vr_map = await flatten(store, [hit("gone", 0), hit("v2", 0)],
                                        {"gone": graph_record("rg"), "v2": graph_record("r2")})
        assert vr_map["gone"] is None
        assert {r["virtual_record_id"] for r in results} == {"v2"}

    async def test_duplicate_hits_are_merged_and_each_record_downloaded_once(self) -> None:
        store = InMemoryBlobStore({"v1": blob("v1", [text(0, "a"), text(1, "b")])})
        results, _ = await flatten(
            store, [hit("v1", 0, score=0.9), hit("v1", 0, score=0.1), hit("v1", 1)],
            {"v1": graph_record("r1")}, from_tool=True,
        )
        assert [r["block_index"] for r in results] == [0, 1]
        assert [d[0] for d in store.downloads] == ["v1"]
        assert store.downloads[0][1] == {"documentId": "doc-v1"}

    async def test_lookup_and_config_outages_degrade_gracefully(self) -> None:
        store = InMemoryBlobStore({"v1": blob("v1", [text(0, "a")])}, config_fails=True, lookup_fails=True)
        results, vr_map = await flatten(store, [hit("v1", 0)], {"v1": graph_record("r1")})
        assert [r["content"] for r in results] == ["a"]
        assert store.downloads == [("v1", None)]
        assert vr_map["v1"]["frontend_url"] == ""

    async def test_neighbouring_text_blocks_are_added_for_context(self) -> None:
        store = InMemoryBlobStore({"v1": blob("v1", [text(0, "before"), text(1, "hit"), text(2, "after")])})
        results, _ = await flatten(store, [hit("v1", 1)], {"v1": graph_record("r1")})
        assert sorted(r["block_index"] for r in results) == [0, 1, 2]
        tool_results, _ = await flatten(store, [hit("v1", 1)], {"v1": graph_record("r1")}, from_tool=True)
        assert [r["block_index"] for r in tool_results] == [1]

    async def test_record_summary_hit(self) -> None:
        store = InMemoryBlobStore({"v1": blob("v1", [text(0, "a")])})
        summary = {"metadata": {"virtualRecordId": "v1", "isRecordSummary": True, "isBlockGroup": False},
                   "content": "It is about X", "score": 0.8}
        empty = {"metadata": {"virtualRecordId": "v1", "isRecordSummary": True, "isBlockGroup": False},
                 "content": ""}
        results, _ = await flatten(store, [summary, dict(summary), empty], {"v1": graph_record("r1")}, from_tool=True)
        (only,) = results
        assert only["block_type"] == "record_summary" and only["block_index"] is None
        assert only["metadata"]["webUrl"] == "https://drive.test/r1"


class TestFlattenedResultsIncompleteGraphRecords:
    async def test_null_connector_and_missing_version_do_not_fail_the_search(self) -> None:
        # connectorId is nullable in the graph schema and version has only a default.
        store = InMemoryBlobStore({"v1": blob("v1", [text(0, "kept")]), "v2": blob("v2", [text(0, "also kept")])})
        vmap = {"v1": graph_record("r1", connectorId=None, version=None), "v2": graph_record("r2")}
        results, vr_map = await flatten(store, [hit("v1", 0), hit("v2", 0)], vmap, from_tool=True)
        assert [r["content"] for r in results] == ["kept", "also kept"]
        assert "r1.pdf" in vr_map["v1"]["context_metadata"]

    async def test_unknown_connector_on_a_record_with_type_data_only_loses_its_header(self) -> None:
        store = InMemoryBlobStore({"v1": blob("v1", [text(0, "kept")])})
        graph = InMemoryTypeDocs({"files": {"r1": {"isFile": True, "extension": "pdf"}}})
        vmap = {"v1": graph_record("r1", connectorName="NOT_A_CONNECTOR")}
        results, vr_map = await flatten(store, [hit("v1", 0)], vmap, graph=graph, from_tool=True)
        assert [r["content"] for r in results] == ["kept"]
        assert vr_map["v1"]["context_metadata"] == ""

    async def test_unknown_record_type_only_loses_its_header(self) -> None:
        store = InMemoryBlobStore({"v1": blob("v1", [text(0, "kept")])})
        results, vr_map = await flatten(store, [hit("v1", 0)], {"v1": graph_record("r1", recordType="HOLOGRAM")},
                                        from_tool=True)
        assert [r["content"] for r in results] == ["kept"]
        assert vr_map["v1"]["context_metadata"] == ""


class TestFlattenedResultsBlockKinds:
    async def test_top_level_code_block_keeps_its_symbol(self) -> None:
        code = {"index": 0, "type": "code", "data": {"text": "def f(): pass"},
                "code_metadata": {"qualified_name": "mod.f"}}
        store = InMemoryBlobStore({"v1": blob("v1", [code])})
        (result,), _ = await flatten(store, [hit("v1", 0)], {"v1": graph_record("r1")}, from_tool=True)
        assert result["block_type"] == "code"
        assert "def f(): pass" in result["content"]

    async def test_images(self) -> None:
        image = {"index": 0, "type": "image", "data": {"uri": PNG},
                 "image_metadata": {"description": "a bar chart"}}
        textless = {"index": 1, "type": "image", "data": {"description": "logo"}}
        empty = {"index": 2, "type": "image", "data": {}}
        store = InMemoryBlobStore({"v1": blob("v1", [image, textless, empty])})
        vmap = {"v1": graph_record("r1")}
        multimodal, _ = await flatten(store, [hit("v1", 0, content="chart of sales"), hit("v1", 1), hit("v1", 2)],
                                      vmap, multimodal=True, from_tool=True)
        assert multimodal[0]["content"] == PNG
        assert multimodal[0]["image_description"] == "chart of sales"
        assert multimodal[1]["content"] == "logo"
        assert len(multimodal) == 2
        text_only, _ = await flatten(store, [hit("v1", 0, content=PNG)], vmap, from_tool=True)
        assert text_only == []
        placeholders, _ = await flatten(store, [hit("v1", 0), hit("v1", 1)], vmap, from_retrieval_service=True)
        assert [r["content"] for r in placeholders] == ["image_0", "image_1"]

    def _table_record(self, num_cells: int | None) -> dict:
        rows = [{"index": i, "type": "table_row", "parent_index": 0,
                 "data": {"row_natural_language_text": f"row {i}"}} for i in range(3)]
        table = {"index": 0, "type": "table", "data": {"table_summary": "Sales", "ddl": "CREATE TABLE s"},
                 "children": {"block_ranges": [{"start": 0, "end": 2}]}}
        if num_cells is not None:
            table["table_metadata"] = {"num_of_cells": num_cells}
        return blob("v1", rows, [table])

    async def test_small_table_hit_brings_every_row(self) -> None:
        store = InMemoryBlobStore({"v1": self._table_record(num_cells=6)})
        (table,), _ = await flatten(store, [hit("v1", 0, group=True)], {"v1": graph_record("r1")}, from_tool=True)
        summary, rows = table["content"]
        assert summary.startswith("DDL:\nCREATE TABLE s")
        assert [r["content"] for r in rows] == ["row 0", "row 1", "row 2"]
        assert table["block_group_index"] == 0

    async def test_large_table_hit_brings_only_the_matching_rows(self) -> None:
        store = InMemoryBlobStore({"v1": self._table_record(num_cells=None)})
        (table,), _ = await flatten(store, [hit("v1", 0, group=True), hit("v1", 2)],
                                    {"v1": graph_record("r1")}, from_tool=True)
        _, rows = table["content"]
        assert [r["block_index"] for r in rows] == [2]

    async def test_sql_row_beyond_the_stored_sample_uses_the_search_text(self) -> None:
        record = self._table_record(num_cells=None)
        store = InMemoryBlobStore({"v1": record})
        vmap = {"v1": graph_record("r1", recordType="SQL_TABLE")}
        (table,), _ = await flatten(store, [hit("v1", 50, content="row 50 from qdrant")], vmap, from_tool=True)
        (row,) = table["content"][1]
        assert row["content"] == "row 50 from qdrant"
        assert row["citationType"] == "vectordb"

    async def test_row_split_by_an_inline_image_is_rendered_from_its_fragments(self) -> None:
        container = {"index": 0, "type": "table_row", "parent_index": 0, "data": {}}
        frag_text = {"index": 1, "type": "text", "data": "cell text", "parent_block_index": 0}
        frag_img = {"index": 2, "type": "image", "data": {"uri": PNG, "description": "icon"}, "parent_block_index": 0}
        table = {"index": 0, "type": "table", "data": {"table_summary": "T"}, "children": [{"block_index": 0}],
                 "table_metadata": {"num_of_cells": 2}}
        store = InMemoryBlobStore({"v1": blob("v1", [container, frag_text, frag_img], [table])})
        vmap = {"v1": graph_record("r1")}
        (small,), _ = await flatten(store, [hit("v1", 0, group=True)], vmap, multimodal=True, from_tool=True)
        assert [(r["block_type"], r["block_index"]) for r in small["content"][1]] == [("text", 0), ("image", 0)]
        # A hit on the fragment routes to the container row.
        (via_fragment,), _ = await flatten(store, [hit("v1", 1)], vmap, from_tool=True)
        assert [r["content"] for r in via_fragment["content"][1]] == ["cell text", "icon"]

    async def test_fragment_of_a_list_item_renders_the_whole_list_once(self) -> None:
        item = {"index": 0, "type": "text", "data": "", "parent_index": 0}
        frag = {"index": 1, "type": "text", "data": "first item", "parent_block_index": 0}
        second = {"index": 2, "type": "text", "data": "second item", "parent_index": 0}
        group = {"index": 0, "type": "list", "children": [{"block_index": 0}, {"block_index": 2}]}
        store = InMemoryBlobStore({"v1": blob("v1", [item, frag, second], [group])})
        results, vr_map = await flatten(store, [hit("v1", 1), hit("v1", 2)], {"v1": graph_record("r1")}, from_tool=True)
        assert {r["block_type"] for r in results} == {"list"}
        assert [b["content"] for b in results[0]["content"][1]] == ["first item", "second item"]
        contents, _ = build_message_content_array(results, vr_map)
        assert _joined(contents).count("first item") == 1

    async def test_type_specific_metadata_is_fetched_in_one_batch(self) -> None:
        store = InMemoryBlobStore({"v1": blob("v1", [text(0, "a")]), "v2": blob("v2", [text(0, "b")])})
        graph = InMemoryTypeDocs({"tickets": {"t1": {"status": "Open", "priority": "High"}}})
        vmap = {"v1": graph_record("t1", recordType="TICKET"), "v2": graph_record("f2")}
        _, vr_map = await flatten(store, [hit("v1", 0), hit("v2", 0)], vmap, graph=graph, from_tool=True)
        assert sorted(graph.batches) == [("files", ["f2"]), ("tickets", ["t1"])]
        assert "Open" in vr_map["v1"]["context_metadata"]


class TestEnhancedMetadataMissingFields:
    def test_record_with_nothing_but_an_id_still_links_to_its_page(self) -> None:
        meta = get_enhanced_metadata({"id": "r1"}, text(4, "hello"), {})
        assert meta["webUrl"] == "/record/r1"
        assert meta["blockNum"] == [5]
        assert meta["recordName"] == "" and meta["updatedAt"] is None
        assert meta["bounding_box"] is None

    def test_record_with_no_id_and_no_url(self) -> None:
        meta = get_enhanced_metadata({}, None, {})
        assert meta["webUrl"] == ""
        assert meta["blockType"] == "record_summary"
        assert meta["blockNum"] is None

    def test_hidden_url_points_at_the_internal_page(self) -> None:
        record = {"id": "r1", "weburl": "https://secret.test/x", "hide_weburl": True}
        assert get_enhanced_metadata(record, text(0, "x"), {})["webUrl"] == "/record/r1"

    @pytest.mark.parametrize("record,expect_fragment", [
        ({"id": "r1", "weburl": "https://x.test/a"}, True),
        ({"id": "r1", "weburl": "https://x.test/a", "origin": "UPLOAD"}, False),
        ({"id": "r1", "weburl": "https://x.test/a", "record_type": "MAIL"}, False),
    ])
    def test_text_fragment_links_only_where_the_source_page_supports_them(self, record, expect_fragment) -> None:
        url = get_enhanced_metadata(record, text(0, "some sentence here"), {})["webUrl"]
        assert ("#:~:text=" in url) is expect_fragment

    def test_unparseable_timestamp_is_left_out(self) -> None:
        meta = get_enhanced_metadata({"id": "r1", "updated_at": "yesterday"}, text(0, "x"), {})
        assert meta["updatedAt"] is None
        ok = get_enhanced_metadata({"id": "r1", "updatedAtTimestamp": 0}, text(0, "x"), {})
        assert ok["updatedAt"] == "1970-01-01T00:00:00+00:00"

    def test_spreadsheet_rows(self) -> None:
        row = {"index": 3, "type": "table_row", "data": {"row_natural_language_text": "r",
                                                           "row_number": 4, "sheet_name": "Q1", "sheet_number": 2}}
        xlsx = get_enhanced_metadata({"id": "r1"}, row, {"extension": "xlsx"})
        assert (xlsx["blockNum"], xlsx["sheetName"], xlsx["sheetNum"]) == ([4], "Q1", 2)
        csv = get_enhanced_metadata({"id": "r1"}, row, {"extension": "csv"})
        assert csv["blockNum"] == [3]
        string_row = get_enhanced_metadata({"id": "r1"}, {"index": 0, "type": "table_row", "data": "raw"},
                                           {"extension": "tsv", "sheetName": "S"})
        assert string_row["blockNum"] == [1] and string_row["blockText"] == "raw"
        assert string_row["sheetName"] == "S"

    def test_bounding_boxes_are_kept_only_when_complete(self) -> None:
        block = text(0, "x", citation_metadata={"page_number": 2, "bounding_boxes": [{"x": 1, "y": 2}]})
        meta = get_enhanced_metadata({"id": "r1"}, block, {})
        assert meta["bounding_box"] == [{"x": 1, "y": 2}] and meta["pageNum"] == [2]
        partial = text(0, "x", citation_metadata={"bounding_boxes": [{"x": 1}]})
        assert get_enhanced_metadata({"id": "r1"}, partial, {})["bounding_box"] is None


def _record(vrid: str, rid: str, blocks: list[dict], groups: list[dict] | None = None, **fields: Any) -> dict:
    return {"virtual_record_id": vrid, "id": rid, "frontend_url": FRONTEND, "context_metadata": f"Record ID: {rid}",
            "block_containers": {"blocks": blocks, "block_groups": groups or []}, **fields}


def _flat(vrid: str, index: int | None, content: Any, block_type: str = "text", **extra: Any) -> dict:
    return {"virtual_record_id": vrid, "block_index": index, "content": content, "block_type": block_type,
            "metadata": {}, **extra}


class TestCitationMapping:
    def test_each_block_gets_a_ref_that_resolves_to_its_own_record(self) -> None:
        vr = {"v1": _record("v1", "r1", []), "v2": _record("v2", "r2", [])}
        contents, mapper = build_message_content_array(
            [_flat("v1", 0, "alpha"), _flat("v1", 1, "beta"), _flat("v2", 0, "gamma")], vr,
        )
        assert len(contents) == 2
        text_all = _joined(contents)
        assert "[0|ref1] alpha" in text_all and "[1|ref2] beta" in text_all and "[0|ref3] gamma" in text_all
        assert mapper.ref_to_url == {
            "ref1": f"{FRONTEND}/record/r1/preview#blockIndex=0",
            "ref2": f"{FRONTEND}/record/r1/preview#blockIndex=1",
            "ref3": f"{FRONTEND}/record/r2/preview#blockIndex=0",
        }

    def test_duplicate_block_is_cited_once_and_refs_are_stable_across_calls(self) -> None:
        vr = {"v1": _record("v1", "r1", [])}
        mapper = CitationRefMapper()
        first, mapper = build_message_content_array([_flat("v1", 0, "a"), _flat("v1", 0, "a")], vr, ref_mapper=mapper)
        assert _joined(first).count("[0|ref1]") == 1
        again, mapper = build_message_content_array([_flat("v1", 0, "a")], vr, ref_mapper=mapper)
        assert "[0|ref1]" in _joined(again)
        assert len(mapper.ref_to_url) == 1

    def test_record_with_only_a_summary_gets_a_page_level_citation(self) -> None:
        vr = {"v1": _record("v1", "r1", [])}
        contents, mapper = build_message_content_array([_flat("v1", None, "x", block_type="record_summary")], vr)
        assert "Citation ID for summary: ref2" in _joined(contents)
        assert mapper.ref_to_url["ref2"] == f"{FRONTEND}/record/r1"

    def test_table_rows_are_individually_citable(self) -> None:
        vr = {"v1": _record("v1", "r1", [])}
        rows = [_flat("v1", 3, "row three", "table_row"), _flat("v1", 4, "row four", "table_row")]
        contents, mapper = build_message_content_array(
            [_flat("v1", 3, ("Sales table", rows), "table", block_group_index=0)], vr,
        )
        joined = _joined(contents)
        assert "Sales table" in joined and "row three" in joined and "row four" in joined
        # The table is cited by its first row, so the two share a ref.
        assert [rows[0]["citation_ref"], rows[1]["citation_ref"]] == ["ref1", "ref2"]

    def test_images_go_to_the_side_channel_for_tool_results(self) -> None:
        vr = {"v1": _record("v1", "r1", [])}
        collected: list[dict] = []
        contents, _ = build_message_content_array(
            [_flat("v1", 0, PNG, "image")], vr, is_multimodal_llm=True, collected_images=collected,
        )
        assert "[0|ref1] (image)" in _joined(contents)
        (image,) = collected
        assert image["ref"] == "ref1" and image["virtual_record_id"] == "v1"

    def test_image_for_a_text_only_model_is_described_not_sent(self) -> None:
        vr = {"v1": _record("v1", "r1", [])}
        contents, _ = build_message_content_array([_flat("v1", 0, "a scanned receipt", "image")], vr)
        assert "[0|ref1] (image) a scanned receipt" in _joined(contents)
        dropped, _ = build_message_content_array([_flat("v1", 0, PNG, "image")], vr)
        assert "(image)" not in _joined(dropped)

    def test_group_with_an_image_keeps_reading_order(self) -> None:
        vr = {"v1": _record("v1", "r1", [])}
        group = [_flat("v1", 5, "step one"), _flat("v1", 6, PNG, "image"), _flat("v1", 7, "step two")]
        collected: list[dict] = []
        contents, _ = build_message_content_array(
            [_flat("v1", 5, ("", group), "list", block_group_index=1)], vr,
            is_multimodal_llm=True, collected_images=collected,
        )
        joined = _joined(contents)
        assert joined.index("step one") < joined.index("(image)") < joined.index("step two")
        assert len(collected) == 1


class TestRecordToMessageContent:
    def _table_record(self, rows: list[dict]) -> dict:
        table = {"index": 0, "type": "table", "data": {"table_summary": "T"},
                 "children": [{"block_index": r["index"]} for r in rows]}
        return _record("v1", "r1", rows, [table])

    def test_table_rows_split_by_images_keep_their_text_and_images(self) -> None:
        container = {"index": 0, "type": "table_row", "parent_index": 0, "data": {}}
        frags = [
            {"index": 1, "type": "text", "data": "cell", "parent_block_index": 0},
            {"index": 2, "type": "image", "data": {"uri": PNG}, "parent_block_index": 0},
        ]
        record = self._table_record([container])
        record["block_containers"]["blocks"].extend(frags)
        collected: list[dict] = []
        content, _ = record_to_message_content(record, is_multimodal_llm=True, collected_images=collected)
        joined = "".join(p.get("text", "") for p in content)
        assert "[Table #0]" in joined and "cell" in joined
        assert len(collected) == 1
        text_only, _ = record_to_message_content(record)
        assert "cell" in "".join(p.get("text", "") for p in text_only)

    def test_group_with_images_is_rendered_in_order(self) -> None:
        blocks = [
            {"index": 0, "type": "text", "data": "", "parent_index": 0},
            {"index": 1, "type": "text", "data": "before", "parent_block_index": 0},
            {"index": 2, "type": "image", "data": {"uri": PNG}, "parent_block_index": 0},
        ]
        record = _record("v1", "r1", blocks, [{"index": 0, "type": "list", "children": [{"block_index": 0}]}])
        content, _ = record_to_message_content(record, is_multimodal_llm=True)
        kinds = [p["type"] for p in content]
        assert "image_url" in kinds
        assert "[list #0]" in "".join(p.get("text", "") for p in content)

    def test_record_is_closed_and_names_its_foreign_keys(self) -> None:
        record = _record("v1", "r1", [text(0, "a")], fk_parent_record_ids=[
            {"parentTable": "orders", "sourceColumn": "oid", "targetColumn": "id", "record_id": "r9"},
        ])
        joined = "".join(p.get("text", "") for p in record_to_message_content(record)[0])
        assert "Parent Table: orders (Record ID: r9, FK: oid -> id)" in joined
        assert joined.rstrip().endswith("</record>")
        assert "Parent Table: orders" in record_to_text(record)


class TestTableRowsWithoutTheirTable:
    """A row whose table group is missing (index past the stored groups, or none)."""

    def _record(self, parent_index: int | None) -> dict:
        orphan = {"index": 0, "type": "table_row", "parent_index": parent_index,
                  "data": {"row_natural_language_text": "orphan row"}}
        return _record("v1", "r1", [orphan, text(1, "normal text")])

    @pytest.mark.parametrize("parent_index", [4, None])
    def test_record_render_keeps_going(self, parent_index) -> None:
        content, _ = record_to_message_content(self._record(parent_index))
        assert "normal text" in "".join(p.get("text", "") for p in content)
        assert "normal text" in record_to_text(self._record(parent_index))

    @pytest.mark.parametrize("parent_index", [4, None])
    async def test_search_hit_on_the_row_does_not_fail_the_search(self, parent_index) -> None:
        store = InMemoryBlobStore({
            "v1": self._record(parent_index), "v2": blob("v2", [text(0, "other record")]),
        })
        results, _ = await flatten(store, [hit("v1", 0), hit("v2", 0)],
                                   {"v1": graph_record("r1"), "v2": graph_record("r2")}, from_tool=True)
        assert "other record" in [r["content"] for r in results]


class TestRecordsThatCannotBeShown:
    """Hits whose record is gone, unreadable, or never fetched (`None` or absent)."""

    def test_blocks_of_a_missing_record_are_never_cited_as_another_record(self) -> None:
        vr = {"v1": _record("v1", "r1", []), "gone": None}
        contents, mapper = build_message_content_array(
            [_flat("v1", 0, "from r1"), _flat("gone", 0, "orphan a"), _flat("gone", 1, "orphan b")], vr,
        )
        joined = _joined(contents)
        assert "orphan" not in joined
        assert all("/record/r1/" in url for url in mapper.ref_to_url.values())
        assert len(contents) == 1

    def test_hit_for_a_record_that_was_never_fetched_is_skipped(self) -> None:
        vr = {"v2": _record("v2", "r2", [])}
        contents, _ = build_message_content_array([_flat("never", 0, "x"), _flat("v2", 0, "from r2")], vr)
        assert len(contents) == 1
        assert "from r2" in _joined(contents)
        assert _joined(contents).count("</record>") == 1
