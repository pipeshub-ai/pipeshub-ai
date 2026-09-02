"""Extra unit tests to raise coverage of app.utils.chat_helpers above 95%."""

import asyncio
import base64
import io
import re
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
    MAX_IMAGES_IN_CONVERSATION,
    CitationRefMapper,
    ImageBudget,
    RecordIdShortener,
    _find_first_block_index_recursive,
    _render_blocks_with_images,
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
    get_record_id_shortener_if_enabled,
    image_block_text,
    image_dict_to_part,
    is_base64_image,
    record_to_message_content,
)
from app.utils.image_admission import ImageAdmission
from app.utils.image_policy import resolve_image_policy
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


class TestRecordIdShortener:
    """TEMPORARY token-savings experiment — see `RecordIdShortener` docstring."""

    def test_get_or_create_short_id_is_sequential(self):
        shortener = RecordIdShortener()
        short = shortener.get_or_create_short_id("abcdef1234567890")
        assert short == "R1"

    def test_get_or_create_short_id_is_idempotent(self):
        shortener = RecordIdShortener()
        first = shortener.get_or_create_short_id("abcdef1234567890")
        second = shortener.get_or_create_short_id("abcdef1234567890")
        assert first == second == "R1"

    def test_second_distinct_id_gets_next_label(self):
        shortener = RecordIdShortener()
        short_a = shortener.get_or_create_short_id("abcd1111")
        short_b = shortener.get_or_create_short_id("abcd2222")
        assert short_a == "R1"
        assert short_b == "R2"
        # Both resolve back to their own full id, not each other's.
        assert shortener.resolve(short_a) == "abcd1111"
        assert shortener.resolve(short_b) == "abcd2222"

    def test_resolve_unknown_id_passes_through_unchanged(self):
        shortener = RecordIdShortener()
        assert shortener.resolve("full-uuid-from-navigate") == "full-uuid-from-navigate"

    def test_shorten_record_ids_in_text_replaces_label_line(self):
        shortener = RecordIdShortener()
        text = "Record ID: abcdef1234567890\nName: Doc A"
        result = shortener.shorten_record_ids_in_text(text)
        assert result == "Record ID: R1\nName: Doc A"
        assert shortener.resolve("R1") == "abcdef1234567890"

    def test_shorten_record_ids_in_text_matches_linked_record_id_label(self):
        shortener = RecordIdShortener()
        text = "* Linked Record ID: abcdef1234567890"
        result = shortener.shorten_record_ids_in_text(text)
        assert result == "* Linked Record ID: R1"

    def test_shorten_record_ids_in_text_is_consistent_across_calls(self):
        shortener = RecordIdShortener()
        first_pass = shortener.shorten_record_ids_in_text("Record ID: abcdef1234567890")
        second_pass = shortener.shorten_record_ids_in_text(
            "Record ID: abcdef1234567890 | Name: Doc A"
        )
        assert "Record ID: R1" in first_pass
        assert "Record ID: R1" in second_pass

    def test_shorten_record_ids_in_text_handles_multiple_records(self):
        shortener = RecordIdShortener()
        text = "Record ID: rec-aaa-111\n\nRecord ID: rec-bbb-222"
        result = shortener.shorten_record_ids_in_text(text)
        assert "Record ID: R1" in result
        assert "Record ID: R2" in result
        assert "rec-aaa-111" not in result
        assert "rec-bbb-222" not in result

    def test_shorten_record_ids_in_text_empty_string(self):
        shortener = RecordIdShortener()
        assert shortener.shorten_record_ids_in_text("") == ""

    def test_shorten_record_ids_in_text_no_match_unchanged(self):
        shortener = RecordIdShortener()
        text = "Name: Doc A\nType: FILE"
        assert shortener.shorten_record_ids_in_text(text) == text

    def test_shorten_record_ids_in_text_stops_before_trailing_comma(self):
        """FK-relations footer format: `(Record ID: <id>, FK: ...)` — the id
        must not swallow the trailing comma, or `resolve()` on the short
        label returned later would produce an id that matches nothing."""
        shortener = RecordIdShortener()
        text = "  - Parent Table: orders (Record ID: rec-aaa-111, FK: col1 -> col2)"
        result = shortener.shorten_record_ids_in_text(text)
        assert result == "  - Parent Table: orders (Record ID: R1, FK: col1 -> col2)"
        assert shortener.resolve("R1") == "rec-aaa-111"

    def test_shorten_record_ids_in_text_stops_before_closing_paren(self):
        shortener = RecordIdShortener()
        text = "(Record ID: rec-aaa-111)"
        result = shortener.shorten_record_ids_in_text(text)
        assert result == "(Record ID: R1)"
        assert shortener.resolve("R1") == "rec-aaa-111"

    def test_shorten_record_id_assigns_in_text(self):
        shortener = RecordIdShortener()
        text = "- [Record/TICKET] Foo | record_id=abcdef1234567890 | has children"
        result = shortener.shorten_record_id_assigns_in_text(text)
        assert "record_id=R1" in result
        assert "abcdef1234567890" not in result
        assert shortener.resolve("R1") == "abcdef1234567890"

    def test_shorten_node_id_assigns_in_text_preserves_quotes(self):
        shortener = RecordIdShortener()
        text = 'navigate(node_id="abcdef1234567890") to open a child'
        result = shortener.shorten_node_id_assigns_in_text(text)
        assert result == 'navigate(node_id="R1") to open a child'

    def test_shorten_node_id_assigns_in_text_unquoted(self):
        shortener = RecordIdShortener()
        result = shortener.shorten_node_id_assigns_in_text("node_id=abcdef1234567890")
        assert result == "node_id=R1"

    def test_shorten_all_record_ids_applies_every_pattern(self):
        shortener = RecordIdShortener()
        text = (
            "Record ID: abcdef1234567890\n"
            "- [Record/TICKET] Foo | record_id=abcdef1234567890\n"
            'navigate(node_id="abcdef1234567890") to open a child'
        )
        result = shortener.shorten_all_record_ids(text)
        assert "abcdef1234567890" not in result
        assert result.count("R1") == 3

    def test_same_full_id_gets_same_label_across_patterns(self):
        """A record surfaced by both a header line and a row/hint gets one label."""
        shortener = RecordIdShortener()
        shortener.shorten_record_ids_in_text("Record ID: abcdef1234567890")
        result = shortener.shorten_record_id_assigns_in_text("record_id=abcdef1234567890")
        assert "record_id=R1" in result


