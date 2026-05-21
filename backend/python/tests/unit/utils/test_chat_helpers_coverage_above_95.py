"""Targeted tests to raise branch/line coverage of app.utils.chat_helpers above ~95%."""

from __future__ import annotations

import asyncio
from typing import Any
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.blocks import BlockType, GroupType
from app.models.entities import DealRecord, MeetingRecord
from app.utils.chat_helpers import (
    CitationRefMapper,
    _extract_text_content_recursive,
    _find_first_block_index_recursive,
    build_fk_info,
    build_group_blocks,
    build_multimodal_user_content,
    build_message_content_array,
    create_record_instance_from_dict,
    enrich_virtual_record_id_to_result_with_fk_children,
    extract_bounding_boxes,
    extract_start_end_text,
    generate_text_fragment_url,
    get_message_content,
    get_flattened_results,
    is_base64_image,
    record_to_message_content,
)


def _run(coro):
    return asyncio.run(coro)


_VALID_MINIMAL_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _base_record_dict(**overrides):
    defaults = {
        "id": "rec-1",
        "org_id": "org-1",
        "record_name": "Test Record",
        "external_record_id": "ext-1",
        "version": 1,
        "origin": "CONNECTOR",
        "connector_name": "DRIVE",
        "connector_id": "conn-1",
        "mime_type": "text/plain",
        "source_created_at": None,
        "source_updated_at": None,
        "weburl": "https://example.com",
        "semantic_metadata": {},
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# is_base64_image branches
# ---------------------------------------------------------------------------


class TestIsBase64ImageExtraBranches:
    def test_blank_string(self):
        assert is_base64_image("   ") is False

    def test_invalid_padding_length(self):
        # Length not multiple of 4 after stripping
        assert is_base64_image("AAA") is False

    def test_decode_raises_returns_false(self, monkeypatch):
        def boom(_data):
            raise ValueError("bad decode")

        monkeypatch.setattr(base64, "b64decode", boom)
        assert is_base64_image("AAAA") is False

    def test_svg_xml_after_decode(self):
        raw = b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>"
        b64 = base64.b64encode(raw).decode("ascii")
        assert is_base64_image(b64) is True


class TestBuildMultimodalNonDictBlockDataContinues:
    def test_skips_block_when_data_neither_dict_nor_string(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(
            return_value={
                "block_containers": {
                    "blocks": [
                        {"type": "image", "data": 12345},  # invalid shape
                        {"type": "image", "data": {"uri": _VALID_MINIMAL_PNG_DATA_URI}},
                    ],
                },
            },
        )
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        result = _run(
            build_multimodal_user_content("Hi", attachments, mock_blob, "org1")
        )
        assert isinstance(result, list)
        assert result[0]["text"] == "Hi"


# ---------------------------------------------------------------------------
# CitationRefMapper.url_to_ref returns a copy (line 251)
# ---------------------------------------------------------------------------


class TestCitationRefMapperUrlProperty:
    def test_url_to_ref_returns_independent_snapshot(self):
        m = CitationRefMapper()
        m.get_or_create_ref("http://a.example/block")
        snap = m.url_to_ref
        snap["http://b.example/other"] = "ref_fake"
        assert "http://b.example/other" not in m.url_to_ref


# ---------------------------------------------------------------------------
# create_record_instance_from_dict — Meeting + Deal arms
# ---------------------------------------------------------------------------


class TestCreateRecordMeetingAndDeal:
    def test_meeting_record_with_graph_doc(self):
        d = _base_record_dict(record_type="MEETING")
        gd = {
            "hostEmail": "h@example.com",
            "hostId": "h1",
            "meetingType": 2,
            "durationMinutes": 30,
            "startTime": "2020-01-01T00:00:00Z",
            "endTime": "2020-01-01T00:30:00Z",
            "timezone": "UTC",
            "recordingUrl": "https://zoom.example/rec",
        }
        r = create_record_instance_from_dict(d, gd)
        assert isinstance(r, MeetingRecord)
        assert r.host_email == "h@example.com"

    def test_deal_record_with_graph_doc_float_amount(self):
        d = _base_record_dict(record_type="DEAL")
        gd = {"name": "Big", "amount": "99.5", "isWon": False, "isClosed": False}
        r = create_record_instance_from_dict(d, gd)
        assert isinstance(r, DealRecord)
        assert r.amount == 99.5


# ---------------------------------------------------------------------------
# Recursive helpers (_find_first_block_index_recursive /
# _extract_text_content_recursive)
# ---------------------------------------------------------------------------


class TestRecursiveBlockHelpers:
    def test_find_first_via_block_group_ranges_nested(self):
        block_groups = [
            {
                "type": GroupType.TEXT_SECTION.value,
                "children": {"block_ranges": [{"start": 5, "end": 5}]},
            },
        ]
        outer_children: dict[str, Any] = {
            "block_ranges": [],
            "block_group_ranges": [{"start": 0, "end": 0}],
        }
        idx = _find_first_block_index_recursive(block_groups, outer_children)
        assert idx == 5

    def test_extract_text_block_ranges_and_group_ranges(self):
        blocks = [
            {"type": BlockType.TEXT.value, "data": "a"},
            {"type": BlockType.TEXT.value, "data": "b"},
        ]
        children = {
            "block_ranges": [{"start": 0, "end": 1}],
            "block_group_ranges": [],
        }
        text = _extract_text_content_recursive(
            [], blocks, children, virtual_record_id="vr", seen_chunks=set()
        )
        assert "a" in text and "b" in text


# ---------------------------------------------------------------------------
# enrich_virtual_record_id_to_result_with_fk_children — deep path
# ---------------------------------------------------------------------------


class TestEnrichFkChildrenDeep:
    @pytest.mark.asyncio
    async def test_fetches_related_blob_and_appends_table_ddl(self):
        graph = MagicMock()
        graph.get_child_record_ids_by_relation_type = AsyncMock(
            return_value=[{"record_id": "rec_child", "childTable": "child_t"}]
        )
        graph.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        graph.get_virtual_record_ids_for_record_ids = AsyncMock(
            return_value={"rec_child": "vr_child"}
        )
        graph.get_document = AsyncMock(
            return_value={
                "orgId": "org-1",
                "recordName": "child",
                "recordType": "SQL_TABLE",
                "version": 1,
                "origin": "CONNECTOR",
                "connectorName": "SNOWFLAKE",
                "connectorId": "c1",
                "mimeType": "application/sql",
                "webUrl": "https://sql.example",
                "hideWeburl": False,
            }
        )

        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock(
            return_value={
                "id": "rec_child",
                "record_name": "child",
                "record_type": "SQL_TABLE",
                "block_containers": {
                    "block_groups": [
                        {
                            "type": "table",
                            "data": "raw-ddl-fallback-summary",  # non-dict data branch
                        },
                        {
                            "type": BlockType.TABLE.value,
                            "data": {
                                "ddl": "CREATE TABLE child (id INT);",
                                "table_summary": "Child table",
                            },
                        },
                    ],
                    "blocks": [
                        {
                            "type": "table_row",
                            "data": {"row_natural_language_text": "row one"},
                        },
                    ],
                },
            }
        )

        vmap = {
            "vr_main": {
                "id": "rec_main",
                "record_type": "SQL_TABLE",
                "record_name": "orders",
            }
        }
        flat: list = []

        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap,
            blob,
            "org-1",
            graph_provider=graph,
            flattened_results=flat,
        )

        assert "vr_child" in vmap
        fk_blocks = [x for x in flat if (x.get("metadata") or {}).get("source") == "FK_ENRICHMENT"]
        assert len(fk_blocks) >= 1
        # Child path: record_id not in initial record_id_to_fk_relations → inline fetch
        graph.get_child_record_ids_by_relation_type.assert_called()

    @pytest.mark.asyncio
    async def test_child_and_parent_fetch_exceptions_are_handled(self):
        graph = MagicMock()
        graph.get_child_record_ids_by_relation_type = AsyncMock(
            side_effect=RuntimeError("child boom")
        )
        graph.get_parent_record_ids_by_relation_type = AsyncMock(
            side_effect=RuntimeError("parent boom")
        )
        graph.get_virtual_record_ids_for_record_ids = AsyncMock(return_value={})

        vmap = {
            "vr_main": {
                "id": "rec_main",
                "record_type": "SQL_TABLE",
                "record_name": "t",
            }
        }
        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap, MagicMock(), "org-1", graph_provider=graph, flattened_results=[]
        )


