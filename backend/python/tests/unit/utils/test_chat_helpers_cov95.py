"""Extra unit tests to raise coverage of app.utils.chat_helpers above 95%."""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.utils.chat_helpers as chat_helpers_module
from app.config.constants.arangodb import Connectors
from app.models.blocks import BlockType, GroupType
from app.models.entities import (
    DealRecord,
    MeetingRecord,
    MimeTypes,
    OriginTypes,
    RecordType,
)
from app.utils.chat_helpers import (
    CitationRefMapper,
    _find_first_block_index_recursive,
    build_message_content_array,
    build_multimodal_user_content,
    create_block_from_metadata,
    create_record_instance_from_dict,
    enrich_virtual_record_id_to_result_with_fk_children,
    extract_bounding_boxes,
    generate_text_fragment_url,
    get_flattened_results,
    get_message_content,
    get_record,
    is_base64_image,
    record_to_message_content,
)

from tests.unit.utils.test_chat_helpers import _make_record_blob, _make_text_block

_MIN_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _minimal_record_dict(record_type: str) -> dict:
    return {
        "id": "rec-1",
        "org_id": "org-1",
        "record_name": "Test",
        "external_record_id": "ext-1",
        "version": 1,
        "origin": "CONNECTOR",
        "connector_name": "DRIVE",
        "connector_id": "conn-1",
        "mime_type": MimeTypes.UNKNOWN.value,
        "source_created_at": None,
        "source_updated_at": None,
        "weburl": "https://example.com",
        "semantic_metadata": {},
        "record_type": record_type,
    }


def _make_sql_table_record(vrid="vr-1", record_id="rec-sql-1") -> dict:
    return {
        "virtual_record_id": vrid,
        "id": record_id,
        "record_name": "t1",
        "record_type": "SQL_TABLE",
        "semantic_metadata": {},
        "block_containers": {
            "blocks": [],
            "block_groups": [{
                "type": "table",
                "data": {"table_summary": "s", "ddl": "CREATE TABLE x();"},
                "children": [],
            }],
        },
    }


def _run(coro):
    return asyncio.run(coro)


class TestGetFlattenedResultsCoverageShims:
    @pytest.mark.asyncio
    async def test_frontend_endpoint_config_non_dict_survives(self):
        block = _make_text_block(index=0, data="Hi")
        record = _make_record_blob()
        record["block_containers"]["blocks"] = [block]

        blob_store = AsyncMock()
        blob_store.get_record_from_storage = AsyncMock(return_value=record)
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value="invalid")
        blob_store.get_reconciliation_metadata = AsyncMock(return_value=None)

        vr_map = {"vr-1": record}
        result_set = [{
            "content": "Hi",
            "score": 1.0,
            "metadata": {
                "virtualRecordId": "vr-1",
                "blockIndex": 0,
                "isBlockGroup": False,
            },
        }]
        results = await get_flattened_results(
            result_set, blob_store, "org-1", False, vr_map,
        )
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_prefetch_reconciliation_failure_still_runs(self):
        block = _make_text_block(index=0, data="Hi")
        record = _make_record_blob()
        record["block_containers"]["blocks"] = [block]

        blob_store = AsyncMock()
        blob_store.get_record_from_storage = AsyncMock(return_value=record)
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})
        blob_store.get_reconciliation_metadata = AsyncMock(
            side_effect=RuntimeError("recon unavailable"),
        )

        vr_map = {"vr-1": record}
        result_set = [{
            "content": "",
            "score": 1.0,
            "metadata": {
                "virtualRecordId": "vr-1",
                "blockIndex": None,
                "blockId": "bid-x",
                "isBlockGroup": False,
            },
        }]
        results = await get_flattened_results(
            result_set, blob_store, "org-1", False, vr_map,
        )
        assert results == []


class TestCitationRefMapperSnapshots:
    def test_url_to_ref_returns_copy(self):
        m = CitationRefMapper()
        m.get_or_create_ref("https://a.example/block")
        snap = m.url_to_ref
        snap["https://evil"] = "refX"
        assert "https://evil" not in m.url_to_ref


