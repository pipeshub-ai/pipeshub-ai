import asyncio
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.zoom.zoom import ZoomResponse
from app.agents.actions.zoom.zoom import (
    Zoom,
    _coerce_meeting_id,
    _STRIP_FIELDS,
    GetMyProfileInput,
    ListMeetingsInput,
    GetMeetingInput,
    RecurrenceInput,
    CreateMeetingInput,
    UpdateMeetingInput,
    DeleteMeetingInput,
    ListUpcomingMeetingsInput,
    GetMeetingInvitationInput,
    GetMeetingTranscriptInput,
    ListContactsInput,
    GetContactInput,
    ListFolderChildrenInput,
    ListRecurringMeetingsEndingInput,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_client.return_value = MagicMock()
    return client


@pytest.fixture
def zoom(mock_client):
    z = Zoom.__new__(Zoom)
    z.client = MagicMock()
    z.client.http = MagicMock()
    return z


def _zoom_resp(*, success=True, data=None, error=None, message=None):
    return ZoomResponse(success=success, data=data, error=error, message=message)


# ---------------------------------------------------------------------------
# Pydantic schema tests
# ---------------------------------------------------------------------------


class TestCoerceMeetingId:
    def test_str_passthrough(self):
        assert _coerce_meeting_id("12345") == "12345"

    def test_int_coerced(self):
        assert _coerce_meeting_id(12345) == "12345"


class TestRecurrenceInput:
    def test_alias_type(self):
        r = RecurrenceInput(type=1)
        assert r.type_ == 1

    def test_field_name(self):
        r = RecurrenceInput(type_=2)
        assert r.type_ == 2

    def test_dump_by_alias(self):
        r = RecurrenceInput(type_=3, repeat_interval=2)
        dumped = r.model_dump(by_alias=True, exclude_none=True)
        assert dumped["type"] == 3
        assert dumped["repeat_interval"] == 2


class TestCreateMeetingInputValidator:
    def test_empty_recurrence_coerced_to_none(self):
        m = CreateMeetingInput(user_id="me", topic="Test", recurrence={})
        assert m.recurrence is None

    def test_non_empty_recurrence_preserved(self):
        m = CreateMeetingInput(user_id="me", topic="Test", recurrence={"type": 1})
        assert m.recurrence is not None
        assert m.recurrence.type_ == 1

    def test_none_recurrence_preserved(self):
        m = CreateMeetingInput(user_id="me", topic="Test", recurrence=None)
        assert m.recurrence is None


class TestUpdateMeetingInputValidator:
    def test_empty_recurrence_coerced_to_none(self):
        m = UpdateMeetingInput(meeting_id="123", recurrence={})
        assert m.recurrence is None

    def test_non_empty_recurrence_preserved(self):
        m = UpdateMeetingInput(meeting_id="123", recurrence={"type": 2})
        assert m.recurrence is not None

    def test_meeting_id_int_coercion(self):
        m = UpdateMeetingInput(meeting_id=99999)
        assert m.meeting_id == "99999"


class TestGetMeetingInput:
    def test_meeting_id_int_coercion(self):
        m = GetMeetingInput(meeting_id=111)
        assert m.meeting_id == "111"


class TestListRecurringMeetingsEndingInput:
    def test_basic(self):
        m = ListRecurringMeetingsEndingInput(from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z")
        assert m.from_ == "2026-03-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Helper method tests
# ---------------------------------------------------------------------------


class TestCleanResponseData:
    def test_dict_with_settings(self, zoom):
        data = {
            "id": 1,
            "global_dial_in_numbers": [1, 2],
            "settings": {
                "host_video": True,
                "global_dial_in_numbers": [3],
                "dial_in_numbers": [4],
            },
        }
        result = zoom._clean_response_data(data)
        assert "global_dial_in_numbers" not in result
        assert "global_dial_in_numbers" not in result["settings"]
        assert "dial_in_numbers" not in result["settings"]
        assert result["settings"]["host_video"] is True

    def test_dict_with_meetings_list(self, zoom):
        data = {
            "meetings": [
                {"id": 1, "global_dial_in_countries": ["US"]},
                {"id": 2, "topic": "ok"},
            ]
        }
        result = zoom._clean_response_data(data)
        assert "global_dial_in_countries" not in result["meetings"][0]
        assert result["meetings"][1]["topic"] == "ok"

    def test_non_dict_passthrough(self, zoom):
        assert zoom._clean_response_data("hello") == "hello"
        assert zoom._clean_response_data(123) == 123
        assert zoom._clean_response_data(None) is None

    def test_dict_without_settings(self, zoom):
        data = {"id": 1, "topic": "Test"}
        result = zoom._clean_response_data(data)
        assert result == {"id": 1, "topic": "Test"}


class TestHandleResponse:
    def test_zoom_response_success(self, zoom):
        resp = _zoom_resp(success=True, data={"id": 1})
        ok, body = zoom._handle_response(resp, "ok message")
        assert ok is True
        parsed = json.loads(body)
        assert parsed["message"] == "ok message"
        assert parsed["data"]["id"] == 1

    def test_zoom_response_failure_error(self, zoom):
        resp = _zoom_resp(success=False, error="auth failed")
        ok, body = zoom._handle_response(resp, "msg")
        assert ok is False
        assert json.loads(body)["error"] == "auth failed"

    def test_zoom_response_failure_message(self, zoom):
        resp = _zoom_resp(success=False, message="rate limited")
        ok, body = zoom._handle_response(resp, "msg")
        assert ok is False
        assert json.loads(body)["error"] == "rate limited"

    def test_zoom_response_failure_unknown(self, zoom):
        resp = _zoom_resp(success=False)
        ok, body = zoom._handle_response(resp, "msg")
        assert ok is False
        assert json.loads(body)["error"] == "Unknown error"

    def test_raw_dict_success(self, zoom):
        resp = {"meetings": []}
        ok, body = zoom._handle_response(resp, "Listed")
        assert ok is True
        assert json.loads(body)["message"] == "Listed"

    def test_raw_dict_with_error(self, zoom):
        resp = {"error": "not found", "code": 404}
        ok, body = zoom._handle_response(resp, "msg")
        assert ok is False
        parsed = json.loads(body)
        assert parsed["error"] == "not found"

    def test_raw_dict_with_code_only(self, zoom):
        resp = {"code": 400, "message": "bad request"}
        ok, body = zoom._handle_response(resp, "msg")
        assert ok is False


class TestGetMeetingDetailDict:
    @pytest.mark.asyncio
    async def test_success(self, zoom):
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "123", "topic": "Test"}
        ))
        result = await zoom._get_meeting_detail_dict("123")
        assert result == {"id": "123", "topic": "Test"}

    @pytest.mark.asyncio
    async def test_failure(self, zoom):
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(success=False, error="err"))
        result = await zoom._get_meeting_detail_dict("123")
        assert result is None

    @pytest.mark.asyncio
    async def test_non_dict_response(self, zoom):
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data=["not a dict"]
        ))
        result = await zoom._get_meeting_detail_dict("123")
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_data_key_returns_detail(self, zoom):
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={"topic": "no data key"}
        ))
        result = await zoom._get_meeting_detail_dict("123")
        assert result == {"topic": "no data key"}