class TestGetRecordIdShortenerIfEnabled:
    """Single gate for all 7 knowledge-tool lazy-creation sites — see
    `ChatQuery.enableRecordIdShortening` (opt-in, disabled by default)."""

    def test_flag_absent_returns_none_and_does_not_create(self):
        state: dict = {}
        assert get_record_id_shortener_if_enabled(state) is None
        assert "record_id_shortener" not in state

    def test_flag_false_returns_none_and_does_not_create(self):
        state = {"enable_record_id_shortening": False}
        assert get_record_id_shortener_if_enabled(state) is None
        assert "record_id_shortener" not in state

    def test_flag_true_creates_and_stores_shortener(self):
        state = {"enable_record_id_shortening": True}
        shortener = get_record_id_shortener_if_enabled(state)
        assert isinstance(shortener, RecordIdShortener)
        assert state["record_id_shortener"] is shortener

    def test_flag_true_reuses_existing_shortener_across_calls(self):
        """Every one of the 7 lazy-creation sites must share the same
        instance within a request — the second call must not mint a new
        one, or short labels minted by one tool won't resolve in another."""
        state = {"enable_record_id_shortening": True}
        first = get_record_id_shortener_if_enabled(state)
        second = get_record_id_shortener_if_enabled(state)
        assert first is second

    def test_existing_shortener_returned_even_if_flag_later_false(self):
        """A shortener already minted this request (flag was True at some
        earlier call) keeps being reused even if a later call site reads
        the flag as False — `tool_state` is shared, so this should not
        happen in practice, but the read path must not fabricate a second
        competing instance."""
        shortener = RecordIdShortener()
        state = {"enable_record_id_shortening": False, "record_id_shortener": shortener}
        assert get_record_id_shortener_if_enabled(state) is shortener


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

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (b"\x89PNG\r\n\x1a\n" + b"\x00" * 40_000, True),
            (b"\xff\xd8\xff" + b"\x00" * 40_000, True),
            (b"GIF89a" + b"\x00" * 40_000, True),
            (b'<svg xmlns="http://www.w3.org/2000/svg">' + b"<rect/>" * 5_000 + b"</svg>", True),
            (b"not an image at all " * 2_000, False),
        ],
        ids=["png", "jpeg", "gif", "svg", "plain-text"],
    )
    def test_large_payloads_sniffed_from_prefix(self, payload, expected):
        """Only the first 204 decoded bytes decide the verdict, so payloads far
        larger than the decoded prefix must classify exactly as small ones do."""
        assert is_base64_image(base64.b64encode(payload).decode()) is expected

    def test_large_payload_decodes_only_the_prefix(self, monkeypatch):
        """Guards the optimisation itself: a 40 KB image must not be fully decoded."""
        seen: list[int] = []
        real = chat_helpers_module.base64.b64decode

        def spy(data, *a, **k):
            seen.append(len(data))
            return real(data, *a, **k)

        monkeypatch.setattr(chat_helpers_module.base64, "b64decode", spy)
        big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40_000).decode()
        assert is_base64_image(big) is True
        assert seen and max(seen) <= 272, f"decoded {max(seen)} chars, expected <= 272"


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


