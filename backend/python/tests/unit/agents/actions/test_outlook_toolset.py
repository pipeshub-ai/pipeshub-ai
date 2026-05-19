"""
Unit tests for app.agents.actions.microsoft.outlook.outlook

Covers module-level helpers, Pydantic schemas, Outlook._handle_error /
_serialize_response, and all @tool methods with mocked OutlookCalendarContactsDataSource.
"""

import base64
import importlib
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agents.actions.microsoft.outlook.outlook as outlook_module
from app.agents.actions.microsoft.outlook.outlook import (
    CreateCalendarEventInput,
    DeleteCalendarEventInput,
    ForwardMessageInput,
    GetCalendarEventInput,
    GetCalendarEventsInput,
    GetMailFoldersInput,
    GetMeetingTranscriptsInput,
    GetMessageInput,
    GetRecurringEventsEndingInput,
    GetRecurringEventsInput,
    Outlook,
    ReplyAllToMessageInput,
    ReplyToMessageInput,
    SearchCalendarEventsInput,
    SearchMessagesInput,
    SendMailInput,
    StageAttachmentToBlobInput,
    UpdateCalendarEventInput,
    _build_recurrence_body,
    _decode_graph_file_attachment_content_bytes,
    _normalize_odata,
    _response_data,
    _serialize_graph_obj,
    _status_label,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_graph_response(*, success=True, data=None, error=None):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    return resp


def _build_outlook(client_methods: dict | None = None, state: dict | None = None) -> Outlook:
    """Instantiate Outlook with a stubbed data-source client."""
    ol = Outlook.__new__(Outlook)
    client = MagicMock()
    for name, value in (client_methods or {}).items():
        setattr(client, name, value)
    ol.client = client
    ol.state = state
    return ol


def _build_outlook_via_init(state: dict | None = None) -> Outlook:
    """Construct through ``__init__`` to cover datasource wiring."""
    graph_client = MagicMock()
    with patch(
        "app.agents.actions.microsoft.outlook.outlook.OutlookCalendarContactsDataSource",
    ) as mock_ds_cls:
        mock_ds_cls.return_value = MagicMock()
        ol = Outlook(graph_client, state=state)
    return ol


def _ok_tuple(result):
    ok, payload = result
    assert ok is True, f"Expected success, got: {payload}"
    return json.loads(payload)


def _err_tuple(result):
    ok, payload = result
    assert ok is False, f"Expected error, got success: {payload}"
    return json.loads(payload)


# ===========================================================================
# Module-level helpers
# ===========================================================================


class TestDecodeGraphFileAttachmentContentBytes:
    def test_decodes_base64_text_payload(self):
        raw = b"hello"
        encoded = base64.b64encode(raw)
        assert _decode_graph_file_attachment_content_bytes(encoded) == raw

    def test_empty_bytes(self):
        assert _decode_graph_file_attachment_content_bytes(b"") == b""

    def test_kiota_fixed_version_returns_blob_unchanged(self):
        raw = base64.b64encode(b"hello")
        with patch.object(
            outlook_module,
            "_KIOTA_JSON_VERSIONS_WITH_DECODED_CONTENT_BYTES",
            frozenset({"1.99.0"}),
        ):
            with patch(
                "app.agents.actions.microsoft.outlook.outlook.importlib.metadata.version",
                return_value="1.99.0",
            ):
                assert _decode_graph_file_attachment_content_bytes(raw) == raw

    def test_package_not_found_still_decodes(self):
        encoded = base64.b64encode(b"data")
        with patch(
            "app.agents.actions.microsoft.outlook.outlook.importlib.metadata.version",
            side_effect=importlib.metadata.PackageNotFoundError("nope"),
        ):
            assert _decode_graph_file_attachment_content_bytes(encoded) == b"data"


class TestSerializeGraphObj:
    def test_primitives_and_collections(self):
        assert _serialize_graph_obj(None) is None
        assert _serialize_graph_obj("x") == "x"
        assert _serialize_graph_obj(42) == 42
        assert _serialize_graph_obj(1.5) == 1.5
        assert _serialize_graph_obj(True) is True
        assert _serialize_graph_obj([1, {"a": 2}]) == [1, {"a": 2}]

    def test_plain_object_via_vars(self):
        class Obj:
            def __init__(self):
                self.id = "abc"
                self._hidden = "skip"

        result = _serialize_graph_obj(Obj())
        assert result == {"id": "abc"}

    def test_kiota_json_writer_success(self):
        writer_content = b'{"id":"m1"}'

        class _FakeWriter:
            def write_object_value(self, *_a, **_k):
                return None

            def get_serialized_content(self):
                return writer_content

        class _Kiota:
            def get_field_deserializers(self):
                return {}

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            return_value=_FakeWriter(),
        ):
            assert _serialize_graph_obj(_Kiota()) == {"id": "m1"}

    def test_kiota_backing_store_with_nested_failure(self):
        class _BackingStore:
            def enumerate_(self):
                yield "id", "ok"
                yield "bad", object()

        class _Kiota:
            def get_field_deserializers(self):
                return {}

            def __init__(self):
                self.backing_store = _BackingStore()
                self.additional_data = {"extra": object()}

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            side_effect=RuntimeError("fail"),
        ):
            result = _serialize_graph_obj(_Kiota())
        assert result["id"] == "ok"
        assert isinstance(result["bad"], str)
        assert isinstance(result["extra"], str)

    def test_vars_typeerror_and_additional_data(self):
        class NoVars:
            __slots__ = ()

            def get_field_deserializers(self):
                return {}

            @property
            def additional_data(self):
                return {"meta": "v"}

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            side_effect=RuntimeError("fail"),
        ):
            with patch(
                "app.agents.actions.microsoft.outlook.outlook.vars",
                side_effect=TypeError("no vars"),
            ):
                result = _serialize_graph_obj(NoVars())
        assert result == {"meta": "v"}

    def test_empty_kiota_object_falls_back_to_str(self):
        class EmptyKiota:
            def get_field_deserializers(self):
                return {}

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            side_effect=RuntimeError("fail"),
        ):
            result = _serialize_graph_obj(EmptyKiota())
        assert isinstance(result, str)


class TestNormalizeOdata:
    def test_adds_results_alias_for_value_list(self):
        data = {"value": [{"id": "1"}]}
        out = _normalize_odata(data)
        assert out["results"] == out["value"]

    def test_leaves_existing_results(self):
        data = {"value": [], "results": []}
        out = _normalize_odata(data)
        assert out["results"] == []


class TestResponseData:
    def test_serializes_response_data(self):
        resp = _mock_graph_response(data={"value": [{"id": "m1"}]})
        data = _response_data(resp)
        assert data["results"] == [{"id": "m1"}]

    def test_none_data_returns_none(self):
        assert _response_data(_mock_graph_response(data=None)) is None


class TestStatusLabel:
    def test_known_and_unknown(self):
        assert _status_label("2") == "busy"
        assert _status_label("9") == "unknown"


class TestBuildRecurrenceBody:
    def test_requires_pattern_and_range(self):
        body = {
            "pattern": {"type": "daily", "interval": 1},
            "range": {"type": "noEnd", "startDate": "2026-03-01"},
        }
        assert _build_recurrence_body(body)["pattern"]["type"] == "daily"

    def test_missing_keys_raises(self):
        with pytest.raises(ValueError, match="pattern.*range"):
            _build_recurrence_body({"pattern": {}})

    def test_non_dict_raises(self):
        with pytest.raises(ValueError, match="must be a dict"):
            _build_recurrence_body("not-a-dict")  # type: ignore[arg-type]


# ===========================================================================
# Pydantic schemas
# ===========================================================================


class TestSendMailInput:
    def test_required_fields(self):
        s = SendMailInput(
            to_recipients=["a@x.com"],
            subject="Hi",
            body="Hello",
        )
        assert s.body_type == "Text"
        assert s.cc_recipients is None


class TestSearchMessagesInput:
    def test_defaults(self):
        s = SearchMessagesInput()
        assert s.top == 10
        assert s.orderby == "receivedDateTime desc"


class TestCreateCalendarEventInput:
    def test_recurrence_optional(self):
        s = CreateCalendarEventInput(
            subject="Sync",
            start_datetime="2026-05-01T10:00:00Z",
            end_datetime="2026-05-01T11:00:00Z",
        )
        assert s.is_online_meeting is False
        assert s.recurrence is None


class TestGetRecurringEventsEndingInput:
    def test_top_bounds(self):
        s = GetRecurringEventsEndingInput(end_before="2026-12-31T23:59:59Z")
        assert s.top == 10


# ===========================================================================
# Outlook helpers
# ===========================================================================