class TestMeetingDetailsById:
    @pytest.mark.asyncio
    async def test_empty_list(self, zoom):
        result = await zoom._meeting_details_by_id([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_duplicates_removed(self, zoom):
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "1", "topic": "T"}
        ))
        result = await zoom._meeting_details_by_id(["1", "1", "1"])
        assert zoom.client.meeting.call_count == 1
        assert "1" in result

    @pytest.mark.asyncio
    async def test_exceptions_in_gather(self, zoom):
        async def side_effect(meetingId, **kwargs):
            if meetingId == "2":
                raise RuntimeError("boom")
            return _zoom_resp(success=True, data={"id": meetingId})

        zoom.client.meeting = AsyncMock(side_effect=side_effect)
        result = await zoom._meeting_details_by_id(["1", "2", "3"])
        assert "1" in result
        assert "2" not in result
        assert "3" in result

    @pytest.mark.asyncio
    async def test_chunking_more_than_10(self, zoom):
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "x", "topic": "T"}
        ))
        ids = [str(i) for i in range(15)]
        result = await zoom._meeting_details_by_id(ids)
        assert zoom.client.meeting.call_count == 15


class TestFetchText:
    @pytest.mark.asyncio
    async def test_success_with_text_method(self, zoom):
        mock_response = MagicMock()
        mock_response.text.return_value = "WEBVTT content"
        zoom.client.http.execute = AsyncMock(return_value=mock_response)
        result = await zoom._fetch_text("http://example.com/vtt")
        assert result == "WEBVTT content"

    @pytest.mark.asyncio
    async def test_success_without_text_method(self, zoom):
        mock_response = "plain string response"
        zoom.client.http.execute = AsyncMock(return_value=mock_response)
        result = await zoom._fetch_text("http://example.com/vtt")
        assert result == "plain string response"

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.http.execute = AsyncMock(side_effect=RuntimeError("network"))
        result = await zoom._fetch_text("http://example.com/vtt")
        assert result is None


class TestParseVtt:
    def test_full_parse(self):
        vtt = (
            "WEBVTT\n"
            "\n"
            "NOTE This is a note\n"
            "\n"
            "1\n"
            "00:00:01.000 --> 00:00:03.000\n"
            "Hello world\n"
            "\n"
            "2\n"
            "00:00:04.000 --> 00:00:06.000\n"
            "Second line\n"
            "continuation\n"
            "\n"
        )
        result = Zoom._parse_vtt(vtt)
        assert "[00:00:01.000 - 00:00:03.000] Hello world" in result
        assert "[00:00:04.000 - 00:00:06.000] Second line continuation" in result

    def test_cue_at_end_without_trailing_blank(self):
        vtt = (
            "WEBVTT\n"
            "\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "Final cue"
        )
        result = Zoom._parse_vtt(vtt)
        assert "[00:00:01.000 - 00:00:02.000] Final cue" in result

    def test_empty_string(self):
        assert Zoom._parse_vtt("") == ""

    def test_numeric_only_lines_skipped(self):
        vtt = (
            "WEBVTT\n"
            "\n"
            "123\n"
            "00:00:00.000 --> 00:00:01.000\n"
            "text\n"
            "\n"
        )
        result = Zoom._parse_vtt(vtt)
        assert "123" not in result
        assert "text" in result


class TestEnsureAware:
    def test_naive_datetime(self, zoom):
        dt = datetime(2026, 1, 1, 12, 0, 0)
        result = zoom._ensure_aware(dt)
        assert result.tzinfo == timezone.utc

    def test_aware_datetime(self, zoom):
        tz = timezone(timedelta(hours=5, minutes=30))
        dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=tz)
        result = zoom._ensure_aware(dt)
        assert result.tzinfo == tz


class TestInRange:
    def test_in_range(self, zoom):
        from_dt = datetime(2026, 3, 1, tzinfo=timezone.utc)
        to_dt = datetime(2026, 3, 31, tzinfo=timezone.utc)
        assert zoom._in_range("2026-03-15T10:00:00Z", from_dt, to_dt) is True

    def test_before_range(self, zoom):
        from_dt = datetime(2026, 3, 1, tzinfo=timezone.utc)
        to_dt = datetime(2026, 3, 31, tzinfo=timezone.utc)
        assert zoom._in_range("2026-02-28T10:00:00Z", from_dt, to_dt) is False

    def test_after_range(self, zoom):
        from_dt = datetime(2026, 3, 1, tzinfo=timezone.utc)
        to_dt = datetime(2026, 3, 31, tzinfo=timezone.utc)
        assert zoom._in_range("2026-04-01T10:00:00Z", from_dt, to_dt) is False

    def test_invalid_date_string(self, zoom):
        from_dt = datetime(2026, 3, 1, tzinfo=timezone.utc)
        to_dt = datetime(2026, 3, 31, tzinfo=timezone.utc)
        assert zoom._in_range("not-a-date", from_dt, to_dt) is False