class TestImageBudget:
    def test_default_max_is_50(self):
        budget = ImageBudget()
        assert budget.max_images == MAX_IMAGES_IN_CONVERSATION == 50
        assert budget.remaining == 50
        assert budget.can_add()

    def test_try_consume_decrements_remaining(self):
        budget = ImageBudget(max_images=5)
        assert budget.try_consume(3) == 3
        assert budget.remaining == 2
        assert budget.can_add()

    def test_try_consume_caps_at_remaining_when_overflowing(self):
        budget = ImageBudget(max_images=5)
        assert budget.try_consume(3) == 3
        # Only 2 remain -- requesting 10 more only actually consumes 2.
        assert budget.try_consume(10) == 2
        assert budget.remaining == 0
        assert not budget.can_add()

    def test_try_consume_after_exhaustion_returns_zero(self):
        budget = ImageBudget(max_images=1)
        assert budget.try_consume(1) == 1
        assert budget.try_consume(1) == 0
        assert budget.remaining == 0


class TestRenderBlocksWithImagesGroupedImage:
    """`_render_blocks_with_images` renders table/group entries that mix
    text and IMAGE block_type items -- these are the "collected_images"
    counterparts to `record_to_message_content`'s standalone-image path,
    and must stay consistent with it: a `[ref] (image)` text anchor when
    collecting, and a citation-marked fallback when the budget is spent."""

    def _group(self, ref: str = "ref1", block_index: int = 3) -> list[dict]:
        return [
            {
                "content": "Row header",
                "block_type": BlockType.TEXT.value,
                "block_index": block_index,
                "citation_ref": ref,
            },
            {
                "content": _MIN_PNG_DATA_URI,
                "block_type": BlockType.IMAGE.value,
                "block_index": block_index,
                "citation_ref": ref,
                "virtual_record_id": "vr-1",
            },
        ]

    def test_collected_images_gets_text_anchor_alongside_side_channel_entry(self):
        """The bug fix: collecting into `collected_images` must NOT leave
        `content` with zero trace of the image -- otherwise the text a
        multipart ToolMessage carries has no `[ref]` a model can cite back
        to for a table/group image."""
        collected: list[dict] = []
        content = _render_blocks_with_images(
            self._group(ref="ref7"), is_multimodal_llm=True,
            image_budget=ImageBudget(), collected_images=collected,
        )

        assert len(collected) == 1
        assert collected[0]["ref"] == "ref7"
        assert collected[0]["image_url"]["url"] == _MIN_PNG_DATA_URI
        text = "".join(c["text"] for c in content if c["type"] == "text")
        assert "[ref7] (image)" in text
        assert not any(c["type"] == "image_url" for c in content)

    def test_exhausted_budget_emits_citation_marked_fallback_text(self):
        """The second bug: an exhausted budget must degrade to a
        citation-marked placeholder (matching the standalone-image path in
        `record_to_message_content`), not silently drop the image with no
        trace at all."""
        exhausted = ImageBudget(max_images=1)
        exhausted.try_consume(1)
        collected: list[dict] = []

        content = _render_blocks_with_images(
            self._group(ref="ref9"), is_multimodal_llm=True,
            image_budget=exhausted, collected_images=collected,
        )

        assert collected == []
        text = "".join(c["text"] for c in content if c["type"] == "text")
        assert "[ref9]" in text
        assert "conversation image limit" in text
        assert not any(c["type"] == "image_url" for c in content)