class TestOutlookHandleError:
    def test_attribute_error_auth_message(self):
        ol = _build_outlook()
        ok, body = ol._handle_error(
            AttributeError("'NoneType' object has no attribute 'me'"),
            "search messages",
        )
        parsed = json.loads(body)
        assert ok is False
        assert "not authenticated" in parsed["error"].lower()

    def test_oauth_error_message(self):
        ol = _build_outlook()
        ok, body = ol._handle_error(
            RuntimeError("Request failed: unauthorized"),
            "send email",
        )
        parsed = json.loads(body)
        assert "OAuth" in parsed["error"]

    def test_recurrence_value_error_not_treated_as_auth(self):
        ol = _build_outlook()
        ok, body = ol._handle_error(
            ValueError("invalid recurrence pattern"),
            "create calendar event",
        )
        parsed = json.loads(body)
        assert parsed["error"] == "invalid recurrence pattern"

    def test_generic_error(self):
        ol = _build_outlook()
        ok, body = ol._handle_error(RuntimeError("timeout"), "op")
        assert json.loads(body)["error"] == "timeout"


class TestOutlookInit:
    def test_init_wires_datasource_and_state(self):
        ol = _build_outlook_via_init(state={"org_id": "o1"})
        assert ol.state == {"org_id": "o1"}

    def test_state_from_kwargs(self):
        graph_client = MagicMock()
        state = {"conversation_id": "c1"}
        with patch(
            "app.agents.actions.microsoft.outlook.outlook.OutlookCalendarContactsDataSource",
        ):
            ol = Outlook(graph_client, **{"state": state})
        assert ol.state is state


class TestOutlookSerializeResponse:
    def test_delegates_like_module_helper(self):
        assert Outlook._serialize_response({"id": "1"}) == {"id": "1"}

    def test_kiota_object_via_json_writer(self):
        writer_content = b'{"id":"evt-1","subject":"Meet"}'

        class _FakeWriter:
            def write_object_value(self, *_args, **_kwargs):
                return None

            def get_serialized_content(self):
                return writer_content

        class _KiotaLike:
            def get_field_deserializers(self):
                return {}

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            return_value=_FakeWriter(),
        ):
            result = Outlook._serialize_response(_KiotaLike())
        assert result == {"id": "evt-1", "subject": "Meet"}

    def test_kiota_backing_store_fallback(self):
        class _BackingStore:
            def enumerate_(self):
                yield "id", "abc"
                yield "_private", "hidden"

        class _KiotaLike:
            def get_field_deserializers(self):
                return {}

            def __init__(self):
                self.backing_store = _BackingStore()
                self.additional_data = {"extra": "val"}

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            side_effect=RuntimeError("writer failed"),
        ):
            result = Outlook._serialize_response(_KiotaLike())
        assert result["id"] == "abc"
        assert result["extra"] == "val"
        assert "_private" not in result

    def test_generic_object_vars_and_additional_data(self):
        class Thing:
            def __init__(self):
                self.visible = "yes"
                self.additional_data = {"addon": 1}

        result = Outlook._serialize_response(Thing())
        assert result["visible"] == "yes"
        assert result["addon"] == 1

    def test_nested_serialize_exception_becomes_str(self):
        class BadNested:
            pass

        class Thing:
            def __init__(self):
                self.nested = BadNested()

        result = Outlook._serialize_response(Thing())
        assert isinstance(result["nested"], str)

    def test_object_without_dict_falls_back_to_str(self):
        assert isinstance(Outlook._serialize_response(object()), str)

    def test_kiota_writer_empty_dict_falls_through(self):
        class _FakeWriter:
            def write_object_value(self, *_a, **_k):
                return None

            def get_serialized_content(self):
                return b"{}"

        class _Kiota:
            def get_field_deserializers(self):
                return {}

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            return_value=_FakeWriter(),
        ):
            result = Outlook._serialize_response(_Kiota())
        assert isinstance(result, str)


# ===========================================================================
# Mail tools
# ===========================================================================


class TestSendEmail:
    @pytest.mark.asyncio
    async def test_success_with_cc_bcc(self):
        ol = _build_outlook({
            "me_create_messages": AsyncMock(
                return_value=_mock_graph_response(data={"id": "MSG1"}),
            ),
            "me_messages_message_send": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
        })
        ok, body = await ol.send_email(
            to_recipients=[" to@x.com ", ""],
            subject="Hello",
            body="Body",
            cc_recipients=["cc@x.com"],
            bcc_recipients=["bcc@x.com"],
        )
        parsed = _ok_tuple((ok, body))
        assert parsed["message_id"] == "MSG1"
        assert parsed["recipients"]["cc"] == ["cc@x.com"]

    @pytest.mark.asyncio
    async def test_create_draft_failure(self):
        ol = _build_outlook({
            "me_create_messages": AsyncMock(
                return_value=_mock_graph_response(success=False, error="denied"),
            ),
        })
        err = _err_tuple(await ol.send_email(
            to_recipients=["a@x.com"], subject="S", body="B",
        ))
        assert "denied" in err["error"]

    @pytest.mark.asyncio
    async def test_missing_message_id_after_create(self):
        ol = _build_outlook({
            "me_create_messages": AsyncMock(
                return_value=_mock_graph_response(data={}),
            ),
        })
        err = _err_tuple(await ol.send_email(
            to_recipients=["a@x.com"], subject="S", body="B",
        ))
        assert "message ID" in err["error"]

    @pytest.mark.asyncio
    async def test_send_failure(self):
        ol = _build_outlook({
            "me_create_messages": AsyncMock(
                return_value=_mock_graph_response(data={"id": "MSG1"}),
            ),
            "me_messages_message_send": AsyncMock(
                return_value=_mock_graph_response(success=False, error="send fail"),
            ),
        })
        err = _err_tuple(await ol.send_email(
            to_recipients=["a@x.com"], subject="S", body="B",
        ))
        assert "send fail" in err["error"]

    @pytest.mark.asyncio
    async def test_exception_uses_handle_error(self):
        ol = _build_outlook({
            "me_create_messages": AsyncMock(side_effect=RuntimeError("boom")),
        })
        err = _err_tuple(await ol.send_email(
            to_recipients=["a@x.com"], subject="S", body="B",
        ))
        assert err["error"] == "boom"


class TestReplyTools:
    @pytest.mark.asyncio
    async def test_reply_to_message_success(self):
        ol = _build_outlook({
            "me_messages_message_reply": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
        })
        parsed = _ok_tuple(await ol.reply_to_message("MSG1", "Thanks"))
        assert "Reply sent" in parsed["message"]

    @pytest.mark.asyncio
    async def test_reply_all_success_and_failure(self):
        ol = _build_outlook({
            "me_messages_message_reply_all": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
        })
        parsed = _ok_tuple(await ol.reply_all_to_message("MSG1", "Hi all"))
        assert "Reply-all sent" in parsed["message"]

        ol.client.me_messages_message_reply_all = AsyncMock(
            return_value=_mock_graph_response(success=False, error="nope"),
        )
        err = _err_tuple(await ol.reply_all_to_message("MSG1", "Hi all"))
        assert "nope" in err["error"]

    @pytest.mark.asyncio
    async def test_forward_with_comment(self):
        ol = _build_outlook({
            "me_messages_message_forward": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
        })
        parsed = _ok_tuple(await ol.forward_message(
            "MSG1", ["fwd@x.com"], comment="FYI",
        ))
        assert "forwarded" in parsed["message"].lower()


class TestSearchAndGetMessage:
    @pytest.mark.asyncio
    async def test_search_messages_success(self):
        ol = _build_outlook({
            "me_list_messages": AsyncMock(
                return_value=_mock_graph_response(
                    data={"value": [{"id": "m1", "subject": "Hi"}]},
                ),
            ),
        })
        parsed = _ok_tuple(await ol.search_messages(top=5))
        assert parsed["count"] == 1
        ol.client.me_list_messages.assert_awaited_once_with(
            search=None, filter=None, top=5, orderby="receivedDateTime desc",
        )

    @pytest.mark.asyncio
    async def test_get_message_no_data(self):
        ol = _build_outlook({
            "me_get_message": AsyncMock(
                return_value=_mock_graph_response(success=True, data=None),
            ),
        })
        parsed = _ok_tuple(await ol.get_message("MSG1"))
        assert "error" in parsed

    @pytest.mark.asyncio
    async def test_list_message_attachments_maps_fields(self):
        ol = _build_outlook({
            "me_messages_list_attachments": AsyncMock(
                return_value=_mock_graph_response(data={
                    "value": [{
                        "id": "att1",
                        "name": "file.pdf",
                        "contentType": "application/pdf",
                        "size": 100,
                        "isInline": False,
                        "@odata.type": "#fileAttachment",
                    }],
                }),
            ),
        })
        parsed = _ok_tuple(await ol.list_message_attachments("MSG1"))
        assert parsed["count"] == 1
        assert parsed["attachments"][0]["attachment_id"] == "att1"


class _NonMappingStateContainer:
    """Truthy object without ``.get`` — must not be coerced via ``or {}``."""

    pass