# ---------------------------------------------------------------------------
# Tool method tests
# ---------------------------------------------------------------------------


class TestGetMyProfile:
    @pytest.mark.asyncio
    async def test_success(self, zoom):
        zoom.client.user = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "me", "email": "u@ex.com"}
        ))
        ok, body = await zoom.get_my_profile(user_id="me")
        assert ok is True
        assert "u@ex.com" in body

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.user = AsyncMock(side_effect=RuntimeError("fail"))
        ok, body = await zoom.get_my_profile()
        assert ok is False
        assert "fail" in body


class TestListMeetings:
    @pytest.mark.asyncio
    async def test_range_entirely_in_past(self, zoom):
        past = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        past2 = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")

        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 1, "type": 2, "topic": "Past", "start_time": f"{past}T10:00:00Z", "duration": 30}
            ]}
        ))
        ok, body = await zoom.list_meetings(from_=past, to_=past2)
        assert ok is True
        zoom.client.meetings.assert_called_once()
        call_kwargs = zoom.client.meetings.call_args[1]
        assert call_kwargs["type_"] == "previous_meetings"

    @pytest.mark.asyncio
    async def test_range_entirely_in_future(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
        future2 = (datetime.now(timezone.utc) + timedelta(days=10)).strftime("%Y-%m-%d")

        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 1, "type": 2, "topic": "Future", "start_time": f"{future}T10:00:00Z", "duration": 60}
            ]}
        ))
        ok, body = await zoom.list_meetings(from_=future, to_=future2)
        assert ok is True
        call_kwargs = zoom.client.meetings.call_args[1]
        assert call_kwargs["type_"] == "upcoming"

    @pytest.mark.asyncio
    async def test_range_spanning_now(self, zoom):
        past = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        future = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")

        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": []}
        ))
        ok, body = await zoom.list_meetings(from_=past, to_=future)
        assert ok is True
        assert zoom.client.meetings.call_count == 2

    @pytest.mark.asyncio
    async def test_pagination(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        future_start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

        page1 = _zoom_resp(success=True, data={
            "meetings": [{"id": 1, "type": 2, "topic": "A", "start_time": future_start}],
            "next_page_token": "token123",
        })
        page2 = _zoom_resp(success=True, data={
            "meetings": [{"id": 2, "type": 2, "topic": "B", "start_time": future_start}],
        })
        zoom.client.meetings = AsyncMock(side_effect=[page1, page2])
        ok, body = await zoom.list_meetings(from_=future, to_=future)
        assert ok is True
        assert zoom.client.meetings.call_count == 2

    @pytest.mark.asyncio
    async def test_deduplication(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 1, "type": 2, "topic": "Dup", "start_time": start, "duration": 30},
                {"id": 1, "type": 2, "topic": "Dup", "start_time": start, "duration": 30},
            ]}
        ))
        ok, body = await zoom.list_meetings(from_="2026-01-01", to_=future)
        assert ok is True
        parsed = json.loads(body)
        assert parsed["count"] <= 1

    @pytest.mark.asyncio
    async def test_search_filter(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 1, "type": 2, "topic": "Standup", "start_time": start},
                {"id": 2, "type": 2, "topic": "Planning", "start_time": start},
            ]}
        ))
        ok, body = await zoom.list_meetings(from_="2026-01-01", to_=future, search="standup")
        assert ok is True
        parsed = json.loads(body)
        assert parsed["search"] == "standup"
        assert all("standup" in m["topic"].lower() for m in parsed["meetings"])

    @pytest.mark.asyncio
    async def test_non_recurring_out_of_range(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 1, "type": 2, "topic": "Old", "start_time": "2020-01-01T10:00:00Z"},
            ]}
        ))
        ok, body = await zoom.list_meetings(from_="2026-03-01", to_=future)
        assert ok is True
        assert json.loads(body)["count"] == 0

    @pytest.mark.asyncio
    async def test_recurring_with_expired_end_date(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {
                    "id": 1, "type": 8, "topic": "Expired Recurring",
                    "recurrence": {"end_date_time": "2020-01-01T00:00:00Z"},
                },
            ]}
        ))
        ok, body = await zoom.list_meetings(from_="2026-03-01", to_=future)
        assert ok is True
        assert json.loads(body)["count"] == 0

    @pytest.mark.asyncio
    async def test_recurring_with_occurrences(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%d")
        occ_time = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()

        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 100, "type": 8, "topic": "Weekly", "recurrence": {}},
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={
                "id": "100", "join_url": "https://zoom.us/j/100",
                "occurrences": [
                    {"occurrence_id": "o1", "start_time": occ_time, "duration": 30, "status": "available"},
                ],
            }
        ))
        ok, body = await zoom.list_meetings(from_="2026-01-01", to_=future)
        assert ok is True
        parsed = json.loads(body)
        assert any(m.get("recurring") for m in parsed["meetings"])

    @pytest.mark.asyncio
    async def test_top_limit(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        meetings = [{"id": i, "type": 2, "topic": f"M{i}", "start_time": start} for i in range(50)]
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": meetings}
        ))
        ok, body = await zoom.list_meetings(from_="2026-01-01", to_=future, top=3)
        assert ok is True
        assert json.loads(body)["count"] == 3

    @pytest.mark.asyncio
    async def test_api_failure(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(success=False, error="unauthorized"))
        ok, body = await zoom.list_meetings(from_="2026-03-01", to_=future)
        assert ok is True
        assert json.loads(body)["count"] == 0

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.meetings = AsyncMock(side_effect=RuntimeError("oops"))
        ok, body = await zoom.list_meetings(from_="2026-03-01", to_="2026-03-31")
        assert ok is False
        assert "oops" in body


