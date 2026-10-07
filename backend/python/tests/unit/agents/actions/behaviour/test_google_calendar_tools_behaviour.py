"""Behaviour tests for the Google Calendar agent tools.

Each test drives a tool the way the agent does and checks what Google would
receive and what the agent is told back. See ``gcal_tool_fakes`` for what
is real and what is faked.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from gcal_tool_fakes import (
    ACCESS_TOKEN,
    CLIENT_SECRET,
    REFRESH_TOKEN,
    TOKEN_PATH,
    FakeGoogleHttp,
    GoogleResponse,
    build_calendar_tool,
    google_error,
    result,
)

from app.agents.actions.google.calendar.calendar import (
    CreateCalendarEventInput,
    GoogleCalendar,
)

EVENTS = "/calendars/primary/events"


@pytest.fixture
def http() -> FakeGoogleHttp:
    return FakeGoogleHttp()


@pytest.fixture
def cal(http: FakeGoogleHttp) -> GoogleCalendar:
    return build_calendar_tool(http)


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def assert_safe_error(payload: dict[str, Any]) -> str:
    """The error is plain text the agent can relay: no secrets, no raw library dump."""
    message = payload["error"]
    assert isinstance(message, str) and message
    for leaked in (ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET, "Bearer", "HttpError", "googleapis.com", "<"):
        assert leaked not in message, f"{leaked!r} leaked into: {message}"
    return message


def created_event(**overrides: object) -> dict[str, Any]:
    event = {
        "id": "evt-1",
        "summary": "Design review",
        "start": {"dateTime": "2026-09-30T10:00:00Z"},
        "end": {"dateTime": "2026-09-30T11:00:00Z"},
        "organizer": {"email": "me@example.com"},
        "attendees": [{"email": "a@example.com"}],
    }
    event.update(overrides)
    return event


# ---------------------------------------------------------------------------
# get_calendar_events
# ---------------------------------------------------------------------------


class TestGetCalendarEvents:
    async def test_filters_reach_google_as_query_parameters_on_the_users_primary_calendar(self, cal, http) -> None:
        http.on("GET", EVENTS, {"items": [{"id": "e1", "summary": "Standup", "location": "Room 1"}, {"id": "e2"}]})

        ok, data = result(await cal.get_calendar_events(
            max_results=5, time_min="2026-09-30T00:00:00Z", time_max="2026-10-01T00:00:00Z",
            order_by="startTime", single_events=True, query="standup", show_deleted=False, time_zone="UTC",
        ))

        assert ok is True
        request = http.calls("GET", EVENTS)[0]
        query = dict(request.query)
        assert instant(query.pop("timeMin")) == instant("2026-09-30T00:00:00Z")
        assert instant(query.pop("timeMax")) == instant("2026-10-01T00:00:00Z")
        assert query == {
            "maxResults": "5", "orderBy": "startTime", "singleEvents": "true", "q": "standup",
            "showDeleted": "false", "timeZone": "UTC", "alt": "json",
        }
        assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
        assert [e["location"] for e in data["items"]] == ["Room 1", ""]

    async def test_named_calendar_and_unset_filters_are_not_sent(self, cal, http) -> None:
        http.on("GET", "/calendars/team@group.calendar.google.com/events", {"items": []})

        ok, _ = result(await cal.get_calendar_events(calendar_id="team@group.calendar.google.com"))

        assert ok is True
        assert http.calls("GET")[0].query == {"alt": "json"}

    async def test_next_page_token_is_passed_back_to_the_agent(self, cal, http) -> None:
        http.on("GET", EVENTS, {"items": [{"id": "e1"}], "nextPageToken": "page-2"})

        _, data = result(await cal.get_calendar_events(max_results=1))

        assert data["nextPageToken"] == "page-2"

    async def test_time_without_offset_is_sent_as_rfc3339_in_the_requested_zone(self, cal, http) -> None:
        # Google rejects timeMin/timeMax without an offset, so a bare local time must gain one.
        http.on("GET", EVENTS, {"items": []})

        ok, _ = result(await cal.get_calendar_events(
            time_min="2026-09-30T09:00:00", time_max="2026-09-30T18:00:00", time_zone="Asia/Kolkata",
        ))

        assert ok is True
        query = http.calls("GET", EVENTS)[0].query
        assert instant(query["timeMin"]) == instant("2026-09-30T09:00:00+05:30")
        assert instant(query["timeMax"]) == instant("2026-09-30T18:00:00+05:30")

    async def test_unreadable_time_is_refused_before_calling_google(self, cal, http) -> None:
        ok, data = result(await cal.get_calendar_events(time_min="next tuesday"))

        assert ok is False
        message = assert_safe_error(data)
        assert "next tuesday" in message and "ISO" in message
        assert http.requests == []

    @pytest.mark.parametrize("response", [
        google_error(429, "Rate Limit Exceeded", "rateLimitExceeded", {"retry-after": "7"}),
        google_error(403, "Rate Limit Exceeded", "userRateLimitExceeded", {"retry-after": "7"}),
    ])
    async def test_rate_limit_tells_the_agent_to_wait_and_retry(self, cal, http, response) -> None:
        http.on("GET", EVENTS, response)

        ok, data = result(await cal.get_calendar_events())

        assert ok is False
        message = assert_safe_error(data)
        assert "too many requests" in message.lower()
        assert "7 seconds" in message

    async def test_expired_sign_in_asks_the_user_to_reconnect(self, cal, http) -> None:
        http.on("GET", EVENTS, google_error(401, "Invalid Credentials", "authError"))

        ok, data = result(await cal.get_calendar_events())

        assert ok is False
        assert "reconnect" in assert_safe_error(data).lower()

    async def test_token_still_rejected_after_refresh_asks_to_reconnect_without_leaking_credentials(self, http) -> None:
        cal = build_calendar_tool(http, refreshable=True)
        http.on("POST", TOKEN_PATH, {"access_token": "ya29.refreshed", "expires_in": 3600}, base="")
        http.on("GET", EVENTS, google_error(401, "Invalid Credentials", "authError"))

        ok, data = result(await cal.get_calendar_events())

        assert ok is False
        assert "reconnect" in assert_safe_error(data).lower()
        sent = [r.headers["authorization"] for r in http.calls("GET", EVENTS)]
        assert sent[0] == f"Bearer {ACCESS_TOKEN}"
        assert set(sent[1:]) == {"Bearer ya29.refreshed"}

    async def test_unexpected_failure_is_reported_without_the_raw_exception(self, cal, http) -> None:
        http.on("GET", EVENTS, GoogleResponse(200))
        http.request = lambda *a, **k: (_ for _ in ()).throw(TimeoutError(f"timed out, token={ACCESS_TOKEN}"))

        ok, data = result(await cal.get_calendar_events())

        assert ok is False
        assert "try again" in assert_safe_error(data).lower()

    async def test_missing_scope_asks_the_user_to_reconnect_with_calendar_access(self, cal, http) -> None:
        http.on("GET", EVENTS, google_error(403, "Request had insufficient authentication scopes.", "insufficientPermissions"))

        ok, data = result(await cal.get_calendar_events())

        assert ok is False
        message = assert_safe_error(data).lower()
        assert "permission" in message and "reconnect" in message

    async def test_google_outage_is_reported_as_temporary(self, cal, http) -> None:
        http.on("GET", EVENTS, google_error(503, "Backend Error", "backendError"))

        ok, data = result(await cal.get_calendar_events())

        assert ok is False
        assert "try again" in assert_safe_error(data).lower()

    async def test_bad_request_relays_googles_reason(self, cal, http) -> None:
        http.on("GET", EVENTS, google_error(400, "The requested ordering is not available for the particular query.", "badRequest"))

        ok, data = result(await cal.get_calendar_events(order_by="startTime"))

        assert ok is False
        assert "ordering is not available" in assert_safe_error(data)


# ---------------------------------------------------------------------------
# create_calendar_event
# ---------------------------------------------------------------------------


class TestCreateCalendarEvent:
    async def test_creates_event_on_primary_calendar_and_invites_attendees(self, cal, http) -> None:
        http.on("POST", EVENTS, created_event())

        ok, data = result(await cal.create_calendar_event(
            event_start_time="2026-09-30T10:00:00Z", event_end_time="2026-09-30T11:00:00Z",
            event_title="Design review", event_description="Agenda", event_location="Room 1",
            event_attendees_emails=["a@example.com", "b@example.com"],
        ))

        assert ok is True
        post = http.calls("POST", EVENTS)[0]
        assert post.query["sendUpdates"] == "all"
        assert post.body["summary"] == "Design review"
        assert post.body["description"] == "Agenda"
        assert post.body["location"] == "Room 1"
        assert post.body["attendees"] == [{"email": "a@example.com"}, {"email": "b@example.com"}]
        assert instant(post.body["start"]["dateTime"]) == instant("2026-09-30T10:00:00Z")
        assert instant(post.body["end"]["dateTime"]) == instant("2026-09-30T11:00:00Z")
        assert data["event_id"] == "evt-1"
        assert data["event_title"] == "Design review"
        assert data["event_start_time"] == "2026-09-30T10:00:00Z"

    async def test_time_with_offset_keeps_its_instant(self, cal, http) -> None:
        http.on("POST", EVENTS, created_event())

        await cal.create_calendar_event(event_start_time="2026-09-30T10:00:00+05:30", event_end_time="2026-09-30T11:00:00+05:30")

        body = http.calls("POST", EVENTS)[0].body
        assert instant(body["start"]["dateTime"]) == instant("2026-09-30T04:30:00Z")

    async def test_local_time_is_read_in_the_requested_timezone(self, cal, http) -> None:
        # "10:00" for a user in India is 10:00 IST, not 10:00 on the server's clock.
        http.on("POST", EVENTS, created_event())

        ok, _ = result(await cal.create_calendar_event(
            event_start_time="2026-09-30T10:00:00", event_end_time="2026-09-30T11:00:00",
            event_timezone="Asia/Kolkata",
        ))

        assert ok is True
        body = http.calls("POST", EVENTS)[0].body
        assert instant(body["start"]["dateTime"]) == instant("2026-09-30T10:00:00+05:30")
        assert instant(body["end"]["dateTime"]) == instant("2026-09-30T11:00:00+05:30")
        assert body["start"]["timeZone"] == "Asia/Kolkata"
        assert body["end"]["timeZone"] == "Asia/Kolkata"

    async def test_unix_timestamp_is_accepted_as_the_parameter_promises(self, cal, http) -> None:
        http.on("POST", EVENTS, created_event())
        start = int(datetime(2026, 9, 30, 10, tzinfo=timezone.utc).timestamp())

        ok, _ = result(await cal.create_calendar_event(event_start_time=str(start), event_end_time=str(start + 3600)))

        assert ok is True
        body = http.calls("POST", EVENTS)[0].body
        assert instant(body["start"]["dateTime"]) == instant("2026-09-30T10:00:00Z")
        assert instant(body["end"]["dateTime"]) == instant("2026-09-30T11:00:00Z")

    async def test_unknown_timezone_is_refused_before_calling_google(self, cal, http) -> None:
        ok, data = result(await cal.create_calendar_event(
            event_start_time="2026-09-30T10:00:00", event_end_time="2026-09-30T11:00:00", event_timezone="Mars/Olympus",
        ))

        assert ok is False
        message = assert_safe_error(data)
        assert "Mars/Olympus" in message and "America/New_York" in message
        assert http.requests == []

    async def test_unreadable_date_is_refused_before_calling_google(self, cal, http) -> None:
        ok, data = result(await cal.create_calendar_event(event_start_time="tomorrow at 3", event_end_time="2026-09-30T11:00:00Z"))

        assert ok is False
        assert "tomorrow at 3" in assert_safe_error(data)
        assert http.requests == []

    async def test_end_before_start_is_refused_before_calling_google(self, cal, http) -> None:
        ok, data = result(await cal.create_calendar_event(event_start_time="2026-09-30T11:00:00Z", event_end_time="2026-09-30T10:00:00Z"))

        assert ok is False
        assert "end after it starts" in assert_safe_error(data)
        assert http.requests == []

    @pytest.mark.parametrize("start, end, missing", [("", "2026-09-30T11:00:00Z", "start time"), ("2026-09-30T10:00:00Z", "", "end time")])
    async def test_missing_time_is_refused(self, cal, http, start, end, missing) -> None:
        ok, data = result(await cal.create_calendar_event(event_start_time=start, event_end_time=end))

        assert ok is False
        assert missing in data["error"].lower()
        assert http.requests == []

    async def test_all_day_event_uses_the_dates_the_user_gave(self, cal, http) -> None:
        # Midnight in India is the previous evening in UTC; the event must still land on the 30th.
        http.on("POST", EVENTS, created_event(start={"date": "2026-09-30"}, end={"date": "2026-10-01"}))

        ok, data = result(await cal.create_calendar_event(
            event_start_time="2026-09-30T00:00:00+05:30", event_end_time="2026-10-01T00:00:00+05:30", event_all_day=True,
        ))

        assert ok is True
        body = http.calls("POST", EVENTS)[0].body
        assert body["start"] == {"date": "2026-09-30"}
        assert body["end"] == {"date": "2026-10-01"}
        assert data["event_start_time"] == "2026-09-30"
        assert data["event_end_time"] == "2026-10-01"
        assert data["event_all_day"] is True

    async def test_all_day_event_from_timestamps_uses_the_event_timezone_dates(self, cal, http) -> None:
        # Midnight in India is 18:30 the previous day in UTC; the event must land on the 30th.
        http.on("POST", EVENTS, created_event(start={"date": "2026-09-30"}, end={"date": "2026-10-01"}))
        start = int(instant("2026-09-30T00:00:00+05:30").timestamp())
        end = int(instant("2026-10-01T00:00:00+05:30").timestamp())

        ok, _ = result(await cal.create_calendar_event(
            event_start_time=str(start), event_end_time=str(end), event_all_day=True, event_timezone="Asia/Kolkata",
        ))

        assert ok is True
        body = http.calls("POST", EVENTS)[0].body
        assert body["start"] == {"date": "2026-09-30"}
        assert body["end"] == {"date": "2026-10-01"}

    async def test_single_day_all_day_event_ends_the_next_day(self, cal, http) -> None:
        # Google's all-day end date is exclusive; start == end would be rejected as an empty range.
        http.on("POST", EVENTS, created_event(start={"date": "2026-09-30"}, end={"date": "2026-10-01"}))

        ok, _ = result(await cal.create_calendar_event(event_start_time="2026-09-30", event_end_time="2026-09-30", event_all_day=True))

        assert ok is True
        body = http.calls("POST", EVENTS)[0].body
        assert body["start"] == {"date": "2026-09-30"}
        assert body["end"] == {"date": "2026-10-01"}

    async def test_google_refusal_is_reported_as_failure(self, cal, http) -> None:
        http.on("POST", EVENTS, google_error(403, "You need to have writer access to this calendar.", "requiredAccessLevel"))

        ok, data = result(await cal.create_calendar_event(event_start_time="2026-09-30T10:00:00Z", event_end_time="2026-09-30T11:00:00Z"))

        assert ok is False
        assert "writer access" in assert_safe_error(data)

    async def test_created_event_without_meeting_entry_points_is_still_reported_as_created(self, cal, http) -> None:
        # The event exists at this point; reporting failure would make the agent create it again.
        http.on("POST", EVENTS, created_event(conferenceData={"entryPoints": []}))

        ok, data = result(await cal.create_calendar_event(event_start_time="2026-09-30T10:00:00Z", event_end_time="2026-09-30T11:00:00Z"))

        assert ok is True
        assert data["event_id"] == "evt-1"
        assert data["event_meeting_link"] == ""

    @pytest.mark.xfail(strict=True, reason=(
        "A meeting link passed by the agent is sent as a Meet createRequest id without "
        "conferenceDataVersion=1, so Google drops it and the event has no link. Fixing it means "
        "choosing between storing the given URL or generating a Meet room; left for a product call."
    ))
    async def test_meeting_link_given_by_the_agent_ends_up_on_the_event(self, cal, http) -> None:
        http.on("POST", EVENTS, created_event())

        await cal.create_calendar_event(
            event_start_time="2026-09-30T10:00:00Z", event_end_time="2026-09-30T11:00:00Z",
            event_meeting_link="https://zoom.us/j/123",
        )

        post = http.calls("POST", EVENTS)[0]
        assert post.query.get("conferenceDataVersion") == "1" or "https://zoom.us/j/123" in str(post.body.get("location"))

    def test_attendee_objects_from_an_earlier_tool_are_reduced_to_emails(self) -> None:
        parsed = CreateCalendarEventInput(
            event_start_time="s", event_end_time="e",
            event_attendees_emails=[{"email": "a@example.com", "responseStatus": "accepted"}, {"emailAddress": "b@example.com"}, "c@example.com"],
        )
        assert parsed.event_attendees_emails == ["a@example.com", "b@example.com", "c@example.com"]

    @pytest.mark.parametrize("raw, expected", [
        ("['a@example.com', 'b@example.com']", ["a@example.com", "b@example.com"]),
        ("a@example.com", ["a@example.com"]),
        ([{"responseStatus": "accepted"}], None),
        (None, None),
    ])
    def test_attendee_strings_are_coerced(self, raw, expected) -> None:
        assert CreateCalendarEventInput(event_start_time="s", event_end_time="e", event_attendees_emails=raw).event_attendees_emails == expected


# ---------------------------------------------------------------------------
# update_calendar_event
# ---------------------------------------------------------------------------


class TestUpdateCalendarEvent:
    async def test_reads_then_writes_back_the_merged_event_with_notifications(self, cal, http) -> None:
        http.on("GET", f"{EVENTS}/evt-1", created_event(description="old"))
        http.on("PUT", f"{EVENTS}/evt-1", created_event(summary="Renamed"))

        ok, data = result(await cal.update_calendar_event(
            event_id="evt-1", event_title="Renamed", event_location="Room 2", event_attendees_emails=["z@example.com"],
        ))

        assert ok is True
        put = http.calls("PUT", f"{EVENTS}/evt-1")[0]
        assert put.query["sendUpdates"] == "all"
        assert put.body["summary"] == "Renamed"
        assert put.body["description"] == "old"
        assert put.body["location"] == "Room 2"
        assert put.body["attendees"] == [{"email": "z@example.com"}]
        assert put.body["start"] == {"dateTime": "2026-09-30T10:00:00Z"}
        assert data["success"] is True and data["event_title"] == "Renamed"

    async def test_description_organizer_and_link_are_written_back(self, cal, http) -> None:
        http.on("GET", f"{EVENTS}/evt-1", created_event())
        http.on("PUT", f"{EVENTS}/evt-1", created_event())

        ok, _ = result(await cal.update_calendar_event(
            event_id="evt-1", event_description="New agenda", event_organizer="boss@example.com",
            event_meeting_link="https://meet.google.com/xyz",
        ))

        assert ok is True
        body = http.calls("PUT")[0].body
        assert body["description"] == "New agenda"
        assert body["organizer"] == {"email": "boss@example.com"}
        assert body["conferenceData"]["entryPoints"][0]["uri"] == "https://meet.google.com/xyz"

    async def test_moving_an_event_reads_local_times_in_the_requested_timezone(self, cal, http) -> None:
        http.on("GET", f"{EVENTS}/evt-1", created_event())
        http.on("PUT", f"{EVENTS}/evt-1", created_event())

        ok, _ = result(await cal.update_calendar_event(
            event_id="evt-1", event_start_time="2026-10-01T15:00:00", event_end_time="2026-10-01T16:00:00",
            event_timezone="America/New_York",
        ))

        assert ok is True
        body = http.calls("PUT")[0].body
        assert instant(body["start"]["dateTime"]) == instant("2026-10-01T15:00:00-04:00")
        assert body["start"]["timeZone"] == "America/New_York"

    @pytest.mark.parametrize("times", [
        {"event_start_time": "2026-10-01T15:00:00Z"},
        {"event_end_time": "2026-10-01T16:00:00Z"},
    ])
    async def test_half_a_new_time_is_refused_instead_of_silently_ignored(self, cal, http, times) -> None:
        http.on("GET", f"{EVENTS}/evt-1", created_event())
        http.on("PUT", f"{EVENTS}/evt-1", created_event())

        ok, data = result(await cal.update_calendar_event(event_id="evt-1", **times))

        assert ok is False
        assert "both" in assert_safe_error(data).lower()
        assert http.calls("PUT") == []

    async def test_unknown_event_says_how_to_find_the_right_id(self, cal, http) -> None:
        http.on("GET", f"{EVENTS}/nope", google_error(404, "Not Found", "notFound"))

        ok, data = result(await cal.update_calendar_event(event_id="nope", event_title="x"))

        assert ok is False
        message = assert_safe_error(data)
        assert "could not find" in message.lower() and "get_calendar_events" in message
        assert http.calls("PUT") == []

    async def test_non_organizer_refusal_is_reported(self, cal, http) -> None:
        http.on("GET", f"{EVENTS}/evt-1", created_event())
        http.on("PUT", f"{EVENTS}/evt-1", google_error(403, "Only the organizer can change this event.", "forbiddenForNonOrganizer"))

        ok, data = result(await cal.update_calendar_event(event_id="evt-1", event_title="x"))

        assert ok is False
        assert "organizer" in assert_safe_error(data)


# ---------------------------------------------------------------------------
# create_meet_link
# ---------------------------------------------------------------------------


class TestCreateMeetLink:
    async def test_asks_google_to_generate_a_meet_room_and_returns_its_link(self, cal, http) -> None:
        http.on("POST", EVENTS, created_event(summary="Meeting", conferenceData={"entryPoints": [
            {"entryPointType": "phone", "uri": "tel:+1"}, {"entryPointType": "video", "uri": "https://meet.google.com/abc"},
        ]}))

        ok, data = result(await cal.create_meet_link(event_start_time="2026-09-30T10:00:00Z", event_end_time="2026-09-30T11:00:00Z"))

        assert ok is True
        post = http.calls("POST", EVENTS)[0]
        assert post.query["conferenceDataVersion"] == "1"
        assert post.query["sendUpdates"] == "all"
        assert post.body["summary"] == "Meeting"
        assert post.body["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
        assert data["meet_link"] == "https://meet.google.com/abc"

    async def test_falls_back_to_hangout_link(self, cal, http) -> None:
        http.on("POST", EVENTS, created_event(hangoutLink="https://meet.google.com/old"))

        _, data = result(await cal.create_meet_link(event_start_time="2026-09-30T10:00:00Z", event_end_time="2026-09-30T11:00:00Z"))

        assert data["meet_link"] == "https://meet.google.com/old"

    async def test_local_time_is_read_in_the_requested_timezone(self, cal, http) -> None:
        http.on("POST", EVENTS, created_event())

        await cal.create_meet_link(event_start_time="2026-09-30T10:00:00", event_end_time="2026-09-30T10:30:00", event_timezone="Europe/Berlin")

        body = http.calls("POST", EVENTS)[0].body
        assert instant(body["start"]["dateTime"]) == instant("2026-09-30T10:00:00+02:00")
        assert body["start"]["timeZone"] == "Europe/Berlin"

    @pytest.mark.parametrize("start, end", [("", "2026-09-30T11:00:00Z"), ("2026-09-30T10:00:00Z", "")])
    async def test_missing_time_is_refused(self, cal, http, start, end) -> None:
        ok, _ = result(await cal.create_meet_link(event_start_time=start, event_end_time=end))

        assert ok is False
        assert http.requests == []

    async def test_rate_limit_is_reported_as_failure(self, cal, http) -> None:
        http.on("POST", EVENTS, google_error(429, "Rate Limit Exceeded", "rateLimitExceeded"))

        ok, data = result(await cal.create_meet_link(event_start_time="2026-09-30T10:00:00Z", event_end_time="2026-09-30T11:00:00Z"))

        assert ok is False
        assert "too many requests" in assert_safe_error(data).lower()


# ---------------------------------------------------------------------------
# delete, calendar list
# ---------------------------------------------------------------------------


class TestDeleteCalendarEvent:
    async def test_deletes_from_primary_calendar(self, cal, http) -> None:
        http.on("DELETE", f"{EVENTS}/evt-1", GoogleResponse(204))

        ok, data = result(await cal.delete_calendar_event(event_id="evt-1"))

        assert ok is True
        assert "evt-1" in data["message"]
        assert len(http.calls("DELETE", f"{EVENTS}/evt-1")) == 1

    async def test_already_deleted_event_is_explained(self, cal, http) -> None:
        http.on("DELETE", f"{EVENTS}/evt-1", google_error(410, "Resource has been deleted", "deleted"))

        ok, data = result(await cal.delete_calendar_event(event_id="evt-1"))

        assert ok is False
        assert "already been deleted" in assert_safe_error(data)


class TestCalendarList:
    async def test_lists_the_users_calendars(self, cal, http) -> None:
        http.on("GET", "/users/me/calendarList", {"items": [{"id": "primary", "summary": "Me"}]})

        ok, data = result(await cal.get_calendar_list())

        assert ok is True
        assert data["items"][0]["summary"] == "Me"

    async def test_gets_one_calendar_defaulting_to_primary(self, cal, http) -> None:
        http.on("GET", "/calendars/primary", {"id": "primary", "timeZone": "UTC"})

        ok, data = result(await cal.get_calendar_list_by_id())

        assert ok is True
        assert data["timeZone"] == "UTC"

    async def test_list_failure_is_reported(self, cal, http) -> None:
        http.on("GET", "/users/me/calendarList", google_error(500, "Backend Error", "backendError"))

        ok, data = result(await cal.get_calendar_list())

        assert ok is False
        assert_safe_error(data)

    async def test_unknown_calendar_is_reported(self, cal, http) -> None:
        http.on("GET", "/calendars/missing", google_error(404, "Not Found", "notFound"))

        ok, data = result(await cal.get_calendar_list_by_id(calendar_id="missing"))

        assert ok is False
        assert "could not find" in assert_safe_error(data).lower()

    async def test_signed_in_user_without_refresh_token_is_asked_to_reconnect(self, cal, http) -> None:
        # A 401 makes the auth layer try a token refresh, which fails without a refresh token.
        http.on("GET", "/calendars/primary", google_error(401, "Invalid Credentials", "authError"))

        ok, data = result(await cal.get_calendar_list_by_id())

        assert ok is False
        assert "reconnect" in assert_safe_error(data).lower()