class TestStageAttachmentToBlob:
    @pytest.mark.asyncio
    async def test_non_dict_state_rejected(self):
        ol = _build_outlook(state=_NonMappingStateContainer())
        err = _err_tuple(await ol.stage_attachment_to_blob("MSG", "ATT"))
        assert "chat state container" in err["error"]

    @pytest.mark.asyncio
    async def test_missing_org_context(self):
        ol = _build_outlook(state={})
        err = _err_tuple(await ol.stage_attachment_to_blob("MSG", "ATT"))
        assert "org_id" in err["error"]

    @pytest.mark.asyncio
    async def test_success_registers_document(self):
        raw = b"%PDF-1.4"
        attachment = MagicMock()
        attachment.odata_type = "#microsoft.graph.fileAttachment"
        attachment.name = "doc.pdf"
        attachment.content_type = "application/pdf"
        attachment.content_bytes = base64.b64encode(raw)

        blob_store = MagicMock()
        blob_store.save_conversation_file_to_storage = AsyncMock(
            return_value={
                "documentId": "doc-123",
                "downloadUrl": "https://blob/x",
                "storageType": "s3",
            },
        )

        state = {
            "org_id": "org-1",
            "config_service": MagicMock(),
            "conversation_id": "conv-1",
            "blob_store": blob_store,
            "document_id_to_url": {},
        }
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=attachment),
            ),
        }, state=state)

        parsed = _ok_tuple(await ol.stage_attachment_to_blob("MSG", "ATT"))
        assert parsed["document_id"] == "doc-123"
        registry_entry = state["document_id_to_url"]["doc-123"]
        assert registry_entry.filename == "doc.pdf"
        blob_store.save_conversation_file_to_storage.assert_awaited_once_with(
            org_id="org-1",
            conversation_id="conv-1",
            file_name="doc.pdf",
            file_bytes=raw,
            content_type="application/pdf",
            custom_metadata=[{"key": "isTemporary", "value": True}],
        )

    @pytest.mark.asyncio
    async def test_missing_conversation_id(self):
        ol = _build_outlook(state={
            "org_id": "o",
            "config_service": MagicMock(),
            "document_id_to_url": {},
        })
        err = _err_tuple(await ol.stage_attachment_to_blob("MSG", "ATT"))
        assert "conversation_id" in err["error"]

    @pytest.mark.asyncio
    async def test_lazy_blob_store_construction(self):
        raw = b"data"
        attachment = MagicMock()
        attachment.odata_type = "#microsoft.graph.fileAttachment"
        attachment.name = "f.txt"
        attachment.content_type = "text/plain"
        attachment.content_bytes = base64.b64encode(raw)

        state = {
            "org_id": "org-1",
            "config_service": MagicMock(),
            "conversation_id": "conv-1",
            "document_id_to_url": {},
        }
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=attachment),
            ),
        }, state=state)

        mock_blob = MagicMock()
        mock_blob.save_conversation_file_to_storage = AsyncMock(
            return_value={"documentId": "d9", "downloadUrl": "https://blob/d9"},
        )
        with patch(
            "app.agents.actions.microsoft.outlook.outlook.BlobStorage",
            return_value=mock_blob,
        ):
            parsed = _ok_tuple(await ol.stage_attachment_to_blob("MSG", "ATT"))
        assert parsed["document_id"] == "d9"
        assert state["blob_store"] is mock_blob

    @pytest.mark.asyncio
    async def test_zero_byte_attachment_rejected(self):
        attachment = MagicMock()
        attachment.odata_type = "#microsoft.graph.fileAttachment"
        attachment.name = "empty.bin"
        attachment.content_type = "application/octet-stream"
        attachment.content_bytes = b"cHJpdmF0ZQ=="

        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=attachment),
            ),
        }, state={
            "org_id": "o",
            "config_service": MagicMock(),
            "conversation_id": "c",
            "blob_store": MagicMock(),
            "document_id_to_url": {},
        })
        with patch(
            "app.agents.actions.microsoft.outlook.outlook."
            "_decode_graph_file_attachment_content_bytes",
            return_value=b"",
        ):
            err = _err_tuple(await ol.stage_attachment_to_blob("MSG", "ATT"))
        assert "zero bytes" in err["error"].lower()

    @pytest.mark.asyncio
    async def test_non_file_attachment_rejected(self):
        attachment = MagicMock()
        attachment.odata_type = "#microsoft.graph.itemAttachment"
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=attachment),
            ),
        }, state={
            "org_id": "o",
            "config_service": MagicMock(),
            "conversation_id": "c",
            "blob_store": MagicMock(),
            "document_id_to_url": {},
        })
        err = _err_tuple(await ol.stage_attachment_to_blob("MSG", "ATT"))
        assert "fileAttachment" in err["error"]


class TestGetMailFolders:
    @pytest.mark.asyncio
    async def test_success(self):
        ol = _build_outlook({
            "me_list_mail_folders": AsyncMock(
                return_value=_mock_graph_response(
                    data={"value": [{"id": "inbox", "displayName": "Inbox"}]},
                ),
            ),
        })
        parsed = _ok_tuple(await ol.get_mail_folders(top=5))
        assert parsed["count"] == 1


# ===========================================================================
# Calendar tools
# ===========================================================================


class TestCalendarCrud:
    @pytest.mark.asyncio
    async def test_get_calendar_events_success(self):
        ol = _build_outlook({
            "me_calendar_list_calendar_view": AsyncMock(
                return_value=_mock_graph_response(
                    data={"value": [{"id": "evt1", "subject": "Meet"}]},
                ),
            ),
        })
        parsed = _ok_tuple(await ol.get_calendar_events(
            "2026-05-01T00:00:00Z", "2026-05-07T00:00:00Z", top=3,
        ))
        assert parsed["count"] == 1
        assert parsed["data"]["results"][0]["id"] == "evt1"

    @pytest.mark.asyncio
    async def test_search_calendar_events(self):
        ol = _build_outlook({
            "me_search_events": AsyncMock(
                return_value=_mock_graph_response(
                    data={"value": [{"id": "evt2"}]},
                ),
            ),
        })
        parsed = _ok_tuple(await ol.search_calendar_events("standup"))
        assert parsed["search"] == "standup"

    @pytest.mark.asyncio
    async def test_create_calendar_event_with_recurrence(self):
        ol = _build_outlook({
            "me_calendar_create_events": AsyncMock(
                return_value=_mock_graph_response(data={"id": "new-evt"}),
            ),
        })
        recurrence = {
            "pattern": {"type": "daily", "interval": 1},
            "range": {"type": "numbered", "startDate": "2026-05-01", "numberOfOccurrences": 5},
        }
        parsed = _ok_tuple(await ol.create_calendar_event(
            subject="Daily",
            start_datetime="2026-05-01T09:00:00",
            end_datetime="2026-05-01T09:30:00",
            attendees=["guest@x.com"],
            recurrence=recurrence,
            is_online_meeting=True,
        ))
        assert parsed["id"] == "new-evt"
        body = ol.client.me_calendar_create_events.await_args.kwargs["request_body"]
        assert body["isOnlineMeeting"] is True
        assert "recurrence" in body

    @pytest.mark.asyncio
    async def test_get_calendar_event(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={"id": "evt1"}),
            ),
        })
        parsed = _ok_tuple(await ol.get_calendar_event("evt1"))
        assert parsed["id"] == "evt1"

    @pytest.mark.asyncio
    async def test_update_calendar_event_normalizes_api_shapes(self):
        ol = _build_outlook({
            "me_calendar_update_events": AsyncMock(
                return_value=_mock_graph_response(data={"id": "evt1"}),
            ),
        })
        parsed = _ok_tuple(await ol.update_calendar_event(
            event_id="evt1",
            body={"content": "Updated body"},
            location={"displayName": "Room A"},
            attendees=[{"emailAddress": {"address": "a@x.com"}}],
        ))
        assert "updated" in parsed["message"].lower()
        body = ol.client.me_calendar_update_events.await_args.kwargs["request_body"]
        assert body["body"]["content"] == "Updated body"
        assert body["location"]["displayName"] == "Room A"

    @pytest.mark.asyncio
    async def test_update_calendar_event_no_fields(self):
        ol = _build_outlook()
        err = _err_tuple(await ol.update_calendar_event(event_id="evt1"))
        assert "No fields" in err["error"]

    @pytest.mark.asyncio
    async def test_delete_calendar_event(self):
        ol = _build_outlook({
            "me_calendar_delete_events": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
        })
        parsed = _ok_tuple(await ol.delete_calendar_event("evt1"))
        assert parsed["event_id"] == "evt1"