class TestIsBase64ImageBranches:
    def test_non_string(self):
        assert is_base64_image(None) is False
        assert is_base64_image(123) is False

    def test_whitespace_only(self):
        assert is_base64_image("   ") is False

    def test_invalid_padding_length(self):
        # Valid charset but length not multiple of 4
        assert is_base64_image("SGVsbG8") is False

    def test_invalid_base64_charset(self):
        assert is_base64_image("!!!!") is False

    def test_decode_not_image_and_not_svg(self):
        # Valid base64 payload that decodes to ASCII, not an image
        assert is_base64_image(base64.b64encode(b"nope").decode()) is False

    def test_svg_raw_base64(self):
        raw = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"/>').decode()
        assert is_base64_image(raw) is True

    def test_b64decode_exception_returns_false(self, monkeypatch):
        def boom(_):
            raise ValueError("bad decode")

        monkeypatch.setattr(chat_helpers_module.base64, "b64decode", boom)
        assert is_base64_image("dGVzdA==") is False


class TestCreateRecordInstanceMeetingDeal:
    def test_meeting_with_graph_doc(self):
        d = _minimal_record_dict("MEETING")
        gd = {
            "hostEmail": "host@example.com",
            "hostId": "h1",
            "meetingType": 2,
            "durationMinutes": 30,
            "startTime": "2024-01-01T00:00:00Z",
            "endTime": "2024-01-01T00:30:00Z",
            "timezone": "UTC",
            "recordingUrl": "https://zoom.us/rec/1",
        }
        inst = create_record_instance_from_dict(d, gd)
        assert isinstance(inst, MeetingRecord)
        assert inst.host_email == "host@example.com"

    def test_deal_with_graph_doc(self):
        d = _minimal_record_dict("DEAL")
        gd = {
            "name": "Acme",
            "amount": "99.5",
            "expectedRevenue": 100,
            "expectedCloseDate": "2024-12-31",
            "conversionProbability": 0.5,
            "type": "NEW",
            "ownerId": "o1",
            "isWon": False,
            "isClosed": False,
            "createdDate": "2024-01-01",
            "closeDate": None,
        }
        inst = create_record_instance_from_dict(d, gd)
        assert isinstance(inst, DealRecord)
        assert inst.name == "Acme"
        assert inst.amount == 99.5


class TestExtractBoundingBoxesPropagatesUnexpectedErrors:
    def test_non_mapping_point_raises(self):
        meta = {"bounding_boxes": [object()]}
        with pytest.raises(Exception):
            extract_bounding_boxes(meta)


class TestCreateBlockFromMetadataError:
    def test_invalid_blocknum_type_raises(self):
        with pytest.raises(Exception):
            create_block_from_metadata({"blockNum": 42}, "page text")


class TestFindFirstBlockIndexRecursiveNested:
    def test_block_group_ranges_then_block_ranges(self):
        inner = {"type": "inner", "children": {"block_ranges": [{"start": 7, "end": 7}]}}
        placeholder = {"type": "pad", "children": {}}
        block_groups = [placeholder, inner]
        # Outer dict must declare block_ranges key (possibly empty) to enter the
        # range-based branch; block_group_ranges are only consulted when block_ranges is empty.
        children = {"block_ranges": [], "block_group_ranges": [{"start": 1, "end": 1}]}
        assert _find_first_block_index_recursive(block_groups, children) == 7


class TestBuildMultimodalUserContentEdgeBranches:
    def test_skips_non_dict_block(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={
            "block_containers": {
                "blocks": [
                    "not-a-dict",
                    {"type": "image", "data": {"uri": _MIN_PNG_DATA_URI}},
                ],
            },
        })
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        out = _run(build_multimodal_user_content("Hi", attachments, mock_blob, "org1"))
        assert isinstance(out, list)

    def test_skips_non_string_non_dict_image_data(self):
        mock_blob = MagicMock()
        mock_blob.get_record_from_storage = AsyncMock(return_value={
            "block_containers": {
                "blocks": [
                    {"type": "image", "data": 12345},
                ],
            },
        })
        attachments = [{"mimeType": "image/png", "virtualRecordId": "vr1"}]
        out = _run(build_multimodal_user_content("Hi", attachments, mock_blob, "org1"))
        assert out == "Hi"