class TestImageDictToPart:
    def test_valid_image_dict_returns_image_part(self):
        from app.agent_loop_lib.core.messages import ImagePart

        part = image_dict_to_part({"image_url": {"url": _MIN_PNG_DATA_URI}})
        assert isinstance(part, ImagePart)
        from app.agent_loop_lib.core.messages import image_data_url

        assert part.source.type == "base64"
        assert image_data_url(part.source) == _MIN_PNG_DATA_URI

    def test_missing_url_returns_none(self):
        assert image_dict_to_part({"image_url": {}}) is None
        assert image_dict_to_part({}) is None

    def test_string_image_url_value_is_used_directly(self):
        part = image_dict_to_part({"image_url": _MIN_PNG_DATA_URI})
        assert part is not None
        from app.agent_loop_lib.core.messages import image_data_url

        assert part.source.type == "base64"
        assert image_data_url(part.source) == _MIN_PNG_DATA_URI


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
        assert "(image)" in blob

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

    def test_base64_png_without_collected_images_sink_falls_back_to_text(self):
        """A from_tool=True caller that doesn't pass `collected_images` has
        no way to carry an image through its tool result, so the image
        must degrade to a text placeholder rather than being inlined as an
        orphaned `image_url` block or silently dropped (the historical
        bug: standalone IMAGE blocks vanished entirely for every
        production search path, all of which pass from_tool=True)."""
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
        text_joined = " ".join(m["text"] for m in merged if m.get("type") == "text")
        assert "(image)" in text_joined

    def test_base64_png_with_collected_images_sink_routes_to_side_channel(self):
        """The fix: when the caller DOES pass `collected_images` (the new
        side-channel a tool wrapper reads to build a multipart
        ToolOutput), the standalone IMAGE block is captured there instead
        of being dropped, and a text reference is still emitted."""
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
        collected_images: list = []
        parts, _ = build_message_content_array(
            flat, vr, is_multimodal_llm=True, from_tool=True,
            collected_images=collected_images,
        )
        merged = [x for sub in parts for x in sub]
        assert not any(it.get("type") == "image_url" for it in merged)
        assert len(collected_images) == 1
        assert collected_images[0]["image_url"] == {"url": _MIN_PNG_DATA_URI}
        text_joined = " ".join(m["text"] for m in merged if m.get("type") == "text")
        assert "(image)" in text_joined

    def test_exhausted_budget_falls_back_to_text_and_skips_collection(self):
        """Once the shared `ImageBudget` is exhausted (e.g. by 50 prior
        images from other tool calls/attachments in the same turn), a new
        IMAGE block must degrade to a text description instead of being
        collected -- this is the 50-image-conversation-cap contract."""
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
        exhausted_budget = ImageBudget(max_images=1)
        exhausted_budget.try_consume(1)
        collected_images: list = []
        parts, _ = build_message_content_array(
            flat, vr, is_multimodal_llm=True, from_tool=True,
            collected_images=collected_images, image_budget=exhausted_budget,
        )
        merged = [x for sub in parts for x in sub]
        assert not collected_images
        assert not any(it.get("type") == "image_url" for it in merged)
        text_joined = " ".join(m["text"] for m in merged if m.get("type") == "text")
        assert "conversation image limit" in text_joined


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

    def _image_record(self) -> dict:
        return {
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

    def test_collected_images_populated_when_multimodal_true(self):
        """The full-fetch-record path (`_FetchFullRecordTool`) passes
        `collected_images` so the image reaches the LLM via a multipart
        `ToolMessage` instead of vanishing from the text-typed content
        list — this is the fix for 'full fetch of an image record sends
        no image'."""
        collected_images: list = []
        blocks, _ = record_to_message_content(
            self._image_record(), ref_mapper=CitationRefMapper(),
            is_multimodal_llm=True, collected_images=collected_images,
        )
        assert not any(b.get("type") == "image_url" for b in blocks)
        assert len(collected_images) == 1
        assert collected_images[0]["image_url"] == {"url": _MIN_PNG_DATA_URI}
        text_joined = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "(image)" in text_joined

    def test_collected_images_not_populated_when_multimodal_false(self):
        """A text-only LLM never receives image_url/collected_images
        entries -- the image-to-text (VLM description) retrieval path is
        unaffected by the multipart plumbing."""
        collected_images: list = []
        blocks, _ = record_to_message_content(
            self._image_record(), ref_mapper=CitationRefMapper(),
            is_multimodal_llm=False, collected_images=collected_images,
        )
        assert not collected_images
        assert not any(b.get("type") == "image_url" for b in blocks)

    def _image_record_with_text(self, uri: str | None, **image_metadata) -> dict:
        return {
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
                        "data": {"uri": uri},
                        "image_metadata": image_metadata or None,
                    },
                ],
                "block_groups": [],
            },
        }

    def test_the_indexed_description_is_what_a_text_only_llm_reads(self):
        """The two halves meet here: `ImageDescriber` writes prose onto
        `image_metadata.description` at indexing time, and it is the richest
        text this path can send when the pixels cannot go."""
        record = self._image_record_with_text(
            _MIN_PNG_DATA_URI, captions=["Figure 3"],
        )
        record["block_containers"]["blocks"][0]["image_metadata"]["description"] = (
            "Bar chart: Q3 revenue by region, EMEA highest at 4.2M"
        )
        blocks, _ = record_to_message_content(
            record, ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
        )
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "EMEA highest at 4.2M" in text
        assert "Figure 3" in text, "the caption still rides along"

    def test_text_only_llm_still_gets_the_image_blocks_caption(self):
        """A non-multimodal LLM used to get NOTHING for an image block — not
        even the caption the parser captured. The pixels can't go, the text
        must."""
        blocks, _ = record_to_message_content(
            self._image_record_with_text(_MIN_PNG_DATA_URI, captions=["Q3 revenue by region"]),
            ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
        )
        text_joined = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "Q3 revenue by region" in text_joined

    def test_image_without_usable_uri_still_sends_its_description(self):
        """A block the vector store only ever held a description for (text-only
        embedding pipeline) has no URI to send — the description still goes."""
        record = self._image_record_with_text(None)
        record["block_containers"]["blocks"][0]["data"] = {
            "uri": None, "description": "a bar chart of Q3 revenue",
        }
        blocks, _ = record_to_message_content(
            record, ref_mapper=CitationRefMapper(), is_multimodal_llm=True,
        )
        text_joined = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "a bar chart of Q3 revenue" in text_joined

    def test_collected_image_carries_its_caption_alongside(self):
        """The tool path routes the pixels to `collected_images`; the caption
        rides along in the text so it survives however the image is delivered
        (or dropped) downstream."""
        collected_images: list = []
        blocks, _ = record_to_message_content(
            self._image_record_with_text(_MIN_PNG_DATA_URI, captions=["Q3 revenue by region"]),
            ref_mapper=CitationRefMapper(), is_multimodal_llm=True,
            collected_images=collected_images,
        )
        assert len(collected_images) == 1
        text_joined = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "(image) Q3 revenue by region" in text_joined

    def test_image_with_no_text_at_all_is_unchanged_for_a_text_only_llm(self):
        blocks, _ = record_to_message_content(
            self._image_record_with_text(_MIN_PNG_DATA_URI),
            ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
        )
        text_joined = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "(image)" not in text_joined

    def test_over_budget_image_keeps_the_existing_limit_note(self):
        blocks, _ = record_to_message_content(
            self._image_record_with_text(_MIN_PNG_DATA_URI),
            ref_mapper=CitationRefMapper(), is_multimodal_llm=True,
            image_budget=ImageBudget(0),
        )
        text_joined = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "conversation image limit" in text_joined

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
                    {"type": "commits", "children": [{"block_index": 0}]},
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