class TestRecurringEvents:
    @pytest.mark.asyncio
    async def test_get_recurring_events_groups_occurrences(self):
        ol = _build_outlook({
            "me_calendar_list_calendar_view": AsyncMock(
                return_value=_mock_graph_response(data={
                    "value": [
                        {
                            "type": "seriesMaster",
                            "id": "series-1",
                            "subject": "Standup",
                            "recurrence": {"pattern": {"type": "weekly"}},
                        },
                        {
                            "type": "occurrence",
                            "seriesMasterId": "series-1",
                            "id": "occ-1",
                            "start": {"dateTime": "2026-05-01T09:00:00Z"},
                            "end": {"dateTime": "2026-05-01T09:15:00Z"},
                        },
                    ],
                }),
            ),
        })
        parsed = _ok_tuple(await ol.get_recurring_events(top=5))
        assert parsed["count"] == 1
        # seriesMaster and its occurrence both contribute rows.
        assert parsed["total_occurrences"] == 2

    @pytest.mark.asyncio
    async def test_get_recurring_events_ending_invalid_range(self):
        ol = _build_outlook()
        err = _err_tuple(await ol.get_recurring_events_ending(
            end_before="2026-01-01T00:00:00Z",
            end_after="2026-12-31T00:00:00Z",
        ))
        assert "end_after must be before" in err["error"]

    @pytest.mark.asyncio
    async def test_get_recurring_events_ending_matches(self):
        ol = _build_outlook({
            "me_list_series_master_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "value": [{
                        "id": "series-1",
                        "subject": "Weekly",
                        "recurrence": {
                            "range": {"type": "endDate", "endDate": "2026-05-15"},
                        },
                    }],
                }),
            ),
        })
        parsed = _ok_tuple(await ol.get_recurring_events_ending(
            end_before="2026-06-01T00:00:00Z",
            end_after="2026-05-01T00:00:00Z",
        ))
        assert parsed["count"] == 1
        assert parsed["results"][0]["_recurrenceEndDate"] == "2026-05-15"

    @pytest.mark.asyncio
    async def test_delete_recurring_event_occurrence_empty_dates(self):
        ol = _build_outlook()
        err = _err_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1", occurrence_dates=[],
        ))
        assert "No occurrence dates" in err["error"]

    @pytest.mark.asyncio
    async def test_delete_recurring_event_occurrence_invalid_date(self):
        ol = _build_outlook()
        err = _err_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1", occurrence_dates=["not-a-date"],
        ))
        assert "Invalid date format" in err["error"]

    @pytest.mark.asyncio
    async def test_delete_recurring_event_occurrence_master_fetch_failure(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(success=False, error="not found"),
            ),
        })
        err = _err_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1", occurrence_dates=["2026-05-10"],
        ))
        assert "not found" in err["error"].lower()

    @pytest.mark.asyncio
    async def test_delete_recurring_event_occurrence_deletes_one(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "recurrence": {
                        "range": {
                            "type": "endDate",
                            "startDate": "2026-05-01",
                            "endDate": "2026-06-01",
                        },
                    },
                }),
            ),
            "me_list_event_occurrences": AsyncMock(
                return_value=_mock_graph_response(data={
                    "value": [{
                        "id": "occ-evt-1",
                        "subject": "Standup",
                        "start": {"dateTime": "2026-05-10T09:00:00"},
                    }],
                }),
            ),
            "me_calendar_delete_events": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
        })
        parsed = _ok_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1",
            occurrence_dates=["2026-05-10"],
        ))
        assert parsed["summary"]["deleted"] == 1
        assert parsed["deleted"][0]["date"] == "2026-05-10"

    @pytest.mark.asyncio
    async def test_get_recurring_events_fetch_failure(self):
        ol = _build_outlook({
            "me_calendar_list_calendar_view": AsyncMock(
                return_value=_mock_graph_response(success=False, error="throttled"),
            ),
        })
        err = _err_tuple(await ol.get_recurring_events())
        assert "throttled" in err["error"]


class TestMeetingTranscripts:
    @pytest.mark.asyncio
    async def test_resolve_failure(self):
        ol = _build_outlook()
        with patch.object(ol, "_resolve_to_online_meeting_id", new=AsyncMock(return_value=None)):
            err = _err_tuple(await ol.get_meeting_transcripts(join_url="https://teams/x"))
        assert "Could not resolve" in err["error"]

    @pytest.mark.asyncio
    async def test_no_transcripts_available(self):
        ol = _build_outlook({
            "me_list_online_meeting_transcripts": AsyncMock(
                return_value=_mock_graph_response(data={"value": []}),
            ),
        })
        with patch.object(ol, "_resolve_to_online_meeting_id", new=AsyncMock(return_value="meet-1")):
            parsed = _ok_tuple(await ol.get_meeting_transcripts(join_url="https://teams/x"))
        assert parsed["transcripts"] == []

    @pytest.mark.asyncio
    async def test_fetches_and_parses_transcript_metadata(self):
        transcript_obj = {"id": "t1", "createdDateTime": "2026-05-01T10:00:00Z"}
        ol = _build_outlook({
            "me_list_online_meeting_transcripts": AsyncMock(
                return_value=_mock_graph_response(data={"value": [transcript_obj]}),
            ),
            "me_get_online_meeting_transcript_metadata": AsyncMock(
                return_value=_mock_graph_response(data={
                    "content": (
                        '{"speakerName":"Alice","spokenText":"Hello"}\n'
                        'not-json-line\n'
                    ),
                }),
            ),
        })
        with patch.object(ol, "_resolve_to_online_meeting_id", new=AsyncMock(return_value="meet-1")):
            parsed = _ok_tuple(await ol.get_meeting_transcripts(join_url="https://teams/x"))
        assert parsed["transcript_count"] == 1
        assert parsed["transcripts"][0]["entry_count"] == 1
        assert parsed["transcripts"][0]["entries"][0]["speaker"] == "Alice"


class TestResolveOnlineMeetingId:
    @pytest.mark.asyncio
    async def test_join_url_path(self):
        ol = _build_outlook({
            "me_list_online_meetings": AsyncMock(
                return_value=_mock_graph_response(
                    data={"value": [{"id": "online-1"}]},
                ),
            ),
        })
        meeting_id = await ol._online_meeting_id_from_join_url(
            "https://teams.microsoft.com/l/meetup-join/abc%2Fdef",
        )
        assert meeting_id == "online-1"
        filter_arg = ol.client.me_list_online_meetings.await_args.kwargs["filter"]
        assert "joinWebUrl eq" in filter_arg

    @pytest.mark.asyncio
    async def test_event_id_path_extracts_join_url(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "onlineMeeting": {"joinUrl": "https://teams/join"},
                }),
            ),
            "me_list_online_meetings": AsyncMock(
                return_value=_mock_graph_response(
                    data={"value": [{"id": "online-2"}]},
                ),
            ),
        })
        meeting_id = await ol._resolve_to_online_meeting_id(event_id="evt-1")
        assert meeting_id == "online-2"


class TestParseMetadataJson:
    def test_parses_json_lines(self):
        meta = (
            '{"speakerName":"Bob","spokenText":"Hi"}\n'
            '{"speakerName":"Carol","spokenText":""}\n'
        )
        entries = Outlook._parse_metadata_json(meta)
        assert len(entries) == 1
        assert entries[0]["speaker"] == "Bob"
        assert entries[0]["text"] == "Hi"

    def test_invalid_json_line_skipped(self):
        entries = Outlook._parse_metadata_json('{"broken"\n')
        assert entries == []


# ===========================================================================
# Extended coverage — error branches, pagination, recurring delete paths
# ===========================================================================


class TestMailToolErrorBranches:
    @pytest.mark.asyncio
    async def test_reply_to_message_failure_and_exception(self):
        ol = _build_outlook({
            "me_messages_message_reply": AsyncMock(
                return_value=_mock_graph_response(success=False, error="denied"),
            ),
        })
        err = _err_tuple(await ol.reply_to_message("M1", "x"))
        assert "denied" in err["error"]

        ol.client.me_messages_message_reply = AsyncMock(side_effect=RuntimeError("boom"))
        err = _err_tuple(await ol.reply_to_message("M1", "x"))
        assert err["error"] == "boom"

    @pytest.mark.asyncio
    async def test_reply_all_exception(self):
        ol = _build_outlook({
            "me_messages_message_reply_all": AsyncMock(side_effect=RuntimeError("x")),
        })
        err = _err_tuple(await ol.reply_all_to_message("M1", "x"))
        assert err["error"] == "x"

    @pytest.mark.asyncio
    async def test_forward_without_comment_and_failures(self):
        ol = _build_outlook({
            "me_messages_message_forward": AsyncMock(
                return_value=_mock_graph_response(success=False, error="fwd fail"),
            ),
        })
        err = _err_tuple(await ol.forward_message("M1", ["a@x.com"]))
        assert "fwd fail" in err["error"]

        ol.client.me_messages_message_forward = AsyncMock(side_effect=RuntimeError("z"))
        err = _err_tuple(await ol.forward_message("M1", ["a@x.com"], comment="Hi"))
        assert err["error"] == "z"

    @pytest.mark.asyncio
    async def test_search_messages_failure_and_exception(self):
        ol = _build_outlook({
            "me_list_messages": AsyncMock(
                return_value=_mock_graph_response(success=False, error="bad search"),
            ),
        })
        err = _err_tuple(await ol.search_messages(search="x"))
        assert "bad search" in err["error"]

        ol.client.me_list_messages = AsyncMock(side_effect=RuntimeError("search err"))
        err = _err_tuple(await ol.search_messages())
        assert err["error"] == "search err"

    @pytest.mark.asyncio
    async def test_get_message_failure_and_exception(self):
        ol = _build_outlook({
            "me_get_message": AsyncMock(
                return_value=_mock_graph_response(success=False, error="gone"),
            ),
        })
        err = _err_tuple(await ol.get_message("M1"))
        assert "gone" in err["error"]

        ol.client.me_get_message = AsyncMock(side_effect=RuntimeError("get err"))
        err = _err_tuple(await ol.get_message("M1"))
        assert err["error"] == "get err"

    @pytest.mark.asyncio
    async def test_list_attachments_failure_and_skip_non_dict(self):
        ol = _build_outlook({
            "me_messages_list_attachments": AsyncMock(
                return_value=_mock_graph_response(success=False, error="nope"),
            ),
        })
        err = _err_tuple(await ol.list_message_attachments("M1"))
        assert "nope" in err["error"]

        ol = _build_outlook({
            "me_messages_list_attachments": AsyncMock(
                return_value=_mock_graph_response(data={"value": ["bad", {"id": "a1"}]}),
            ),
        })
        parsed = _ok_tuple(await ol.list_message_attachments("M1"))
        assert parsed["count"] == 1

        ol.client.me_messages_list_attachments = AsyncMock(side_effect=RuntimeError("lst"))
        err = _err_tuple(await ol.list_message_attachments("M1"))
        assert err["error"] == "lst"