@pytest.mark.asyncio
async def test_get_record_uses_deal_async_context(monkeypatch):
    """DealRecord should use async to_llm_context_with_graph when graph_provider is set."""
    import app.utils.chat_helpers as ch

    deal = DealRecord(
        id="deal-1",
        org_id="org-1",
        record_name="Big Deal",
        record_type=RecordType.DEAL,
        external_record_id="ext-deal",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.SALESFORCE,
        connector_id="sf-1",
        mime_type=MimeTypes.UNKNOWN.value,
        name="Big Deal",
    )
    monkeypatch.setattr(ch, "create_record_instance_from_dict", lambda *_a, **_k: deal)

    record_blob = {
        "virtual_record_id": "vr-deal",
        "id": "deal-1",
        "record_type": "DEAL",
        "record_name": "Big Deal",
        "semantic_metadata": {},
        "block_containers": {"blocks": [], "block_groups": []},
    }
    blob_store = AsyncMock()
    blob_store.get_record_from_storage = AsyncMock(return_value=record_blob)

    virtual_to_record_map = {
        "vr-deal": {
            "_key": "deal-1",
            "recordType": "DEAL",
            "recordName": "Big Deal",
            "version": 1,
            "origin": "CONNECTOR",
            "connectorName": "SALESFORCE",
            "connectorId": "sf-1",
            "webUrl": "https://crm.example/d/1",
            "mimeType": MimeTypes.UNKNOWN.value,
        },
    }
    gp = AsyncMock()
    gp.get_document = AsyncMock(return_value={"name": "Big Deal", "amount": 10})

    vr_map = {}
    with patch.object(
        DealRecord,
        "to_llm_context_with_graph",
        new_callable=AsyncMock,
        return_value="deal-context-async",
    ) as mock_deal_ctx:
        await get_record(
            "vr-deal",
            vr_map,
            blob_store,
            "org-1",
            virtual_to_record_map,
            gp,
            "https://app.example.com",
        )
    mock_deal_ctx.assert_awaited()
    assert vr_map["vr-deal"]["context_metadata"] == "deal-context-async"


class TestBuildMessageContentArrayBranches:
    def test_none_record_skips_header(self):
        flat = [{
            "virtual_record_id": "vr-missing",
            "block_index": 0,
            "block_type": BlockType.TEXT.value,
            "content": "x",
        }]
        vr = {"vr-missing": None}
        parts, _ = build_message_content_array(flat, vr)
        merged = [x for sub in parts for x in sub]
        assert not merged

    def test_image_description_when_from_tool_multimodal(self):
        flat = [{
            "virtual_record_id": "vr1",
            "block_index": 0,
            "block_type": BlockType.IMAGE.value,
            # Non-base64 content: multimodal + from_tool skips URL images but emits description text.
            "content": "Screenshot stored at https://cdn.example/preview.png showing the modal state",
        }]
        vr = {
            "vr1": {
                "frontend_url": "https://app.example.com",
                "id": "rec-1",
                "context_metadata": "ctx",
            },
        }
        parts, _ = build_message_content_array(
            flat, vr, is_multimodal_llm=True, from_tool=True,
        )
        merged = [x for sub in parts for x in sub]
        text_blocks = [m["text"] for m in merged if m.get("type") == "text"]
        blob = "\n".join(text_blocks)
        assert "image description" in blob

    def test_valid_group_label_renders_block_group_prompt(self):
        flat = [{
            "virtual_record_id": "vr1",
            "block_index": 0,
            "block_type": GroupType.LIST.value,
            "block_group_index": 0,
            "content": (
                "",
                [{
                    "content": "bullet A",
                    "block_type": BlockType.TEXT.value,
                    "block_index": 1,
                    "metadata": {},
                    "score": 0.5,
                    "citationType": "x",
                }],
            ),
        }]
        vr = {
            "vr1": {
                "frontend_url": "https://app.example.com",
                "id": "rec-1",
                "context_metadata": "meta",
            },
        }
        parts, _ = build_message_content_array(flat, vr)
        merged = [x for sub in parts for x in sub]
        text_joined = " ".join(m["text"] for m in merged if m.get("type") == "text")
        assert "bullet A" in text_joined

    def test_base64_png_skipped_when_from_tool_multimodal(self):
        """Line ~2032: multimodal LLM still skips raw base64 image rows in tool transcripts."""
        flat = [{
            "virtual_record_id": "vr1",
            "block_index": 0,
            "block_type": BlockType.IMAGE.value,
            "content": _MIN_PNG_DATA_URI,
        }]
        vr = {
            "vr1": {
                "frontend_url": "https://app.example.com",
                "id": "rec-1",
                "context_metadata": "ctx",
            },
        }
        parts, _ = build_message_content_array(
            flat, vr, is_multimodal_llm=True, from_tool=True,
        )
        merged = [x for sub in parts for x in sub]
        assert not any(it.get("type") == "image_url" for it in merged)