class TestGetMeeting:
    @pytest.mark.asyncio
    async def test_success(self, zoom):
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "123", "topic": "Hello"}
        ))
        ok, body = await zoom.get_meeting(meeting_id="123")
        assert ok is True
        assert "Hello" in body

    @pytest.mark.asyncio
    async def test_with_occurrence_id(self, zoom):
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "123"}
        ))
        ok, body = await zoom.get_meeting(meeting_id="123", occurrence_id="occ1")
        assert ok is True
        zoom.client.meeting.assert_called_once_with(meetingId="123", occurrence_id="occ1")

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.meeting = AsyncMock(side_effect=ValueError("bad"))
        ok, body = await zoom.get_meeting(meeting_id="123")
        assert ok is False
        assert "bad" in body


class TestCreateMeeting:
    @pytest.mark.asyncio
    async def test_minimal(self, zoom):
        zoom.client.meeting_create = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "999", "join_url": "https://zoom.us/j/999"}
        ))
        ok, body = await zoom.create_meeting(user_id="me", topic="Quick")
        assert ok is True
        assert "999" in body

    @pytest.mark.asyncio
    async def test_with_all_options(self, zoom):
        zoom.client.meeting_create = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "1"}
        ))
        ok, body = await zoom.create_meeting(
            user_id="me", topic="Full",
            start_time="2026-03-20T14:00:00Z",
            duration=90, timezone="Asia/Kolkata",
            agenda="Discuss things", type_=2,
        )
        assert ok is True
        call_kwargs = zoom.client.meeting_create.call_args[1]
        assert call_kwargs["body"]["topic"] == "Full"
        assert call_kwargs["body"]["duration"] == 90

    @pytest.mark.asyncio
    async def test_with_recurrence_missing_z(self, zoom):
        zoom.client.meeting_create = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "1"}
        ))
        rec = RecurrenceInput(type_=1, end_date_time="2026-03-31T23:59:00")
        ok, body = await zoom.create_meeting(user_id="me", topic="Rec", type_=8, recurrence=rec)
        assert ok is True
        call_body = zoom.client.meeting_create.call_args[1]["body"]
        assert call_body["recurrence"]["end_date_time"].endswith("Z")

    @pytest.mark.asyncio
    async def test_with_recurrence_already_z(self, zoom):
        zoom.client.meeting_create = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "1"}
        ))
        rec = RecurrenceInput(type_=1, end_date_time="2026-03-31T23:59:00Z")
        ok, body = await zoom.create_meeting(user_id="me", topic="Rec", type_=8, recurrence=rec)
        assert ok is True
        call_body = zoom.client.meeting_create.call_args[1]["body"]
        assert call_body["recurrence"]["end_date_time"] == "2026-03-31T23:59:00Z"

    @pytest.mark.asyncio
    async def test_with_invitees(self, zoom):
        zoom.client.meeting_create = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "1"}
        ))
        ok, body = await zoom.create_meeting(
            user_id="me", topic="Invite",
            invitees=["a@x.com", "b@x.com"],
        )
        assert ok is True
        call_body = zoom.client.meeting_create.call_args[1]["body"]
        assert call_body["settings"]["meeting_invitees"][0]["email"] == "a@x.com"

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.meeting_create = AsyncMock(side_effect=RuntimeError("create fail"))
        ok, body = await zoom.create_meeting(user_id="me", topic="Fail")
        assert ok is False
        assert "create fail" in body


class TestUpdateMeeting:
    @pytest.mark.asyncio
    async def test_empty_body(self, zoom):
        ok, body = await zoom.update_meeting(meeting_id="123")
        assert ok is False
        assert "No fields to update" in body

    @pytest.mark.asyncio
    async def test_with_fields(self, zoom):
        zoom.client.meeting_update = AsyncMock(return_value=_zoom_resp(
            success=True, data={}
        ))
        ok, body = await zoom.update_meeting(meeting_id="123", topic="New Topic", duration=45)
        assert ok is True
        call_kwargs = zoom.client.meeting_update.call_args[1]
        assert call_kwargs["body"]["topic"] == "New Topic"
        assert call_kwargs["body"]["duration"] == 45

    @pytest.mark.asyncio
    async def test_with_recurrence(self, zoom):
        zoom.client.meeting_update = AsyncMock(return_value=_zoom_resp(
            success=True, data={}
        ))
        rec = RecurrenceInput(type_=2, end_date_time="2026-04-30T00:00:00")
        ok, body = await zoom.update_meeting(meeting_id="123", recurrence=rec)
        assert ok is True
        call_body = zoom.client.meeting_update.call_args[1]["body"]
        assert call_body["recurrence"]["end_date_time"].endswith("Z")

    @pytest.mark.asyncio
    async def test_with_invitees(self, zoom):
        zoom.client.meeting_update = AsyncMock(return_value=_zoom_resp(
            success=True, data={}
        ))
        ok, body = await zoom.update_meeting(meeting_id="123", invitees=["x@y.com"])
        assert ok is True
        call_body = zoom.client.meeting_update.call_args[1]["body"]
        assert "meeting_invitees" in call_body["settings"]

    @pytest.mark.asyncio
    async def test_with_occurrence_id(self, zoom):
        zoom.client.meeting_update = AsyncMock(return_value=_zoom_resp(
            success=True, data={}
        ))
        ok, body = await zoom.update_meeting(
            meeting_id="123", topic="Changed", occurrence_id="occ1"
        )
        assert ok is True
        call_kwargs = zoom.client.meeting_update.call_args[1]
        assert call_kwargs["occurrence_id"] == "occ1"
        assert call_kwargs["body"]["occurrence_id"] == "occ1"

    @pytest.mark.asyncio
    async def test_with_all_optional_fields(self, zoom):
        zoom.client.meeting_update = AsyncMock(return_value=_zoom_resp(
            success=True, data={}
        ))
        ok, body = await zoom.update_meeting(
            meeting_id="123",
            topic="T", start_time="2026-03-20T14:00:00Z",
            duration=60, timezone="UTC", agenda="Agenda",
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.meeting_update = AsyncMock(side_effect=RuntimeError("update fail"))
        ok, body = await zoom.update_meeting(meeting_id="123", topic="X")
        assert ok is False
        assert "update fail" in body


class TestDeleteMeeting:
    @pytest.mark.asyncio
    async def test_success(self, zoom):
        zoom.client.meeting_delete = AsyncMock(return_value=_zoom_resp(
            success=True, data={}
        ))
        ok, body = await zoom.delete_meeting(meeting_id="123")
        assert ok is True

    @pytest.mark.asyncio
    async def test_with_occurrence_id(self, zoom):
        zoom.client.meeting_delete = AsyncMock(return_value=_zoom_resp(
            success=True, data={}
        ))
        ok, body = await zoom.delete_meeting(meeting_id="123", occurrence_id="occ1")
        assert ok is True
        zoom.client.meeting_delete.assert_called_once_with(
            meetingId="123", occurrence_id="occ1", cancel_meeting_reminder=None,
        )

    @pytest.mark.asyncio
    async def test_with_cancel_reminder(self, zoom):
        zoom.client.meeting_delete = AsyncMock(return_value=_zoom_resp(
            success=True, data={}
        ))
        ok, body = await zoom.delete_meeting(meeting_id="123", cancel_meeting_reminder=True)
        assert ok is True
        zoom.client.meeting_delete.assert_called_once_with(
            meetingId="123", occurrence_id=None, cancel_meeting_reminder=True,
        )

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.meeting_delete = AsyncMock(side_effect=RuntimeError("del fail"))
        ok, body = await zoom.delete_meeting(meeting_id="123")
        assert ok is False
        assert "del fail" in body


class TestListUpcomingMeetings:
    @pytest.mark.asyncio
    async def test_success(self, zoom):
        zoom.client.list_upcoming_meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": []}
        ))
        ok, body = await zoom.list_upcoming_meetings(user_id="me")
        assert ok is True

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.list_upcoming_meeting = AsyncMock(side_effect=RuntimeError("err"))
        ok, body = await zoom.list_upcoming_meetings()
        assert ok is False
        assert "err" in body