class TestStageAttachmentExtended:
    @pytest.mark.asyncio
    async def test_fetch_attachment_failure(self):
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=False, error="404"),
            ),
        }, state={
            "org_id": "o", "config_service": MagicMock(),
            "conversation_id": "c", "document_id_to_url": {},
        })
        err = _err_tuple(await ol.stage_attachment_to_blob("M", "A"))
        assert "404" in err["error"]

    @pytest.mark.asyncio
    async def test_empty_attachment_response(self):
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=None),
            ),
        }, state={
            "org_id": "o", "config_service": MagicMock(),
            "conversation_id": "c", "document_id_to_url": {},
        })
        err = _err_tuple(await ol.stage_attachment_to_blob("M", "A"))
        assert "empty attachment" in err["error"].lower()

    @pytest.mark.asyncio
    async def test_missing_content_bytes(self):
        attachment = MagicMock()
        attachment.odata_type = "#microsoft.graph.fileAttachment"
        attachment.content_bytes = None
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=attachment),
            ),
        }, state={
            "org_id": "o", "config_service": MagicMock(),
            "conversation_id": "c", "document_id_to_url": {},
        })
        err = _err_tuple(await ol.stage_attachment_to_blob("M", "A"))
        assert "contentBytes" in err["error"]

    @pytest.mark.asyncio
    async def test_decode_failure(self):
        attachment = MagicMock()
        attachment.odata_type = "#microsoft.graph.fileAttachment"
        attachment.content_bytes = b"!!!not-base64!!!"
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=attachment),
            ),
        }, state={
            "org_id": "o", "config_service": MagicMock(),
            "conversation_id": "c", "document_id_to_url": {},
        })
        with patch(
            "app.agents.actions.microsoft.outlook.outlook."
            "_decode_graph_file_attachment_content_bytes",
            side_effect=ValueError("bad b64"),
        ):
            err = _err_tuple(await ol.stage_attachment_to_blob("M", "A"))
        assert "decode" in err["error"].lower()

    @pytest.mark.asyncio
    async def test_size_limit_exceeded(self):
        from app.agents.actions.util.blob_staging import DEFAULT_MAX_STAGE_BYTES

        attachment = MagicMock()
        attachment.odata_type = "#microsoft.graph.fileAttachment"
        attachment.name = "big.bin"
        attachment.content_type = "application/octet-stream"
        attachment.content_bytes = base64.b64encode(b"x")

        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=attachment),
            ),
        }, state={
            "org_id": "o", "config_service": MagicMock(),
            "conversation_id": "c", "document_id_to_url": {},
        })
        with patch(
            "app.agents.actions.microsoft.outlook.outlook."
            "_decode_graph_file_attachment_content_bytes",
            return_value=bytes(DEFAULT_MAX_STAGE_BYTES + 1),
        ):
            err = _err_tuple(await ol.stage_attachment_to_blob("M", "A"))
        assert err["error"] == "size_limit_exceeded"

    @pytest.mark.asyncio
    async def test_blob_upload_failure_and_missing_registry_mapping(self):
        raw = b"pdf"
        attachment = MagicMock()
        attachment.odata_type = "#microsoft.graph.fileAttachment"
        attachment.name = "f.pdf"
        attachment.content_type = "application/pdf"
        attachment.content_bytes = base64.b64encode(raw)

        blob_store = MagicMock()
        blob_store.save_conversation_file_to_storage = AsyncMock(
            side_effect=RuntimeError("upload failed"),
        )
        state = {
            "org_id": "o", "config_service": MagicMock(), "conversation_id": "c",
            "blob_store": blob_store, "document_id_to_url": {},
        }
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=attachment),
            ),
        }, state=state)
        err = _err_tuple(await ol.stage_attachment_to_blob("M", "A"))
        assert "upload failed" in err["error"]

        blob_store.save_conversation_file_to_storage = AsyncMock(
            return_value={"documentId": None, "downloadUrl": None},
        )
        err = _err_tuple(await ol.stage_attachment_to_blob("M", "A"))
        assert "documentId" in err["error"]

    @pytest.mark.asyncio
    async def test_lazy_blob_store_init_failure(self):
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=MagicMock(
                    odata_type="#microsoft.graph.fileAttachment",
                    name="f", content_type="text/plain",
                    content_bytes=base64.b64encode(b"x"),
                )),
            ),
        }, state={
            "org_id": "o", "config_service": MagicMock(),
            "conversation_id": "c", "document_id_to_url": {},
        })
        with patch(
            "app.agents.actions.microsoft.outlook.outlook.BlobStorage",
            side_effect=RuntimeError("no storage"),
        ):
            err = _err_tuple(await ol.stage_attachment_to_blob("M", "A"))
        assert "lazy construction failed" in err["error"]

    @pytest.mark.asyncio
    async def test_stage_exception_uses_handle_error(self):
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(side_effect=RuntimeError("kaboom")),
        }, state={
            "org_id": "o", "config_service": MagicMock(),
            "conversation_id": "c", "document_id_to_url": {},
        })
        err = _err_tuple(await ol.stage_attachment_to_blob("M", "A"))
        assert err["error"] == "kaboom"


class TestCalendarErrorBranches:
    @pytest.mark.asyncio
    async def test_get_mail_folders_failure(self):
        ol = _build_outlook({
            "me_list_mail_folders": AsyncMock(
                return_value=_mock_graph_response(success=False, error="folders"),
            ),
        })
        err = _err_tuple(await ol.get_mail_folders())
        assert "folders" in err["error"]

    @pytest.mark.asyncio
    async def test_calendar_tools_failures_and_exceptions(self):
        ol = _build_outlook({
            "me_calendar_list_calendar_view": AsyncMock(
                return_value=_mock_graph_response(success=False, error="cal"),
            ),
        })
        err = _err_tuple(await ol.get_calendar_events("s", "e"))
        assert "cal" in err["error"]

        ol = _build_outlook({
            "me_search_events": AsyncMock(
                return_value=_mock_graph_response(success=False, error="search"),
            ),
        })
        err = _err_tuple(await ol.search_calendar_events("meet"))
        assert "search" in err["error"]

        ol = _build_outlook({
            "me_calendar_create_events": AsyncMock(
                return_value=_mock_graph_response(success=False, error="create"),
            ),
        })
        err = _err_tuple(await ol.create_calendar_event(
            "S", "2026-05-01T10:00:00", "2026-05-01T11:00:00",
        ))
        assert "create" in err["error"]

        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(success=False, error="get"),
            ),
        })
        err = _err_tuple(await ol.get_calendar_event("e1"))
        assert "get" in err["error"]

        ol = _build_outlook({
            "me_calendar_update_events": AsyncMock(
                return_value=_mock_graph_response(success=False, error="upd"),
            ),
        })
        err = _err_tuple(await ol.update_calendar_event("e1", subject="New"))
        assert "upd" in err["error"]

        ol = _build_outlook({
            "me_calendar_delete_events": AsyncMock(
                return_value=_mock_graph_response(success=False, error="del"),
            ),
        })
        err = _err_tuple(await ol.delete_calendar_event("e1"))
        assert "del" in err["error"]

    @pytest.mark.asyncio
    async def test_calendar_exceptions(self):
        ol = _build_outlook({
            "me_calendar_list_calendar_view": AsyncMock(side_effect=RuntimeError("e1")),
        })
        assert json.loads((await ol.get_calendar_events("s", "e"))[1])["error"] == "e1"

        ol.client.me_search_events = AsyncMock(side_effect=RuntimeError("e2"))
        assert json.loads((await ol.search_calendar_events("x"))[1])["error"] == "e2"

        ol.client.me_calendar_create_events = AsyncMock(side_effect=RuntimeError("e3"))
        assert json.loads((await ol.create_calendar_event(
            "S", "2026-05-01T10:00:00", "2026-05-01T11:00:00",
        ))[1])["error"] == "e3"

        ol.client.me_calendar_get_events = AsyncMock(side_effect=RuntimeError("e4"))
        assert json.loads((await ol.get_calendar_event("e1"))[1])["error"] == "e4"

        ol.client.me_calendar_update_events = AsyncMock(side_effect=RuntimeError("e5"))
        assert json.loads((await ol.update_calendar_event("e1", subject="X"))[1])["error"] == "e5"

        ol.client.me_calendar_delete_events = AsyncMock(side_effect=RuntimeError("e6"))
        assert json.loads((await ol.delete_calendar_event("e1"))[1])["error"] == "e6"