class TestGetMessageContentImageBlocksAndOrphanVrids:
    def test_image_blocks_appended_before_context(self):
        flat = [{
            "virtual_record_id": "vr1",
            "block_index": 0,
            "block_type": BlockType.TEXT.value,
            "content": "hello",
            "metadata": {},
        }]
        vr = {
            "vr1": {
                "frontend_url": "https://a.com",
                "id": "r1",
                "context_metadata": "",
                "block_containers": {"blocks": [], "block_groups": []},
            },
        }
        extra = [{"type": "text", "text": "attached"}]
        content, _ = get_message_content(
            flat, vr, "user", "q", mode="json",
            image_blocks=extra,
        )
        texts = [c["text"] for c in content if c.get("type") == "text"]
        attachments_idx = next(i for i, t in enumerate(texts) if "Attachments:" in t)
        assert attachments_idx >= 0
        attachment_pos = None
        context_header_idx = None
        for i, item in enumerate(content):
            if item.get("type") == "text" and item.get("text") == "attached":
                attachment_pos = i
            if item.get("type") == "text" and isinstance(item.get("text"), str) and (
                item["text"].startswith("</context>")
            ):
                context_header_idx = i
        assert attachment_pos is not None and context_header_idx is not None
        assert attachment_pos < context_header_idx

    def test_adds_records_only_in_virtual_map(self):
        flat = []
        png = (
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        vr = {
            "vr-extra": {
                "virtual_record_id": "vr-extra",
                "record_type": RecordType.SQL_TABLE.value,
                "frontend_url": "https://a.com",
                "id": "rec-x",
                "context_metadata": "only in map",
                "block_containers": {
                    "blocks": [
                        {
                            "index": 0,
                            "type": BlockType.IMAGE.value,
                            "parent_index": None,
                            "data": {"uri": png},
                        },
                    ],
                    "block_groups": [],
                },
            },
        }
        content, _ = get_message_content(
            flat, vr, "user", "q", mode="json", is_multimodal_llm=True,
        )
        flat_text = [c for c in content if c.get("type") == "text"]
        assert any("only in map" in (t.get("text") or "") for t in flat_text)
        assert any(c.get("type") == "image_url" for c in content)

    def test_orphan_skips_blank_record_placeholder(self):
        """Line ~1958: vrids_only_in_map with falsy records are ignored."""
        content, _ = get_message_content(
            [],
            {"vr-extra": None},
            "user",
            "q",
            mode="json",
        )
        assert isinstance(content, list)


class TestRecordToMessageContentMultimodalAndFk:
    def test_multimodal_image_emits_image_url(self):
        record = {
            "virtual_record_id": "vr1",
            "frontend_url": "https://a.com",
            "id": "rec-1",
            "context_metadata": "ctx",
            "block_containers": {
                "blocks": [
                    {
                        "index": 0,
                        "type": BlockType.IMAGE.value,
                        "parent_index": None,
                        "data": {"uri": _MIN_PNG_DATA_URI},
                    },
                ],
                "block_groups": [],
            },
        }
        blocks, mapper = record_to_message_content(record, ref_mapper=CitationRefMapper(), is_multimodal_llm=True)
        types = [b.get("type") for b in blocks]
        assert "image_url" in types

    def test_fk_parent_and_child_sections(self):
        record = {
            "virtual_record_id": "vr1",
            "frontend_url": "",
            "id": "rec-1",
            "context_metadata": "ctx",
            "fk_parent_record_ids": [
                {
                    "parentTable": "orders",
                    "sourceColumn": "user_id",
                    "targetColumn": "id",
                    "record_id": "p1",
                },
            ],
            "fk_child_record_ids": [
                {
                    "childTable": "line_items",
                    "sourceColumn": "id",
                    "targetColumn": "order_id",
                    "record_id": "c1",
                },
            ],
            "block_containers": {"blocks": [], "block_groups": []},
        }
        blocks, _ = record_to_message_content(record)
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "Foreign Key Related Tables:" in text
        assert "Parent Table: orders" in text
        assert "Child Table: line_items" in text

    def test_fk_child_only_section(self):
        record = {
            "virtual_record_id": "vr1",
            "frontend_url": "",
            "id": "rec-1",
            "context_metadata": "ctx",
            "fk_child_record_ids": [
                {"childTable": "rows", "sourceColumn": "id", "targetColumn": "t_id", "record_id": "c1"},
            ],
            "block_containers": {"blocks": [], "block_groups": []},
        }
        blocks, _ = record_to_message_content(record)
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "Child Table: rows" in text
        assert "Parent Table" not in text

    def test_skips_block_group_when_type_not_valid_label(self):
        record = {
            "virtual_record_id": "vr1",
            "frontend_url": "",
            "id": "rec-1",
            "context_metadata": "ctx",
            "block_containers": {
                "blocks": [
                    {"index": 0, "type": "text", "parent_index": 0, "data": "nested"},
                ],
                "block_groups": [
                    {"type": "table", "children": [{"block_index": 0}]},
                ],
            },
        }
        blocks, _ = record_to_message_content(record)
        joined = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "nested" not in joined


class TestGenerateTextFragmentUrlEdgeBranches:
    def test_snippet_trim_trailing_non_alnum(self):
        url = generate_text_fragment_url("https://page.test/doc", "alpha beta gamma delta extra!!!")
        assert "#:~:text=" in url

class TestEnrichFkChildrenExtraBranches:
    @pytest.mark.asyncio
    async def test_skips_non_dict_record_entries(self):
        gp = AsyncMock()
        gp.get_child_record_ids_by_relation_type = AsyncMock(return_value=[])
        gp.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        blob = AsyncMock()
        vmap = {"vr-x": _make_sql_table_record(), "vr-bad": "not-dict"}
        flat = []
        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap, blob, "org", graph_provider=gp, flattened_results=flat,
        )

    @pytest.mark.asyncio
    async def test_blob_fetch_none_sets_placeholder(self):
        child_rels = [{"record_id": "rec-c1"}]
        gp = AsyncMock()
        gp.get_child_record_ids_by_relation_type = AsyncMock(return_value=child_rels)
        gp.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        gp.get_virtual_record_ids_for_record_ids = AsyncMock(
            return_value={"rec-c1": "vr-new"},
        )
        gp.get_document = AsyncMock(return_value={})
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value=None)
        vmap = {"vr-1": _make_sql_table_record()}
        flat = []
        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap, blob, "org", graph_provider=gp, flattened_results=flat,
        )
        assert vmap.get("vr-new") is None

    @pytest.mark.asyncio
    async def test_graph_merge_exception_is_non_fatal(self):
        child_rels = [{"record_id": "rec-c1"}]
        gp = AsyncMock()
        gp.get_child_record_ids_by_relation_type = AsyncMock(return_value=child_rels)
        gp.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        gp.get_virtual_record_ids_for_record_ids = AsyncMock(
            return_value={"rec-c1": "vr-new"},
        )
        gp.get_document = AsyncMock(side_effect=RuntimeError("graph read fail"))
        row_blob = _make_sql_table_record(vrid="vr-new", record_id="rec-c1")
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value=row_blob)
        vmap = {"vr-1": _make_sql_table_record()}
        flat = []
        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap, blob, "org", graph_provider=gp, flattened_results=flat,
        )
        assert vmap["vr-new"] is not None

    @pytest.mark.asyncio
    async def test_blob_storage_exception_sets_none(self):
        child_rels = [{"record_id": "rec-c1"}]
        gp = AsyncMock()
        gp.get_child_record_ids_by_relation_type = AsyncMock(return_value=child_rels)
        gp.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        gp.get_virtual_record_ids_for_record_ids = AsyncMock(
            return_value={"rec-c1": "vr-new"},
        )
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(side_effect=OSError("disk"))
        vmap = {"vr-1": _make_sql_table_record()}
        flat = []
        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap, blob, "org", graph_provider=gp, flattened_results=flat,
        )
        assert vmap.get("vr-new") is None

    @pytest.mark.asyncio
    async def test_fk_relations_fetched_when_not_in_precache_for_ddl_branch(self):
        """Lines 603-621: related table without precached FK relations."""
        gp = AsyncMock()
        gp.get_child_record_ids_by_relation_type = AsyncMock(
            side_effect=[
                [{"record_id": "rec-rel"}],  # children of SQL table in vr map
                [],
            ],
        )
        gp.get_parent_record_ids_by_relation_type = AsyncMock(
            side_effect=[
                [],  # parents of SQL table in vr map
                [],
            ],
        )
        gp.get_virtual_record_ids_for_record_ids = AsyncMock(
            return_value={"rec-rel": "vr-rel"},
        )
        gp.get_document = AsyncMock(return_value={})

        related = _make_sql_table_record(vrid="vr-rel", record_id="rec-rel")
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value=related)

        parent_sql = _make_sql_table_record(vrid="vr-p", record_id="rec-parent-sql")
        vmap = {"vr-p": parent_sql}
        flat = []
        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap, blob, "org", graph_provider=gp, flattened_results=flat,
        )
        fk_entries = [r for r in flat if (r.get("metadata") or {}).get("source") == "FK_ENRICHMENT"]
        assert fk_entries