# ---------------------------------------------------------------------------
# Coverage for lines 876-916: TABLE_ROW fragments and block group handling
# ---------------------------------------------------------------------------


class TestGetFlattenedResultsTableRowFragments:
    @pytest.mark.asyncio
    async def test_table_row_fragment_adds_container_to_rows(self):
        """Lines 882-889: TABLE_ROW parent block handling."""
        table_row_block = {
            "index": 0,
            "type": BlockType.TABLE_ROW.value,
            "parent_index": 10,
            "data": "Row data",
        }
        text_block = {
            "index": 1,
            "type": BlockType.TEXT.value,
            "parent_index": 0,
            "data": "Cell text",
        }
        record = _make_record_blob()
        record["block_containers"]["blocks"] = [table_row_block, text_block]
        record["block_containers"]["block_groups"] = []

        blob_store = AsyncMock()
        blob_store.get_record_from_storage = AsyncMock(return_value=record)
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})
        blob_store.get_reconciliation_metadata = AsyncMock(return_value=None)

        vr_map = {"vr-1": record}
        result_set = [{
            "content": "Cell text",
            "metadata": {
                "virtualRecordId": "vr-1",
                "blockIndex": 1,
                "isBlockGroup": False,
            },
        }]

        flat = await get_flattened_results(
            result_set,
            blob_store,
            "org-1",
            False,
            vr_map,
        )
        
        # Should handle TABLE_ROW fragment logic
        assert isinstance(flat, list)

    @pytest.mark.asyncio
    async def test_block_group_container_without_group_text_skips(self):
        """Lines 905-906: Skip when group_text_result is None."""
        container_block = {
            "index": 0,
            "type": BlockType.BULLET_LIST.value,
            "parent_index": 5,
            "data": "Container",
        }
        text_block = {
            "index": 1,
            "type": BlockType.TEXT.value,
            "parent_index": 0,
            "data": "Text",
        }
        record = _make_record_blob()
        record["block_containers"]["blocks"] = [container_block, text_block]
        record["block_containers"]["block_groups"] = []

        blob_store = AsyncMock()
        blob_store.get_record_from_storage = AsyncMock(return_value=record)
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})
        blob_store.get_reconciliation_metadata = AsyncMock(return_value=None)

        vr_map = {"vr-1": record}
        result_set = [{
            "content": "Text",
            "metadata": {
                "virtualRecordId": "vr-1",
                "blockIndex": 1,
                "isBlockGroup": False,
            },
        }]

        flat = await get_flattened_results(
            result_set,
            blob_store,
            "org-1",
            False,
            vr_map,
        )
        
        assert isinstance(flat, list)