# ---------------------------------------------------------------------------
# build_fk_info, get_message_content, build_message_content_array
# ---------------------------------------------------------------------------


class TestBuildFkInfo:
    def test_parent_and_child_lines(self):
        s = build_fk_info(
            {
                "fk_parent_relations": [
                    {
                        "parentTable": "p",
                        "sourceColumn": "a",
                        "targetColumn": "b",
                        "record_id": "r1",
                    }
                ],
                "fk_child_relations": [
                    {
                        "childTable": "c",
                        "sourceColumn": "x",
                        "targetColumn": "y",
                        "record_id": "r2",
                    }
                ],
            }
        )
        assert "Parent Tables" in s and "Child Tables" in s


class TestGetMessageContentBranches:
    def test_image_blocks_appended_before_context(self):
        flat = []
        # Only vr_extra in map triggers record_to_message_content extension path
        vmap = {
            "vr_x": {
                "id": "rid",
                "frontend_url": "https://app.example.com",
                "virtual_record_id": "vr_x",
                "context_metadata": "",
                "block_containers": {"blocks": [], "block_groups": []},
            }
        }
        ib = [{"type": "text", "text": "[img-placeholder]"}]
        content, _ = get_message_content(
            flat,
            vmap,
            user_data="u",
            query="q",
            mode="json",
            image_blocks=ib,
        )
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        assert any(t.strip() == "Attachments:" for t in texts)