class TestGetMeetingInvitation:
    @pytest.mark.asyncio
    async def test_success(self, zoom):
        zoom.client.meeting_invitation = AsyncMock(return_value=_zoom_resp(
            success=True, data={"invitation": "Join meeting..."}
        ))
        ok, body = await zoom.get_meeting_invitation(meeting_id="123")
        assert ok is True
        assert "invitation" in body

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.meeting_invitation = AsyncMock(side_effect=RuntimeError("inv fail"))
        ok, body = await zoom.get_meeting_invitation(meeting_id="123")
        assert ok is False


class TestListRecurringMeetingsEndingInRange:
    @pytest.mark.asyncio
    async def test_with_end_date_time_in_range(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {
                    "id": 1, "type": 8, "topic": "Weekly",
                    "recurrence": {"end_date_time": "2026-03-15T00:00:00Z", "type": 2, "repeat_interval": 1},
                    "join_url": "https://zoom.us/j/1",
                },
            ]}
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        parsed = json.loads(body)
        assert parsed["count"] == 1
        assert parsed["meetings"][0]["end_determined_by"] == "end_date_time"

    @pytest.mark.asyncio
    async def test_with_end_date_time_out_of_range(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {
                    "id": 1, "type": 8, "topic": "Far Future",
                    "recurrence": {"end_date_time": "2027-01-01T00:00:00Z"},
                },
            ]}
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        assert json.loads(body)["count"] == 0

    @pytest.mark.asyncio
    async def test_fallback_to_last_occurrence(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 2, "type": 3, "topic": "Fallback"},
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={
                "id": "2", "topic": "Fallback", "join_url": "url",
                "recurrence": {"type": 1, "repeat_interval": 1},
                "occurrences": [
                    {"start_time": "2026-03-10T10:00:00Z"},
                    {"start_time": "2026-03-20T10:00:00Z"},
                ],
            }
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        parsed = json.loads(body)
        assert parsed["count"] == 1
        assert parsed["meetings"][0]["end_determined_by"] == "last_occurrence"

    @pytest.mark.asyncio
    async def test_no_occurrences(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 3, "type": 3, "topic": "NoOcc"},
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "3", "occurrences": []}
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        assert json.loads(body)["count"] == 0

    @pytest.mark.asyncio
    async def test_pagination(self, zoom):
        page1 = _zoom_resp(success=True, data={
            "meetings": [{"id": 1, "type": 8, "topic": "A", "recurrence": {"end_date_time": "2026-03-15T00:00:00Z"}}],
            "next_page_token": "tok",
        })
        page2 = _zoom_resp(success=True, data={
            "meetings": [{"id": 2, "type": 8, "topic": "B", "recurrence": {"end_date_time": "2026-03-20T00:00:00Z"}}],
        })
        zoom.client.meetings = AsyncMock(side_effect=[page1, page2, page1, page2])
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_non_recurring_filtered_out(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 1, "type": 2, "topic": "Not recurring"},
            ]}
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        assert json.loads(body)["count"] == 0

    @pytest.mark.asyncio
    async def test_top_limit(self, zoom):
        meetings = [
            {
                "id": i, "type": 8, "topic": f"M{i}",
                "recurrence": {"end_date_time": f"2026-03-{10+i:02d}T00:00:00Z"},
            }
            for i in range(10)
        ]
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": meetings}
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z", top=2
        )
        assert ok is True
        assert json.loads(body)["count"] == 2

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.meetings = AsyncMock(side_effect=RuntimeError("boom"))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is False
        assert "boom" in body