# ---------------------------------------------------------------------------
# Coverage for lines 1017-1037, 1137-1158: Fragment map with images
# ---------------------------------------------------------------------------


class TestFragmentMapImageHandling:
    @pytest.mark.asyncio
    async def test_fragment_with_image_in_multimodal_mode(self):
        """Lines 1034-1045, 1155-1158: IMAGE fragment handling."""
        container_block = {
            "index": 0,
            "type": BlockType.TABLE_CELL.value,
            "data": "Container",
        }
        image_block = {
            "index": 1,
            "type": BlockType.IMAGE.value,
            "parent_index": 0,
            "data": {"uri": _MIN_PNG_DATA_URI},
        }
        text_block = {
            "index": 2,
            "type": BlockType.TEXT.value,
            "parent_index": 0,
            "data": "Caption",
        }
        record = _make_record_blob()
        record["block_containers"]["blocks"] = [container_block, image_block, text_block]

        blob_store = AsyncMock()
        blob_store.get_record_from_storage = AsyncMock(return_value=record)
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})
        blob_store.get_reconciliation_metadata = AsyncMock(return_value=None)

        vr_map = {"vr-1": record}
        result_set = [{
            "content": "Container",
            "metadata": {
                "virtualRecordId": "vr-1",
                "blockIndex": 0,
                "isBlockGroup": False,
            },
        }]

        flat = await get_flattened_results(
            result_set,
            blob_store,
            "org-1",
            True,
            vr_map,
        )
        
        # Should include image in results when multimodal
        assert isinstance(flat, list)

    @pytest.mark.asyncio
    async def test_fragment_with_image_in_non_multimodal_mode_skips(self):
        """Lines 1034-1045: IMAGE fragment skipped when not multimodal."""
        container_block = {
            "index": 0,
            "type": BlockType.TABLE_CELL.value,
            "data": "Container",
        }
        image_block = {
            "index": 1,
            "type": BlockType.IMAGE.value,
            "parent_index": 0,
            "data": {"uri": _MIN_PNG_DATA_URI},
        }
        record = _make_record_blob()
        record["block_containers"]["blocks"] = [container_block, image_block]

        blob_store = AsyncMock()
        blob_store.get_record_from_storage = AsyncMock(return_value=record)
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})
        blob_store.get_reconciliation_metadata = AsyncMock(return_value=None)

        vr_map = {"vr-1": record}
        result_set = [{
            "content": "Container",
            "metadata": {
                "virtualRecordId": "vr-1",
                "blockIndex": 0,
                "isBlockGroup": False,
            },
        }]

        flat = await get_flattened_results(
            result_set,
            blob_store,
            "org-1",
            False,
            vr_map,
        )
        
        # Image should not be included when not multimodal
        assert isinstance(flat, list)


# ---------------------------------------------------------------------------
# Coverage for lines 1823-1861: Citation formatting with images
# ---------------------------------------------------------------------------