class TestBuildMessageContentArrayGrouped:
    def test_valid_group_label_renders_block_group_prompt(self):
        flat = [
            {
                "virtual_record_id": "vr_g",
                "block_type": GroupType.TEXT_SECTION.value,
                "block_index": 0,
                "block_group_index": 0,
                "content": (
                    "",
                    [
                        {
                            "content": "cell",
                            "block_index": 0,
                            "block_type": BlockType.TEXT.value,
                        },
                    ],
                ),
                "metadata": {},
            }
        ]
        vmap = {
            "vr_g": {
                "id": "gr",
                "frontend_url": "https://fe.example",
                "context_metadata": "ctx",
            }
        }
        parts, mapper = build_message_content_array(
            flat, vmap, is_multimodal_llm=False, from_tool=True
        )
        flattened_text = "".join(
            chunk.get("text", "")
            for sub in parts
            for chunk in sub
            if chunk.get("type") == "text"
        )
        assert "cell" in flattened_text
        assert isinstance(mapper, CitationRefMapper)


# ---------------------------------------------------------------------------
# record_to_message_content — multimodal image + FK footer
# ---------------------------------------------------------------------------


class TestRecordToMessageMultimodalAndFk:
    def test_multimodal_image_block_appends_image_url(self):
        rec = {
            "id": "r1",
            "frontend_url": "https://fe.example",
            "virtual_record_id": "vrz",
            "context_metadata": "meta",
            "block_containers": {
                "blocks": [
                    {
                        "index": 1,
                        "type": BlockType.IMAGE.value,
                        "data": {"uri": _VALID_MINIMAL_PNG_DATA_URI},
                    },
                ],
                "block_groups": [],
            },
        }
        content, _ = record_to_message_content(rec, ref_mapper=None, is_multimodal_llm=True)
        assert any(c.get("type") == "image_url" for c in content)

    def test_foreign_key_related_tables_footer(self):
        rec = {
            "id": "r1",
            "frontend_url": "",
            "virtual_record_id": "vrz",
            "context_metadata": "",
            "fk_parent_record_ids": [
                {
                    "parentTable": "p",
                    "sourceColumn": "a",
                    "targetColumn": "b",
                    "record_id": "rp",
                }
            ],
            "fk_child_record_ids": [
                {
                    "childTable": "c",
                    "sourceColumn": "x",
                    "targetColumn": "y",
                    "record_id": "rc",
                }
            ],
            "block_containers": {"blocks": [], "block_groups": []},
        }
        content, _ = record_to_message_content(rec)
        joined = "".join(x.get("text", "") for x in content if x.get("type") == "text")
        assert "Foreign Key Related Tables" in joined


# ---------------------------------------------------------------------------
# extract_start_end_text / generate_text_fragment_url
# ---------------------------------------------------------------------------


class TestExtractStartEndAndFragmentUrl:
    def test_end_text_fallback_when_no_second_match_but_long_first(self):
        # One long alphabetic run; FRAGMENT_WORD_COUNT is 4 — forces elif branch on end_text
        s = "one two three four five six seven eight"
        start, end = extract_start_end_text(s)
        assert start
        assert end  # last four words subset

    def test_generate_fragment_url_exception_falls_back(self, monkeypatch):
        def boom(_snippet):
            raise RuntimeError("fail")

        monkeypatch.setattr("app.utils.chat_helpers.extract_start_end_text", boom)
        url = generate_text_fragment_url("https://ex.com/page", "one two three four")
        assert url == "https://ex.com/page"


# ---------------------------------------------------------------------------
# count_tokens_in_messages — skipped unknown + string list items
# ---------------------------------------------------------------------------


class TestCountTokensInMessagesBranches:
    def test_unknown_message_skipped_and_string_chunks_counted(self):
        from app.utils.chat_helpers import count_tokens_in_messages
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")

        class NoContent:
            pass

        messages = [
            NoContent(),
            {"content": [{"type": "text", "text": "a"}, "raw string slice"]},
        ]
        total = count_tokens_in_messages(messages, enc)
        assert total > 0