class TestGetMeetingTranscript:
    @pytest.mark.asyncio
    async def test_full_success_path(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [{"uuid": "abc123"}]}
        ))
        zoom.client.get_meeting_transcript = AsyncMock(return_value=_zoom_resp(
            success=True, data={"download_url": "https://zoom.us/vtt/123"}
        ))
        zoom.client.http.execute = AsyncMock(return_value=MagicMock(
            text=MagicMock(return_value="WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n\n")
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is True
        parsed = json.loads(body)
        assert "Hello" in parsed["transcript"]
        assert parsed["instance_uuid"] == "abc123"

    @pytest.mark.asyncio
    async def test_no_past_instances(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": []}
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "No past instances" in body

    @pytest.mark.asyncio
    async def test_uuid_with_slash(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [{"uuid": "/abc/def"}]}
        ))
        zoom.client.get_meeting_transcript = AsyncMock(return_value=_zoom_resp(
            success=True, data={"download_url": "https://zoom.us/vtt"}
        ))
        zoom.client.http.execute = AsyncMock(return_value=MagicMock(
            text=MagicMock(return_value="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nTest\n\n")
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is True
        call_args = zoom.client.get_meeting_transcript.call_args[1]
        assert "%2F" in call_args["meetingId"] or "%252F" in call_args["meetingId"]

    @pytest.mark.asyncio
    async def test_uuid_without_slash(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [{"uuid": "simple-uuid"}]}
        ))
        zoom.client.get_meeting_transcript = AsyncMock(return_value=_zoom_resp(
            success=True, data={"download_url": "https://zoom.us/vtt"}
        ))
        zoom.client.http.execute = AsyncMock(return_value=MagicMock(
            text=MagicMock(return_value="WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHi\n\n")
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is True
        call_args = zoom.client.get_meeting_transcript.call_args[1]
        assert call_args["meetingId"] == "simple-uuid"

    @pytest.mark.asyncio
    async def test_missing_uuid(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [{"uuid": None}]}
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "UUID missing" in body

    @pytest.mark.asyncio
    async def test_transcript_response_failure(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [{"uuid": "abc"}]}
        ))
        zoom.client.get_meeting_transcript = AsyncMock(return_value=_zoom_resp(
            success=False, error="no transcript"
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "no transcript" in body

    @pytest.mark.asyncio
    async def test_no_download_url(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [{"uuid": "abc"}]}
        ))
        zoom.client.get_meeting_transcript = AsyncMock(return_value=_zoom_resp(
            success=True, data={"other_field": "value"}
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "No download_url" in body

    @pytest.mark.asyncio
    async def test_vtt_fetch_failure(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [{"uuid": "abc"}]}
        ))
        zoom.client.get_meeting_transcript = AsyncMock(return_value=_zoom_resp(
            success=True, data={"download_url": "https://zoom.us/vtt"}
        ))
        zoom.client.http.execute = AsyncMock(side_effect=RuntimeError("network"))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "Failed to download" in body

    @pytest.mark.asyncio
    async def test_past_meetings_failure(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=False, error="unauthorized"
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "unauthorized" in body

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.past_meetings = AsyncMock(side_effect=RuntimeError("fail"))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "fail" in body

    @pytest.mark.asyncio
    async def test_instances_data_not_dict(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data=["not a dict"]
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "No past instances" in body


class TestListContacts:
    @pytest.mark.asyncio
    async def test_single_type(self, zoom):
        zoom.client.get_user_contacts = AsyncMock(return_value=_zoom_resp(
            success=True, data={"contacts": [{"id": "c1", "name": "Alice"}]}
        ))
        ok, body = await zoom.list_contacts(type_="company", top=10)
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["contacts"][0]["contact_type"] == "company"

    @pytest.mark.asyncio
    async def test_all_types(self, zoom):
        zoom.client.get_user_contacts = AsyncMock(return_value=_zoom_resp(
            success=True, data={"contacts": [{"id": "c1"}]}
        ))
        ok, body = await zoom.list_contacts(type_=None, top=100)
        assert ok is True
        assert zoom.client.get_user_contacts.call_count == 3

    @pytest.mark.asyncio
    async def test_pagination(self, zoom):
        page1 = _zoom_resp(success=True, data={
            "contacts": [{"id": "c1"}],
            "next_page_token": "page2",
        })
        page2 = _zoom_resp(success=True, data={
            "contacts": [{"id": "c2"}],
        })
        zoom.client.get_user_contacts = AsyncMock(side_effect=[page1, page2])
        ok, body = await zoom.list_contacts(type_="personal", top=100)
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["total"] == 2

    @pytest.mark.asyncio
    async def test_failed_response(self, zoom):
        zoom.client.get_user_contacts = AsyncMock(return_value=_zoom_resp(
            success=False, error="err"
        ))
        ok, body = await zoom.list_contacts(type_="company")
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_limit_reached(self, zoom):
        zoom.client.get_user_contacts = AsyncMock(return_value=_zoom_resp(
            success=True, data={"contacts": [{"id": f"c{i}"} for i in range(20)]}
        ))
        ok, body = await zoom.list_contacts(type_="company", top=5)
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["total"] == 5

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.get_user_contacts = AsyncMock(side_effect=RuntimeError("contact err"))
        ok, body = await zoom.list_contacts()
        assert ok is False
        assert "contact err" in body

    @pytest.mark.asyncio
    async def test_limit_reached_across_types(self, zoom):
        zoom.client.get_user_contacts = AsyncMock(return_value=_zoom_resp(
            success=True, data={"contacts": [{"id": f"c{i}"} for i in range(10)]}
        ))
        ok, body = await zoom.list_contacts(type_=None, top=5)
        assert ok is True
        parsed = json.loads(body)
        assert parsed["data"]["total"] == 5


class TestGetContact:
    @pytest.mark.asyncio
    async def test_success(self, zoom):
        zoom.client.get_user_contact = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "c1", "email": "a@b.com"}
        ))
        ok, body = await zoom.get_contact(identifier="a@b.com")
        assert ok is True
        assert "a@b.com" in body

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.get_user_contact = AsyncMock(side_effect=RuntimeError("contact fail"))
        ok, body = await zoom.get_contact(identifier="x")
        assert ok is False
        assert "contact fail" in body


class TestListFolderChildren:
    @pytest.mark.asyncio
    async def test_success(self, zoom):
        zoom.client.list_all_children = AsyncMock(return_value=_zoom_resp(
            success=True, data={"files": [{"id": "f1", "name": "doc.txt"}]}
        ))
        ok, body = await zoom.list_folder_children(folder_id="root", page_size=50)
        assert ok is True
        assert "doc.txt" in body

    @pytest.mark.asyncio
    async def test_exception(self, zoom):
        zoom.client.list_all_children = AsyncMock(side_effect=RuntimeError("folder fail"))
        ok, body = await zoom.list_folder_children()
        assert ok is False
        assert "folder fail" in body