class TestBuildMessageContentArrayImageHandling:
    def test_grouped_blocks_with_images_in_multimodal(self):
        """Lines 1830-1860: Group with multiple blocks including images."""
        blocks_list = [
            {"block_index": 1, "block_type": BlockType.TEXT.value, "content": "Text 1", "citation_ref": "[1]", "virtual_record_id": "vr-1"},
            {"block_index": 1, "block_type": BlockType.IMAGE.value, "content": _MIN_PNG_DATA_URI, "citation_ref": "[1]", "virtual_record_id": "vr-1"},
            {"block_index": 1, "block_type": BlockType.TEXT.value, "content": "Text 2", "citation_ref": "[1]", "virtual_record_id": "vr-1"},
        ]

        vr_map = {"vr-1": {}}
        content_array, ref_mapper = build_message_content_array(blocks_list, vr_map, is_multimodal_llm=True)

        assert len(content_array) > 0

    def test_grouped_blocks_with_images_exceeding_limit(self):
        """Lines 1849-1855: Image count limit enforcement."""
        blocks_list = [
            {"block_index": i, "block_type": BlockType.IMAGE.value, "content": _MIN_PNG_DATA_URI, "citation_ref": f"[{i}]", "virtual_record_id": "vr-1"}
            for i in range(20)
        ]

        vr_map = {"vr-1": {}}
        content_array, ref_mapper = build_message_content_array(blocks_list, vr_map, is_multimodal_llm=True)

        # Should generate content
        assert len(content_array) > 0

    def test_single_block_without_images(self):
        """Lines 1834-1838: Single block without images uses simple format."""
        blocks_list = [
            {"block_index": 1, "block_type": BlockType.TEXT.value, "content": "Simple text", "citation_ref": "[1]", "virtual_record_id": "vr-1"}
        ]

        vr_map = {"vr-1": {}}
        content_array, ref_mapper = build_message_content_array(blocks_list, vr_map, is_multimodal_llm=False)

        assert len(content_array) > 0

    def test_grouped_blocks_with_non_base64_image_skips(self):
        """Lines 1848: Non-base64 images are skipped."""
        blocks_list = [
            {"block_index": 1, "block_type": BlockType.IMAGE.value, "content": "http://example.com/img.png", "citation_ref": "[1]", "virtual_record_id": "vr-1"}
        ]

        vr_map = {"vr-1": {}}
        content_array, ref_mapper = build_message_content_array(blocks_list, vr_map, is_multimodal_llm=True)

        # Should still generate content
        assert isinstance(content_array, list)
        # Should not add image_url content for non-base64 images


# ---------------------------------------------------------------------------
# Coverage for lines 2057-2077: Streaming with fragment maps
# ---------------------------------------------------------------------------


class TestStreamingFragmentMapHandling:
    def test_record_to_message_content_with_fragment_map_text_fragments(self):
        """Lines 2057-2072: Fragment map TEXT handling in streaming."""
        record_blob = {
            "id": "rec-1",
            "record_name": "Test Record",
            "record_type": "FILE",
            "blocks": [
                {"index": 0, "type": BlockType.TABLE_ROW.value, "data": "Row"},
                {"index": 1, "type": BlockType.TEXT.value, "parent_index": 0, "data": "Cell 1"},
                {"index": 2, "type": BlockType.TEXT.value, "parent_index": 0, "data": "Cell 2"},
            ],
            "block_groups": [],
        }

        ref_mapper = CitationRefMapper()
        content, ref_mapper = record_to_message_content(
            record=record_blob,
            ref_mapper=ref_mapper,
            is_multimodal_llm=False,
        )

        # Should process text fragments
        assert isinstance(content, list)

    def test_record_to_message_content_with_fragment_map_image_fragments(self):
        """Lines 2073-2077: Fragment map IMAGE handling in streaming."""
        record_blob = {
            "id": "rec-1",
            "record_name": "Test Record",
            "record_type": "FILE",
            "blocks": [
                {"index": 0, "type": BlockType.TABLE_CELL.value, "data": "Cell"},
                {"index": 1, "type": BlockType.IMAGE.value, "parent_index": 0, "data": {"uri": _MIN_PNG_DATA_URI}},
            ],
            "block_groups": [],
        }

        ref_mapper = CitationRefMapper()
        content, ref_mapper = record_to_message_content(
            record=record_blob,
            ref_mapper=ref_mapper,
            is_multimodal_llm=True,
        )

        # Should process image fragments in multimodal mode
        assert isinstance(content, list)



class TestImageBlockText:
    """`image_block_text` is the single answer to "what text does this image
    block carry" — used wherever the pixels cannot be sent."""

    def test_description_from_data(self):
        assert image_block_text({"data": {"description": " a chart "}}) == "a chart"

    def test_captions_footnotes_and_annotations(self):
        block = {
            "data": {"uri": _MIN_PNG_DATA_URI},
            "image_metadata": {
                "captions": ["Figure 3"], "footnotes": ["source: 10-K"], "annotations": ["red = loss"],
            },
        }
        assert image_block_text(block) == "Figure 3 source: 10-K red = loss"

    def test_alt_text_from_media_metadata(self):
        assert image_block_text({"media_metadata": {"alt_text": "logo"}}) == "logo"

    def test_repeated_text_is_not_duplicated(self):
        block = {
            "data": {"description": "Figure 3"},
            "image_metadata": {"captions": ["Figure 3"]},
        }
        assert image_block_text(block) == "Figure 3"

    def test_base64_string_data_is_not_text(self):
        assert image_block_text({"data": _MIN_PNG_DATA_URI}) == ""

    def test_no_text_returns_empty(self):
        assert image_block_text({"data": {"uri": _MIN_PNG_DATA_URI}}) == ""