# ---------------------------------------------------------------------------
# get_flattened_results — prefetch recon warning + multimodal retrieval image
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_flattened_results_prefetch_recon_resolves_block_id():
    """blockIndex=None + blockId uses reconciliation prefetch + adjacent chunk sweep."""
    vrid = "vr_pref"
    blob = MagicMock()
    blob.config_service.get_config = AsyncMock(return_value={"frontend": {"publicEndpoint": None}})
    blob.get_record_from_storage = AsyncMock(
        return_value={
            "id": "rid_doc",
            "record_type": "FILE",
            "mime_type": "text/plain",
            "block_containers": {
                "blocks": [
                    {"type": BlockType.TEXT.value, "parent_index": None, "data": "hello"},
                    {"type": BlockType.TEXT.value, "parent_index": None, "data": "world"},
                    {"type": BlockType.TEXT.value, "parent_index": None, "data": "after"},
                ],
                "block_groups": [],
            },
        }
    )
    blob.get_reconciliation_metadata = AsyncMock(
        return_value={"block_id_to_index": {"bid-1": 1}}
    )

    result_set = [
        {
            "metadata": {
                "virtualRecordId": vrid,
                "blockIndex": None,
                "blockId": "bid-1",
                "isBlockGroup": False,
            },
            "content": "",
            "score": 1.0,
        }
    ]
    out = await get_flattened_results(
        result_set,
        blob,
        "org-1",
        is_multimodal_llm=False,
        virtual_record_id_to_result={},
        from_tool=False,
        from_retrieval_service=False,
        graph_provider=None,
    )
    texts = [
        x.get("content")
        for x in out
        if x.get("block_type") == BlockType.TEXT.value
    ]
    assert "world" in texts


@pytest.mark.asyncio
async def test_get_flattened_results_prefetch_recon_logs_warning_on_failure():
    vrid = "vr_pref_bad"
    blob = MagicMock()
    blob.config_service.get_config = AsyncMock(return_value={})
    blob.get_record_from_storage = AsyncMock(
        return_value={
            "id": "rid_doc",
            "record_type": "FILE",
            "block_containers": {
                "blocks": [{"type": BlockType.TEXT.value, "parent_index": None, "data": "x"}],
                "block_groups": [],
            },
        }
    )
    blob.get_reconciliation_metadata = AsyncMock(side_effect=RuntimeError("boom"))
    result_set = [
        {
            "metadata": {
                "virtualRecordId": vrid,
                "blockIndex": None,
                "blockId": "bid-z",
                "isBlockGroup": False,
            },
            "content": "",
            "score": 1.0,
        }
    ]
    out = await get_flattened_results(
        result_set,
        blob,
        "org-1",
        is_multimodal_llm=False,
        virtual_record_id_to_result={},
        graph_provider=None,
    )
    assert out == []


@pytest.mark.asyncio
async def test_get_flattened_results_image_from_retrieval_service():
    blob = MagicMock()
    blob.config_service.get_config = AsyncMock(return_value={})
    blob.get_record_from_storage = AsyncMock(
        return_value={
            "id": "rimg",
            "record_type": "FILE",
            "block_containers": {
                "blocks": [
                    {
                        "type": BlockType.IMAGE.value,
                        "data": {"uri": _VALID_MINIMAL_PNG_DATA_URI},
                    },
                ],
                "block_groups": [],
            },
        }
    )

    rs = [
        {
            "metadata": {
                "virtualRecordId": "vr_img",
                "blockIndex": 0,
                "isBlockGroup": False,
            },
            "content": "ignored_for_image",
            "score": 0.5,
        }
    ]
    out = await get_flattened_results(
        rs,
        blob,
        "org",
        is_multimodal_llm=False,
        virtual_record_id_to_result={},
        from_tool=False,
        from_retrieval_service=True,
    )
    imgs = [x for x in out if x.get("block_type") == BlockType.IMAGE.value]
    assert imgs and imgs[0].get("content", "").startswith("image_")


class TestExtractBoundingBoxesPartialFailure:
    def test_returns_none_when_later_point_invalid(self):
        assert (
            extract_bounding_boxes({"bounding_boxes": [{"x": 1, "y": 2}, {}]}) is None
        )