class TestRecurringEventsExtended:
    @pytest.mark.asyncio
    async def test_get_recurring_events_pagination_and_skips(self):
        page1 = {
            "value": [
                {"type": "singleInstance", "id": "skip-me"},
                *[
                    {
                        "type": "occurrence",
                        "seriesMasterId": f"series-{i}",
                        "id": f"occ-{i}",
                        "start": {"dateTime": f"2026-05-{10+i:02d}T09:00:00Z"},
                    }
                    for i in range(50)
                ],
            ],
        }
        page2 = {"value": []}
        ol = _build_outlook({
            "me_calendar_list_calendar_view": AsyncMock(
                side_effect=[
                    _mock_graph_response(data=page1),
                    _mock_graph_response(data=page2),
                ],
            ),
        })
        parsed = _ok_tuple(await ol.get_recurring_events(top=3))
        assert parsed["count"] == 3

    @pytest.mark.asyncio
    async def test_get_recurring_events_exception(self):
        ol = _build_outlook({
            "me_calendar_list_calendar_view": AsyncMock(side_effect=RuntimeError("boom")),
        })
        err = _err_tuple(await ol.get_recurring_events())
        assert err["error"] == "boom"

    @pytest.mark.asyncio
    async def test_get_recurring_events_ending_invalid_datetime(self):
        ol = _build_outlook()
        err = _err_tuple(await ol.get_recurring_events_ending(
            end_before="not-a-datetime",
        ))
        assert "Invalid datetime format" in err["error"]

    @pytest.mark.asyncio
    async def test_get_recurring_events_ending_no_matches(self):
        ol = _build_outlook({
            "me_list_series_master_events": AsyncMock(
                return_value=_mock_graph_response(data={"value": []}),
            ),
        })
        parsed = _ok_tuple(await ol.get_recurring_events_ending(
            end_before="2026-12-31T23:59:59Z",
        ))
        assert parsed["count"] == 0
        assert "No recurring events" in parsed["message"]

    @pytest.mark.asyncio
    async def test_get_recurring_events_ending_skips_non_enddate_recurrence(self):
        ol = _build_outlook({
            "me_list_series_master_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "value": [
                        {"id": "s1", "recurrence": {"range": {"type": "noEnd"}}},
                        {
                            "id": "s2",
                            "recurrence": {
                                "range": {"type": "endDate", "endDate": "bad-date"},
                            },
                        },
                    ],
                }),
            ),
        })
        parsed = _ok_tuple(await ol.get_recurring_events_ending(
            end_before="2026-12-31T23:59:59Z",
        ))
        assert parsed["count"] == 0

    @pytest.mark.asyncio
    async def test_get_recurring_events_ending_fetch_failure_and_exception(self):
        ol = _build_outlook({
            "me_list_series_master_events": AsyncMock(
                return_value=_mock_graph_response(success=False, error="masters"),
            ),
        })
        err = _err_tuple(await ol.get_recurring_events_ending(
            end_before="2026-12-31T23:59:59Z",
        ))
        assert "masters" in err["error"]

        ol.client.me_list_series_master_events = AsyncMock(side_effect=RuntimeError("x"))
        err = _err_tuple(await ol.get_recurring_events_ending(
            end_before="2026-12-31T23:59:59Z",
        ))
        assert err["error"] == "x"


class TestDeleteRecurringExtended:
    @pytest.mark.asyncio
    async def test_all_dates_out_of_scope(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "recurrence": {
                        "range": {
                            "type": "endDate",
                            "startDate": "2026-05-01",
                            "endDate": "2026-05-31",
                        },
                    },
                }),
            ),
        })
        parsed = _ok_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1",
            occurrence_dates=["2026-01-01"],
        ))
        assert parsed["summary"]["out_of_scope"] == 1
        assert parsed["summary"]["deleted"] == 0

    @pytest.mark.asyncio
    async def test_occurrence_fetch_failure(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "recurrence": {"range": {"type": "noEnd", "startDate": "2026-05-01"}},
                }),
            ),
            "me_list_event_occurrences": AsyncMock(
                return_value=_mock_graph_response(success=False, error="occ fail"),
            ),
        })
        err = _err_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1", occurrence_dates=["2026-05-10"],
        ))
        assert "occ fail" in err["error"]

    @pytest.mark.asyncio
    async def test_end_date_optimization_trims_series_end(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "recurrence": {
                        "pattern": {"type": "weekly", "interval": 1},
                        "range": {
                            "type": "endDate",
                            "startDate": "2026-05-01",
                            "endDate": "2026-05-20",
                        },
                    },
                }),
            ),
            "me_list_event_occurrences": AsyncMock(
                return_value=_mock_graph_response(data={
                    "value": [
                        {"id": "o1", "subject": "W", "start": {"dateTime": "2026-05-10T09:00:00"}},
                        {"id": "o2", "subject": "W", "start": {"dateTime": "2026-05-17T09:00:00"}},
                        {"id": "o3", "subject": "W", "start": {"dateTime": "2026-05-20T09:00:00"}},
                    ],
                }),
            ),
            "me_calendar_update_events": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
            "me_calendar_delete_events": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
        })
        parsed = _ok_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1",
            occurrence_dates=["2026-05-20"],
        ))
        assert "end_date_update" in parsed
        assert parsed["end_date_update"]["new_end_date"] == "2026-05-17"
        assert parsed["summary"]["trimmed_via_end_date"] == 1

    @pytest.mark.asyncio
    async def test_not_found_and_delete_error_paths(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "recurrence": {"range": {"type": "noEnd", "startDate": "2026-05-01"}},
                }),
            ),
            "me_list_event_occurrences": AsyncMock(
                return_value=_mock_graph_response(data={
                    "value": [
                        {"id": "o1", "start": {"dateTime": "2026-05-10T09:00:00"}},
                    ],
                }),
            ),
            "me_calendar_delete_events": AsyncMock(
                return_value=_mock_graph_response(success=False, error="del denied"),
            ),
        })
        parsed = _ok_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1",
            occurrence_dates=["2026-05-10", "2026-05-11"],
        ))
        assert parsed["not_found"] == ["2026-05-11"]
        assert parsed["errors"][0]["error"] == "del denied"

    @pytest.mark.asyncio
    async def test_delete_recurring_exception(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(side_effect=RuntimeError("master err")),
        })
        err = _err_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1", occurrence_dates=["2026-05-10"],
        ))
        assert err["error"] == "master err"


class TestMeetingTranscriptsExtended:
    @pytest.mark.asyncio
    async def test_list_transcripts_failure(self):
        ol = _build_outlook({
            "me_list_online_meeting_transcripts": AsyncMock(
                return_value=_mock_graph_response(success=False, error="list fail"),
            ),
        })
        with patch.object(ol, "_resolve_to_online_meeting_id", new=AsyncMock(return_value="m1")):
            err = _err_tuple(await ol.get_meeting_transcripts(join_url="https://teams/x"))
        assert "list fail" in err["error"]

    @pytest.mark.asyncio
    async def test_skips_transcript_without_id(self):
        ol = _build_outlook({
            "me_list_online_meeting_transcripts": AsyncMock(
                return_value=_mock_graph_response(data={"value": [{"no": "id"}]}),
            ),
        })
        with patch.object(ol, "_resolve_to_online_meeting_id", new=AsyncMock(return_value="m1")):
            parsed = _ok_tuple(await ol.get_meeting_transcripts(join_url="https://teams/x"))
        assert parsed["transcript_count"] == 0

    @pytest.mark.asyncio
    async def test_transcript_object_style_and_metadata_failure(self):
        transcript = MagicMock()
        transcript.id = "t-obj"
        transcript.created_date_time = "2026-05-01T10:00:00Z"

        ol = _build_outlook({
            "me_list_online_meeting_transcripts": AsyncMock(
                return_value=_mock_graph_response(data={"value": [transcript]}),
            ),
            "me_get_online_meeting_transcript_metadata": AsyncMock(
                return_value=_mock_graph_response(success=False, error="meta fail"),
            ),
        })
        with patch.object(ol, "_resolve_to_online_meeting_id", new=AsyncMock(return_value="m1")):
            parsed = _ok_tuple(await ol.get_meeting_transcripts(join_url="https://teams/x"))
        assert parsed["transcripts"][0]["entry_count"] == 0

    @pytest.mark.asyncio
    async def test_get_meeting_transcripts_exception(self):
        ol = _build_outlook()
        with patch.object(
            ol,
            "_resolve_to_online_meeting_id",
            new=AsyncMock(side_effect=RuntimeError("resolve err")),
        ):
            err = _err_tuple(await ol.get_meeting_transcripts(event_id="evt"))
        assert err["error"] == "resolve err"