class TestEnrichWarningsNoTableGroup:
    @pytest.mark.asyncio
    async def test_no_table_block_group_does_not_emit_fk_flattened_entry(self):
        child_rels = [{"record_id": "rec-c1"}]
        gp = AsyncMock()
        gp.get_child_record_ids_by_relation_type = AsyncMock(return_value=child_rels)
        gp.get_parent_record_ids_by_relation_type = AsyncMock(return_value=[])
        gp.get_virtual_record_ids_for_record_ids = AsyncMock(
            return_value={"rec-c1": "vr-c"},
        )
        gp.get_document = AsyncMock(return_value={})
        bad_blob = {
            "virtual_record_id": "vr-c",
            "id": "rec-c1",
            "record_name": "x",
            "record_type": "SQL_TABLE",
            "semantic_metadata": {},
            "block_containers": {
                "blocks": [],
                "block_groups": [{"type": "paragraph", "data": {}, "children": []}],
            },
        }
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value=bad_blob)
        vmap = {"vr-1": _make_sql_table_record()}
        flat = []
        await enrich_virtual_record_id_to_result_with_fk_children(
            vmap, blob, "org", graph_provider=gp, flattened_results=flat,
        )
        assert not [r for r in flat if (r.get("metadata") or {}).get("source") == "FK_ENRICHMENT"]