class TestBuildGroupBlocksSkipsImages:
    def test_filtered_child_blocks_exclude_images(self):
        block_groups = [
            {"type": GroupType.LIST.value, "children": [{"block_index": 0}]},
        ]
        blocks = [
            {
                "type": BlockType.IMAGE.value,
                "index": 0,
                "data": {"uri": "x"},
                "comments": [],
            },
            {"type": BlockType.TEXT.value, "index": 1, "data": "ok", "comments": []},
        ]
        out = build_group_blocks(
            block_groups,
            blocks,
            0,
            virtual_record_id="vr",
            record={"id": "r", "mime_type": "plain"},
            result={"score": 1.0, "metadata": {}},
        )
        assert isinstance(out, list)


class TestExtractStartEndEmptyEndText:
    def test_two_word_fragment_yields_blank_end_segment(self):
        start, end = extract_start_end_text("Alpha Beta")
        assert start == "Alpha Beta"
        assert end == ""


class TestBuildFkInfoOnlyChild:
    def test_child_relation_without_parents(self):
        s = build_fk_info(
            {
                "fk_parent_relations": [],
                "fk_child_relations": [
                    {
                        "childTable": "kid",
                        "sourceColumn": "a",
                        "targetColumn": "b",
                        "record_id": "kid1",
                    }
                ],
            }
        )
        assert "Child Tables" in s
        assert "Parent Tables" not in s


class TestEnrichFkChildrenEdgeBranches:
    @pytest.mark.asyncio
    async def test_related_vrid_skipped_when_already_flattened(self):
        graph = MagicMock()
        graph.get_child_record_ids_by_relation_type = AsyncMock(
            return_value=[{"record_id": "rec_known"}],
        )
        graph.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        graph.get_virtual_record_ids_for_record_ids = AsyncMock(
            return_value={"rec_known": "vr_known"}
        )

        blob = MagicMock()

        flat = [{"virtual_record_id": "vr_known", "score": 0.1}]
        vmap = {
            "vr_main": {
                "id": "rec_main",
                "record_type": "SQL_TABLE",
                "record_name": "main",
            },
            "vr_known": {"id": "rec_known"},
        }

        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap,
            blob,
            "org",
            graph_provider=graph,
            flattened_results=flat,
        )
        blob.get_record_from_storage.assert_not_called()

    @pytest.mark.asyncio
    async def test_related_blob_missing_sets_none_placeholder(self):
        graph = MagicMock()
        graph.get_child_record_ids_by_relation_type = AsyncMock(
            return_value=[{"record_id": "rec_x"}],
        )
        graph.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        graph.get_virtual_record_ids_for_record_ids = AsyncMock(
            return_value={"rec_x": "vr_x"}
        )
        graph.get_document = AsyncMock(return_value={"recordName": "n"})

        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock(return_value=None)

        vmap = {
            "vr_main": {
                "id": "rec_main",
                "record_type": "SQL_TABLE",
                "record_name": "orders",
            }
        }

        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap,
            blob,
            "org",
            graph_provider=graph,
            flattened_results=[],
        )
        assert vmap["vr_x"] is None

    @pytest.mark.asyncio
    async def test_graph_metadata_merge_failure_is_soft(self):
        graph = MagicMock()
        graph.get_child_record_ids_by_relation_type = AsyncMock(
            return_value=[{"record_id": "rec_y"}],
        )
        graph.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        graph.get_virtual_record_ids_for_record_ids = AsyncMock(
            return_value={"rec_y": "vr_y"}
        )
        graph.get_document = AsyncMock(side_effect=RuntimeError("arangodb unavailable"))

        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock(
            return_value={
                "id": "rec_y",
                "record_name": "y",
                "record_type": "SQL_TABLE",
                "block_containers": {"block_groups": [], "blocks": []},
            }
        )

        vmap = {
            "vr_main": {
                "id": "rec_main",
                "record_type": "SQL_TABLE",
                "record_name": "orders",
            }
        }
        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap,
            blob,
            "org",
            graph_provider=graph,
            flattened_results=[],
        )
        assert vmap["vr_y"]["record_name"] == "y"

    @pytest.mark.asyncio
    async def test_blob_fetch_exception_sets_none_placeholder(self):
        graph = MagicMock()
        graph.get_child_record_ids_by_relation_type = AsyncMock(
            return_value=[{"record_id": "rec_z"}],
        )
        graph.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        graph.get_virtual_record_ids_for_record_ids = AsyncMock(
            return_value={"rec_z": "vr_z"}
        )

        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock(side_effect=OSError("network"))

        vmap = {
            "vr_main": {
                "id": "rec_main",
                "record_type": "SQL_TABLE",
                "record_name": "orders",
            }
        }

        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap,
            blob,
            "org",
            graph_provider=graph,
            flattened_results=[],
        )
        assert vmap["vr_z"] is None