class TestResolveOnlineMeetingExtended:
    @pytest.mark.asyncio
    async def test_join_url_list_failure(self):
        ol = _build_outlook({
            "me_list_online_meetings": AsyncMock(
                return_value=_mock_graph_response(success=False),
            ),
        })
        assert await ol._online_meeting_id_from_join_url("https://teams/join") is None

    @pytest.mark.asyncio
    async def test_join_url_empty_results(self):
        ol = _build_outlook({
            "me_list_online_meetings": AsyncMock(
                return_value=_mock_graph_response(data={"value": []}),
            ),
        })
        assert await ol._online_meeting_id_from_join_url("https://teams/join") is None

    @pytest.mark.asyncio
    async def test_join_url_object_with_id_attr(self):
        meeting = MagicMock()
        meeting.id = "from-attr"
        ol = _build_outlook({
            "me_list_online_meetings": AsyncMock(
                return_value=_mock_graph_response(data={"value": [meeting]}),
            ),
        })
        assert await ol._online_meeting_id_from_join_url("https://teams/join") == "from-attr"

    @pytest.mark.asyncio
    async def test_resolve_event_paths_return_none(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(success=False),
            ),
        })
        assert await ol._resolve_to_online_meeting_id(event_id="e1") is None

        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data="not a dict"),
            ),
        })
        assert await ol._resolve_to_online_meeting_id(event_id="e2") is None

        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={"onlineMeeting": None}),
            ),
        })
        assert await ol._resolve_to_online_meeting_id(event_id="e3") is None

        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "onlineMeeting": {"joinUrl": 123},
                }),
            ),
        })
        assert await ol._resolve_to_online_meeting_id(event_id="e4") is None

        assert await ol._resolve_to_online_meeting_id() is None

    @pytest.mark.asyncio
    async def test_resolve_exceptions_swallowed(self):
        ol = _build_outlook({
            "me_list_online_meetings": AsyncMock(side_effect=RuntimeError("boom")),
        })
        assert await ol._online_meeting_id_from_join_url("https://teams/join") is None
        assert await ol._resolve_to_online_meeting_id(join_url="https://teams/join") is None