class TestListMeetingsRecurringNoDetail:
    @pytest.mark.asyncio
    async def test_recurring_detail_not_found(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%d")
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 200, "type": 3, "topic": "Ghost", "recurrence": {}},
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=False, error="not found"
        ))
        ok, body = await zoom.list_meetings(from_="2026-01-01", to_=future)
        assert ok is True
        assert json.loads(body)["count"] == 0


class TestListRecurringEndingFallbackLastOccOutOfRange:
    @pytest.mark.asyncio
    async def test_last_occurrence_out_of_range(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 5, "type": 3, "topic": "OutOfRange"},
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={
                "id": "5", "topic": "OutOfRange",
                "recurrence": {},
                "occurrences": [
                    {"start_time": "2027-06-01T10:00:00Z"},
                ],
            }
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        assert json.loads(body)["count"] == 0


class TestListRecurringEndingFallbackMissingStartTime:
    @pytest.mark.asyncio
    async def test_last_occurrence_missing_start_time(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 6, "type": 3, "topic": "NoStart"},
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={
                "id": "6", "topic": "NoStart",
                "recurrence": {},
                "occurrences": [
                    {"start_time": ""},
                ],
            }
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        assert json.loads(body)["count"] == 0


class TestListRecurringEndingDetailNotFound:
    @pytest.mark.asyncio
    async def test_detail_not_found(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 7, "type": 3, "topic": "NoDetail"},
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=False, error="not found"
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        assert json.loads(body)["count"] == 0


class TestTranscriptRawDictResponse:
    @pytest.mark.asyncio
    async def test_past_meetings_raw_dict(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [{"uuid": "raw-uuid"}]}
        ))
        zoom.client.get_meeting_transcript = AsyncMock(return_value=_zoom_resp(
            success=True, data={"download_url": "https://zoom.us/vtt"}
        ))
        mock_resp = MagicMock()
        mock_resp.text.return_value = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nRaw\n\n"
        zoom.client.http.execute = AsyncMock(return_value=mock_resp)
        ok, body = await zoom.get_meeting_transcript(meeting_id="999")
        assert ok is True


class TestTranscriptMessageFieldFallback:
    @pytest.mark.asyncio
    async def test_past_meetings_failure_message_only(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=False, message="rate limited"
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "rate limited" in body

    @pytest.mark.asyncio
    async def test_transcript_failure_message_only(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [{"uuid": "abc"}]}
        ))
        zoom.client.get_meeting_transcript = AsyncMock(return_value=_zoom_resp(
            success=False, message="not available"
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "not available" in body


class TestTranscriptNoDownloadUrlNonDict:
    @pytest.mark.asyncio
    async def test_transcript_data_not_dict(self, zoom):
        zoom.client.past_meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [{"uuid": "abc"}]}
        ))
        zoom.client.get_meeting_transcript = AsyncMock(return_value=_zoom_resp(
            success=True, data=["not a dict"]
        ))
        ok, body = await zoom.get_meeting_transcript(meeting_id="123")
        assert ok is False
        assert "No download_url" in body


class TestZoomInit:
    def test_init_creates_data_source(self):
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock(get_base_url=MagicMock(return_value="https://api.zoom.us/v2"))
        with patch("app.agents.actions.zoom.zoom.ZoomDataSource") as mock_ds:
            z = Zoom(mock_client)
            mock_ds.assert_called_once_with(mock_client)


class TestHandleResponseNonStandardInput:
    def test_response_neither_zoom_nor_dict(self, zoom):
        result = zoom._handle_response("unexpected string", "msg")
        assert result is None


class TestListMeetingsEmptyMeetingTypes:
    @pytest.mark.asyncio
    async def test_from_future_to_past_defaults_to_upcoming(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": []}
        ))
        ok, body = await zoom.list_meetings(from_="2099-01-01", to_="2020-01-01")
        assert ok is True
        call_kwargs = zoom.client.meetings.call_args[1]
        assert call_kwargs["type_"] == "upcoming"


class TestListMeetingsRecurringEndDateException:
    @pytest.mark.asyncio
    async def test_recurring_invalid_end_date_time(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%d")
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {
                    "id": 300, "type": 8, "topic": "BadDate",
                    "recurrence": {"end_date_time": "not-a-date"},
                },
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={"id": "300", "occurrences": []}
        ))
        ok, body = await zoom.list_meetings(from_="2026-01-01", to_=future)
        assert ok is True


class TestListMeetingsTopReachedInNonRecurring:
    @pytest.mark.asyncio
    async def test_top_reached_early_in_step3(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        meetings = [
            {"id": i, "type": 2, "topic": f"M{i}", "start_time": start}
            for i in range(10)
        ]
        meetings.append({"id": 99, "type": 8, "topic": "Recurring", "recurrence": {}})
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": meetings}
        ))
        ok, body = await zoom.list_meetings(from_="2026-01-01", to_=future, top=2)
        assert ok is True
        assert json.loads(body)["count"] == 2


class TestListMeetingsTopReachedInOccurrences:
    @pytest.mark.asyncio
    async def test_top_reached_during_occurrence_expansion(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")
        occ_time1 = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        occ_time2 = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()

        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 500, "type": 8, "topic": "LotsOfOcc", "recurrence": {}},
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={
                "id": "500", "join_url": "url",
                "occurrences": [
                    {"occurrence_id": "o1", "start_time": occ_time1, "duration": 30, "status": "available"},
                    {"occurrence_id": "o2", "start_time": occ_time2, "duration": 30, "status": "available"},
                ],
            }
        ))
        ok, body = await zoom.list_meetings(from_="2026-01-01", to_=future, top=1)
        assert ok is True
        assert json.loads(body)["count"] == 1