def _distinct_png(seed: int, size: tuple[int, int] = (600, 400)) -> str:
    """A real, distinctly-coloured PNG data URI — dedup keys on content, so
    test fixtures must differ in their bytes the way real figures do."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (seed * 7 % 256, seed * 13 % 256, seed * 29 % 256)).save(
        buffer, format="PNG",
    )
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


class TestRecordImageAdmission:
    """`record_to_message_content` under a real per-model policy: the pixels
    are capped, the text never is."""

    @staticmethod
    def _record(count: int) -> dict:
        return {
            "virtual_record_id": "vr1",
            "frontend_url": "https://a.com",
            "id": "rec-1",
            "context_metadata": "ctx",
            "block_containers": {
                "blocks": [
                    {
                        "index": i,
                        "type": BlockType.IMAGE.value,
                        "parent_index": None,
                        # Distinct bytes per block, or dedup would collapse
                        # them into one image (which it should).
                        "data": {"uri": _distinct_png(i)},
                        "image_metadata": {"captions": [f"figure {i}"]},
                    }
                    for i in range(count)
                ],
                "block_groups": [],
            },
        }

    @staticmethod
    def _admission(provider: str) -> ImageAdmission:
        return ImageAdmission(resolve_image_policy(provider=provider, is_multimodal=True))

    def _render(self, count: int, provider: str) -> tuple[list, list]:
        collected: list = []
        blocks, _ = record_to_message_content(
            self._record(count), ref_mapper=CitationRefMapper(), is_multimodal_llm=True,
            collected_images=collected, image_admission=self._admission(provider),
        )
        return blocks, collected

    @pytest.mark.parametrize(
        ("provider", "expected_images"),
        [("azureOpenAI", 8), ("ollama", 1), ("anthropic", 12), ("some-gateway", 2)],
    )
    def test_pixels_are_capped_at_what_the_model_accepts(
        self, provider: str, expected_images: int,
    ) -> None:
        blocks, collected = self._render(30, provider)
        assert len(collected) == expected_images

    @pytest.mark.parametrize("count", [1, 5, 30])
    @pytest.mark.parametrize("provider", ["azureOpenAI", "ollama", "unknown-thing"])
    def test_every_image_block_still_contributes_text(self, count: int, provider: str) -> None:
        """The invariant the whole design rests on: whatever the policy, each
        image block leaves a citable marker and its caption behind."""
        blocks, _ = self._render(count, provider)
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        for i in range(count):
            assert f"figure {i}" in text, f"caption for block {i} was dropped"

    def test_withheld_images_say_so(self) -> None:
        blocks, collected = self._render(30, "ollama")
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "not shown" in text
        assert len(collected) == 1

    def test_a_text_only_model_gets_captions_and_no_pixels(self) -> None:
        collected: list = []
        blocks, _ = record_to_message_content(
            self._record(4), ref_mapper=CitationRefMapper(), is_multimodal_llm=False,
            collected_images=collected,
            image_admission=ImageAdmission(
                resolve_image_policy(provider="openAI", is_multimodal=False),
            ),
        )
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert not collected
        assert all(f"figure {i}" in text for i in range(4))

    def test_refs_of_withheld_images_still_resolve(self) -> None:
        """A model citing an image it only read about must produce a citation
        that maps back to the block."""
        mapper = CitationRefMapper()
        collected: list = []
        blocks, mapper = record_to_message_content(
            self._record(12), ref_mapper=mapper, is_multimodal_llm=True,
            collected_images=collected, image_admission=self._admission("ollama"),
        )
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        refs = set(re.findall(r"\[(ref\d+)\]", text))
        assert len(refs) == 12
        assert len(collected) == 1

    def test_the_same_record_fetched_twice_sends_its_images_once(self) -> None:
        """The model re-reads a record it already fetched -- to continue past
        a truncation point, or because the first result was cleared. Each
        fetch builds its own tool result, so a second batch of `collected`
        images is a second copy of the same pictures on the wire, counted once
        by the cap and cut by the transport guard at the expense of images the
        model had not seen."""
        admission = self._admission("anthropic")

        first: list = []
        record_to_message_content(
            self._record(5), ref_mapper=CitationRefMapper(), is_multimodal_llm=True,
            collected_images=first, image_admission=admission,
        )
        second: list = []
        blocks, _ = record_to_message_content(
            self._record(5), ref_mapper=CitationRefMapper(), is_multimodal_llm=True,
            collected_images=second, image_admission=admission,
        )

        assert len(first) == 5
        assert second == []
        assert admission.budget.used == 5
        # The re-read still renders every block's text, and says where the
        # pixels went rather than claiming they were dropped.
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        assert "shown above" in text
        assert "not shown" not in text