class TestRemainingLineCoverage:
    """Targeted tests for the last uncovered branches toward 97%+."""

    def test_serialize_graph_writer_empty_content_uses_backing_store(self):
        class _FakeWriter:
            def write_object_value(self, *_a, **_k):
                return None

            def get_serialized_content(self):
                return b""

        class _BackingStore:
            def enumerate_(self):
                yield "_skip", "hidden"
                yield "id", "v1"

        class _Kiota:
            def get_field_deserializers(self):
                return {}

            def __init__(self):
                self.backing_store = _BackingStore()

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            return_value=_FakeWriter(),
        ):
            assert _serialize_graph_obj(_Kiota()) == {"id": "v1"}

    def test_serialize_graph_generic_loop_exceptions(self):
        class Bad:
            pass

        class Thing:
            def __init__(self):
                self.bad = Bad()
                self.additional_data = {"also_bad": Bad()}

        real = outlook_module._serialize_graph_obj

        def selective(obj):
            if isinstance(obj, Bad):
                raise RuntimeError("nested fail")
            return real(obj)

        with patch.object(outlook_module, "_serialize_graph_obj", selective):
            result = real(Thing())
        assert isinstance(result["bad"], str)
        assert isinstance(result["also_bad"], str)

    def test_serialize_graph_additional_data_skips_existing_keys(self):
        class Thing:
            def __init__(self):
                self.id = "from_vars"
                self.additional_data = {"id": "skip", "extra": "yes"}

        result = _serialize_graph_obj(Thing())
        assert result["id"] == "from_vars"
        assert result["extra"] == "yes"

    def test_serialize_graph_backing_store_nested_serialize_failure(self):
        class Bad:
            pass

        class _BackingStore:
            def enumerate_(self):
                yield "bad", Bad()

        class _Kiota:
            def get_field_deserializers(self):
                return {}

            def __init__(self):
                self.backing_store = _BackingStore()

        real = outlook_module._serialize_graph_obj

        def selective(obj):
            if isinstance(obj, Bad):
                raise RuntimeError("nested")
            return real(obj)

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            side_effect=RuntimeError("no"),
        ):
            with patch.object(outlook_module, "_serialize_graph_obj", selective):
                result = real(_Kiota())
        assert isinstance(result["bad"], str)

    def test_serialize_graph_backing_store_additional_skips_duplicates(self):
        class _BackingStore:
            def enumerate_(self):
                yield "id", "v"

        class _Kiota:
            def get_field_deserializers(self):
                return {}

            def __init__(self):
                self.backing_store = _BackingStore()
                self.additional_data = {"id": "duplicate", "extra": "new"}

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            side_effect=RuntimeError("no"),
        ):
            result = _serialize_graph_obj(_Kiota())
        assert result["id"] == "v"
        assert result["extra"] == "new"

    def test_outlook_serialize_response_float_and_bool(self):
        assert Outlook._serialize_response(3.14) == 3.14
        assert Outlook._serialize_response(False) is False

    def test_outlook_serialize_kiota_writer_empty_dict_falls_through(self):
        class _FakeWriter:
            def write_object_value(self, *_a, **_k):
                return None

            def get_serialized_content(self):
                return b"{}"

        class _Kiota:
            def get_field_deserializers(self):
                return {}

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            return_value=_FakeWriter(),
        ):
            result = Outlook._serialize_response(_Kiota())
        assert isinstance(result, str)

    def test_outlook_serialize_backing_store_inner_failures(self):
        class _BackingStore:
            def enumerate_(self):
                yield "bad", object()

        class _Kiota:
            def get_field_deserializers(self):
                return {}

            def __init__(self):
                self.backing_store = _BackingStore()
                self.additional_data = {"x": object()}

        with patch(
            "app.agents.actions.microsoft.outlook.outlook.JsonSerializationWriter",
            side_effect=RuntimeError("no"),
        ):
            result = Outlook._serialize_response(_Kiota())
        assert isinstance(result["bad"], str)
        assert isinstance(result["x"], str)

    def test_handle_error_client_attribute_error(self):
        ol = _build_outlook()
        ok, body = ol._handle_error(
            AttributeError("missing client attribute"),
            "op",
        )
        assert "not authenticated" in json.loads(body)["error"].lower()

    @pytest.mark.asyncio
    async def test_send_email_cc_and_bcc_in_recipients_summary(self):
        ol = _build_outlook({
            "me_create_messages": AsyncMock(
                return_value=_mock_graph_response(data={"id": "MSG1"}),
            ),
            "me_messages_message_send": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
        })
        parsed = _ok_tuple(await ol.send_email(
            to_recipients=["a@x.com"],
            subject="S",
            body="B",
            cc_recipients=["cc@x.com"],
            bcc_recipients=["bcc@x.com"],
        ))
        assert parsed["recipients"]["cc"] == ["cc@x.com"]
        assert parsed["recipients"]["bcc"] == ["bcc@x.com"]

    @pytest.mark.asyncio
    async def test_send_email_bcc_only_in_recipients_summary(self):
        ol = _build_outlook({
            "me_create_messages": AsyncMock(
                return_value=_mock_graph_response(data={"id": "MSG1"}),
            ),
            "me_messages_message_send": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
        })
        parsed = _ok_tuple(await ol.send_email(
            to_recipients=["a@x.com"],
            subject="S",
            body="B",
            bcc_recipients=["bcc@x.com"],
        ))
        assert "bcc" in parsed["recipients"]
        assert "cc" not in parsed["recipients"]

    @pytest.mark.asyncio
    async def test_reply_all_default_error_message(self):
        ol = _build_outlook({
            "me_messages_message_reply_all": AsyncMock(
                return_value=_mock_graph_response(success=False, error=None),
            ),
        })
        err = _err_tuple(await ol.reply_all_to_message("M1", "c"))
        assert err["error"] == "Failed to send reply-all"

    @pytest.mark.asyncio
    async def test_stage_reinitializes_non_dict_registry(self):
        raw = b"x"
        attachment = MagicMock(
            odata_type="#microsoft.graph.fileAttachment",
            name="f.txt",
            content_type="text/plain",
            content_bytes=base64.b64encode(raw),
        )
        state = {
            "org_id": "o",
            "config_service": MagicMock(),
            "conversation_id": "c",
            "document_id_to_url": [],  # not a dict — should be replaced
            "blob_store": MagicMock(
                save_conversation_file_to_storage=AsyncMock(
                    return_value={"documentId": "d1", "downloadUrl": "https://b/d1"},
                ),
            ),
        }
        ol = _build_outlook({
            "me_messages_get_attachments": AsyncMock(
                return_value=_mock_graph_response(success=True, data=attachment),
            ),
        }, state=state)
        parsed = _ok_tuple(await ol.stage_attachment_to_blob("M", "A"))
        assert isinstance(state["document_id_to_url"], dict)
        assert parsed["document_id"] == "d1"

    @pytest.mark.asyncio
    async def test_get_mail_folders_exception(self):
        ol = _build_outlook({
            "me_list_mail_folders": AsyncMock(side_effect=RuntimeError("folders err")),
        })
        err = _err_tuple(await ol.get_mail_folders())
        assert err["error"] == "folders err"

    @pytest.mark.asyncio
    async def test_create_calendar_with_body_and_location(self):
        ol = _build_outlook({
            "me_calendar_create_events": AsyncMock(
                return_value=_mock_graph_response(data={"id": "evt"}),
            ),
        })
        await ol.create_calendar_event(
            subject="Lunch",
            start_datetime="2026-05-01T12:00:00",
            end_datetime="2026-05-01T13:00:00",
            body="Bring notes",
            location="Cafe",
        )
        body = ol.client.me_calendar_create_events.await_args.kwargs["request_body"]
        assert body["body"]["content"] == "Bring notes"
        assert body["location"]["displayName"] == "Cafe"

    @pytest.mark.asyncio
    async def test_update_calendar_all_normalization_branches(self):
        ol = _build_outlook({
            "me_calendar_update_events": AsyncMock(
                return_value=_mock_graph_response(data={"id": "evt"}),
            ),
        })
        await ol.update_calendar_event(
            event_id="evt",
            body=123,
            location=456,
            attendees=[
                "  ",
                "plain@x.com",
                {"emailAddress": {"address": "dict@x.com"}},
            ],
            start_datetime="2026-05-01T13:00:00",
            end_datetime="2026-05-01T14:00:00",
            is_online_meeting=True,
            recurrence={
                "pattern": {"type": "daily", "interval": 1},
                "range": {"type": "noEnd", "startDate": "2026-05-01"},
            },
        )
        req = ol.client.me_calendar_update_events.await_args.kwargs["request_body"]
        assert req["body"]["content"] == "123"
        assert req["location"]["displayName"] == "456"
        assert req["isOnlineMeeting"] is True
        assert "recurrence" in req

    @pytest.mark.asyncio
    async def test_get_recurring_events_skips_and_breaks_on_top(self):
        events = [{"type": "notRecurring", "id": "x"}, "not-a-dict"]
        events.append({"type": "occurrence", "id": "orphan"})  # no seriesMasterId
        for i in range(4):
            events.append({
                "type": "occurrence",
                "seriesMasterId": f"s{i}",
                "id": f"o{i}",
                "start": {"dateTime": f"2026-05-{10+i:02d}T09:00:00Z"},
            })
        ol = _build_outlook({
            "me_calendar_list_calendar_view": AsyncMock(
                return_value=_mock_graph_response(data={"value": events}),
            ),
        })
        parsed = _ok_tuple(await ol.get_recurring_events(top=2))
        assert parsed["count"] == 2

    @pytest.mark.asyncio
    async def test_get_recurring_events_second_page_when_top_not_met(self):
        page1 = [{
            "type": "occurrence",
            "seriesMasterId": "s1",
            "id": "o1",
            "start": {"dateTime": "2026-05-10T09:00:00Z"},
        }] * 50
        page2 = [{
            "type": "occurrence",
            "seriesMasterId": "s2",
            "id": "o2",
            "start": {"dateTime": "2026-05-11T09:00:00Z"},
        }]
        ol = _build_outlook({
            "me_calendar_list_calendar_view": AsyncMock(
                side_effect=[
                    _mock_graph_response(data={"value": page1}),
                    _mock_graph_response(data={"value": page2}),
                ],
            ),
        })
        parsed = _ok_tuple(await ol.get_recurring_events(top=10))
        assert parsed["count"] == 2
        assert ol.client.me_calendar_list_calendar_view.await_count == 2

    @pytest.mark.asyncio
    async def test_get_recurring_events_ending_skips_non_dict_and_paginates(self):
        page1 = [
            {
                "id": f"s{i}",
                "recurrence": {"range": {"type": "endDate", "endDate": "2026-05-15"}},
            }
            for i in range(40)
        ]
        page1.extend(
            {
                "id": f"old{i}",
                "recurrence": {"range": {"type": "endDate", "endDate": "2020-01-01"}},
            }
            for i in range(10)
        )
        page1.append("not-a-dict")
        page2 = [{
            "id": "s99",
            "recurrence": {"range": {"type": "endDate", "endDate": "2026-05-20"}},
        }]
        ol = _build_outlook({
            "me_list_series_master_events": AsyncMock(
                side_effect=[
                    _mock_graph_response(data={"value": page1}),
                    _mock_graph_response(data={"value": page2}),
                ],
            ),
        })
        parsed = _ok_tuple(await ol.get_recurring_events_ending(
            end_before="2026-12-31T23:59:59Z",
            end_after="2026-05-01T00:00:00Z",
            top=50,
        ))
        assert parsed["count"] == 41
        assert ol.client.me_list_series_master_events.await_count == 2

    @pytest.mark.asyncio
    async def test_get_recurring_events_ending_pagination(self):
        page = []
        for i in range(55):
            page.append({
                "id": f"s{i}",
                "recurrence": {
                    "range": {"type": "endDate", "endDate": f"2026-05-{(i % 28) + 1:02d}"},
                },
            })
        ol = _build_outlook({
            "me_list_series_master_events": AsyncMock(
                side_effect=[
                    _mock_graph_response(data={"value": page[:50]}),
                    _mock_graph_response(data={"value": page[50:]}),
                ],
            ),
        })
        parsed = _ok_tuple(await ol.get_recurring_events_ending(
            end_before="2026-06-30T23:59:59Z",
            end_after="2026-05-01T00:00:00Z",
            top=5,
        ))
        assert parsed["count"] == 5

    @pytest.mark.asyncio
    async def test_delete_parses_series_start_from_master(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "recurrence": {
                        "range": {
                            "type": "endDate",
                            "startDate": "2026-05-01",
                            "endDate": "2026-05-31",
                        },
                    },
                }),
            ),
            "me_list_event_occurrences": AsyncMock(
                return_value=_mock_graph_response(data={"value": []}),
            ),
        })
        parsed = _ok_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1", occurrence_dates=["2026-05-10"],
        ))
        assert parsed["not_found"] == ["2026-05-10"]

    @pytest.mark.asyncio
    async def test_delete_skips_occurrence_without_start(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(
                return_value=_mock_graph_response(data={
                    "recurrence": {"range": {"type": "noEnd", "startDate": "2026-05-01"}},
                }),
            ),
            "me_list_event_occurrences": AsyncMock(
                return_value=_mock_graph_response(data={
                    "value": [
                        {"id": "bad"},
                        {"id": "good", "start": {"dateTime": "2026-05-10T09:00:00"}},
                    ],
                }),
            ),
            "me_calendar_delete_events": AsyncMock(
                return_value=_mock_graph_response(success=True),
            ),
        })
        parsed = _ok_tuple(await ol.delete_recurring_event_occurrence(
            event_id="series-1", occurrence_dates=["2026-05-10"],
        ))
        assert parsed["summary"]["deleted"] == 1

    @pytest.mark.asyncio
    async def test_transcript_metadata_empty_content(self):
        ol = _build_outlook({
            "me_list_online_meeting_transcripts": AsyncMock(
                return_value=_mock_graph_response(data={"value": [{"id": "t1"}]}),
            ),
            "me_get_online_meeting_transcript_metadata": AsyncMock(
                return_value=_mock_graph_response(data={"content": ""}),
            ),
        })
        with patch.object(ol, "_resolve_to_online_meeting_id", new=AsyncMock(return_value="m1")):
            parsed = _ok_tuple(await ol.get_meeting_transcripts(join_url="https://teams/x"))
        assert parsed["transcripts"][0]["entry_count"] == 0

    @pytest.mark.asyncio
    async def test_resolve_event_exception_in_outer_try(self):
        ol = _build_outlook({
            "me_calendar_get_events": AsyncMock(side_effect=RuntimeError("evt err")),
        })
        assert await ol._resolve_to_online_meeting_id(event_id="e1") is None