class TestListRecurringEndingInvalidEndDateTime:
    @pytest.mark.asyncio
    async def test_invalid_end_date_time_exception_pass(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {
                    "id": 8, "type": 8, "topic": "BadEndDate",
                    "recurrence": {"end_date_time": "invalid-date"},
                },
            ]}
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        assert json.loads(body)["count"] == 0


class TestListRecurringEndingTopReachedSkipsFallback:
    @pytest.mark.asyncio
    async def test_top_reached_in_early_filter_skips_detail_fetch(self, zoom):
        meetings = [
            {
                "id": i, "type": 8, "topic": f"M{i}",
                "recurrence": {"end_date_time": f"2026-03-{10+i:02d}T00:00:00Z"},
            }
            for i in range(5)
        ]
        meetings.append({"id": 99, "type": 3, "topic": "NeedsDetail"})
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": meetings}
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z", top=1
        )
        assert ok is True
        assert json.loads(body)["count"] == 1


class TestListRecurringEndingTopReachedInFallback:
    @pytest.mark.asyncio
    async def test_top_reached_in_fallback_loop(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 10, "type": 3, "topic": "Fallback1"},
                {"id": 11, "type": 3, "topic": "Fallback2"},
            ]}
        ))

        async def meeting_side_effect(meetingId, **kwargs):
            return _zoom_resp(success=True, data={
                "id": meetingId, "topic": f"T{meetingId}",
                "recurrence": {"type": 1},
                "join_url": "url",
                "occurrences": [{"start_time": "2026-03-15T10:00:00Z"}],
            })

        zoom.client.meeting = AsyncMock(side_effect=meeting_side_effect)
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z", top=1
        )
        assert ok is True
        assert json.loads(body)["count"] == 1


class TestListRecurringEndingApiFailure:
    @pytest.mark.asyncio
    async def test_api_failure_breaks_loop(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=False, error="unauthorized"
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        assert json.loads(body)["count"] == 0


class TestParseVttTextBeforeTimecode:
    def test_text_before_any_timecode_ignored(self):
        vtt = (
            "WEBVTT\n"
            "\n"
            "some random text before timecodes\n"
            "\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "Actual cue\n"
            "\n"
        )
        result = Zoom._parse_vtt(vtt)
        assert "some random text" not in result
        assert "Actual cue" in result


class TestListMeetingsTypeNotRecurringOrScheduled:
    @pytest.mark.asyncio
    async def test_meeting_type_1_ignored(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 1, "type": 1, "topic": "Instant"},
                {"id": 2, "type": 2, "topic": "Scheduled", "start_time": start},
            ]}
        ))
        ok, body = await zoom.list_meetings(from_="2026-01-01", to_=future)
        assert ok is True
        parsed = json.loads(body)
        topics = [m["topic"] for m in parsed["meetings"]]
        assert "Instant" not in topics
        assert "Scheduled" in topics


class TestListMeetingsRecurringWithValidEndDate:
    @pytest.mark.asyncio
    async def test_recurring_with_non_expired_end_date_time(self, zoom):
        from_date = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d")
        to_date = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")
        end_dt_str = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        occ_time = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()

        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {
                    "id": 777, "type": 8, "topic": "ValidEnd",
                    "recurrence": {"end_date_time": end_dt_str},
                },
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={
                "id": "777", "join_url": "url",
                "occurrences": [
                    {"occurrence_id": "o1", "start_time": occ_time, "duration": 30, "status": "available"},
                ],
            }
        ))
        ok, body = await zoom.list_meetings(from_=from_date, to_=to_date)
        assert ok is True
        parsed = json.loads(body)
        assert any(m.get("meeting_id") == "777" for m in parsed["meetings"])


class TestListMeetingsOccurrenceOutOfRange:
    @pytest.mark.asyncio
    async def test_occurrence_out_of_range_skipped(self, zoom):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        occ_in_range = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        occ_out_of_range = "2020-01-01T10:00:00Z"

        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {"id": 888, "type": 8, "topic": "MixedOcc", "recurrence": {}},
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={
                "id": "888", "join_url": "url",
                "occurrences": [
                    {"occurrence_id": "old", "start_time": occ_out_of_range, "duration": 30},
                    {"occurrence_id": "new", "start_time": occ_in_range, "duration": 30},
                ],
            }
        ))
        ok, body = await zoom.list_meetings(from_="2026-01-01", to_=future)
        assert ok is True
        parsed = json.loads(body)
        occ_ids = [m.get("occurrence_id") for m in parsed["meetings"]]
        assert "old" not in occ_ids
        assert "new" in occ_ids


class TestListRecurringEndingMidNotInRecurringIds:
    @pytest.mark.asyncio
    async def test_meeting_handled_in_early_filter_skipped_in_fallback(self, zoom):
        zoom.client.meetings = AsyncMock(return_value=_zoom_resp(
            success=True, data={"meetings": [
                {
                    "id": 20, "type": 8, "topic": "EarlyHandled",
                    "recurrence": {"end_date_time": "2026-03-15T00:00:00Z", "type": 2},
                },
                {"id": 21, "type": 3, "topic": "NeedsFallback"},
            ]}
        ))
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(
            success=True, data={
                "id": "21", "topic": "NeedsFallback",
                "recurrence": {"type": 1},
                "join_url": "url",
                "occurrences": [{"start_time": "2026-03-20T10:00:00Z"}],
            }
        ))
        ok, body = await zoom.list_recurring_meetings_ending_in_range(
            from_="2026-03-01T00:00:00Z", to_="2026-03-31T23:59:59Z"
        )
        assert ok is True
        parsed = json.loads(body)
        assert parsed["count"] == 2


class TestGetMeetingDetailDictNonDictJsonLoads:
    @pytest.mark.asyncio
    async def test_json_loads_returns_non_dict(self, zoom):
        zoom.client.meeting = AsyncMock(return_value=_zoom_resp(success=True, data={"id": "1"}))
        with patch("app.agents.actions.zoom.zoom.json.loads", return_value=[1, 2, 3]):
            result = await zoom._get_meeting_detail_dict("123")
        assert result is None
