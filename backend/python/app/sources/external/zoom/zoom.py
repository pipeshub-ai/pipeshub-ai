from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.zoom.zoom import ZoomClient


class ZoomDataSource:
    """Zoom API client wrapper.
    - Uses HTTP client passed as `ZoomClient`
    - Provides methods for common Zoom API operations
    - All methods return HTTPResponse objects
    """

    def __init__(self, client: ZoomClient) -> None:
        """Initialize with ZoomClient."""
        self._client = client.get_http_client()
        try:
            self.base_url = self._client.get_base_url().rstrip("/")  # type: ignore[valid-method]
        except AttributeError as exc:
            raise ValueError("HTTP client does not have get_base_url method") from exc

    def get_data_source(self) -> "ZoomDataSource":
        """Get the data source instance."""
        return self

    # ========================================================================
    # USER APIs
    # ========================================================================

    async def list_users(
        self,
        status: str | None = None,
        page_size: int | None = None,
        role_id: str | None = None,
        page_number: int | None = None,
        include_fields: str | None = None,
        next_page_token: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List users on your account
        HTTP GET /users

        Args:
            status: User status (active, inactive, pending)
            page_size: Number of records returned per page
            role_id: Unique identifier for the role
            page_number: Page number of results
            include_fields: Additional fields to include
            next_page_token: Next page token for pagination
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if status is not None:
            _query["status"] = status
        if page_size is not None:
            _query["page_size"] = page_size
        if role_id is not None:
            _query["role_id"] = role_id
        if page_number is not None:
            _query["page_number"] = page_number
        if include_fields is not None:
            _query["include_fields"] = include_fields
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        _body = None
        rel_path = "/users"
        url = self.base_url + rel_path
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_user(
        self,
        user_id: str,
        login_type: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get a user's information
        HTTP GET /users/{userId}

        Args:
            user_id: The user ID or email address
            login_type: User's login method (0: Facebook, 1: Google, 99: API, 100: Zoom, 101: SSO)
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if login_type is not None:
            _query["login_type"] = login_type
        _body = None
        rel_path = "/users/{userId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_user(
        self,
        action: str,
        user_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Create a user
        HTTP POST /users

        Args:
            action: Action to take (create, autoCreate, custCreate, ssoCreate)
            user_info: User information dict with fields like email, type, first_name, last_name, etc.
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        _body: dict[str, Any] = {"action": action, **user_info}
        rel_path = "/users"
        url = self.base_url + rel_path
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # MEETING APIs
    # ========================================================================

    async def list_meetings(
        self,
        user_id: str,
        type: str | None = None,
        page_size: int | None = None,
        next_page_token: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List all meetings for a user
        HTTP GET /users/{userId}/meetings

        Args:
            user_id: The user ID or email address
            type: Meeting type (scheduled, live, upcoming, previous)
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            from_date: Start date in yyyy-MM-dd format
            to_date: End date in yyyy-MM-dd format
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if type is not None:
            _query["type"] = type
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        if from_date is not None:
            _query["from"] = from_date
        if to_date is not None:
            _query["to"] = to_date
        _body = None
        rel_path = "/users/{userId}/meetings"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_meeting(
        self,
        user_id: str,
        meeting_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Create a meeting
        HTTP POST /users/{userId}/meetings

        Args:
            user_id: The user ID or email address
            meeting_info: Meeting information dict with fields like topic, type, start_time, duration, etc.
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        _body = meeting_info
        rel_path = "/users/{userId}/meetings"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_meeting(
        self,
        meeting_id: str,
        occurrence_id: str | None = None,
        show_previous_occurrences: bool | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get meeting details
        HTTP GET /meetings/{meetingId}

        Args:
            meeting_id: The meeting ID
            occurrence_id: Meeting occurrence ID
            show_previous_occurrences: Show previous occurrences
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"meetingId": meeting_id}
        _query: dict[str, Any] = {}
        if occurrence_id is not None:
            _query["occurrence_id"] = occurrence_id
        if show_previous_occurrences is not None:
            _query["show_previous_occurrences"] = show_previous_occurrences
        _body = None
        rel_path = "/meetings/{meetingId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def update_meeting(
        self,
        meeting_id: str,
        meeting_info: dict[str, Any],
        occurrence_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Update meeting details
        HTTP PATCH /meetings/{meetingId}

        Args:
            meeting_id: The meeting ID
            meeting_info: Meeting information to update
            occurrence_id: Meeting occurrence ID
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {"meetingId": meeting_id}
        _query: dict[str, Any] = {}
        if occurrence_id is not None:
            _query["occurrence_id"] = occurrence_id
        _body = meeting_info
        rel_path = "/meetings/{meetingId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="PATCH",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def delete_meeting(
        self,
        meeting_id: str,
        occurrence_id: str | None = None,
        schedule_for_reminder: bool | None = None,
        cancel_meeting_reminder: bool | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Delete a meeting
        HTTP DELETE /meetings/{meetingId}

        Args:
            meeting_id: The meeting ID
            occurrence_id: Meeting occurrence ID
            schedule_for_reminder: Schedule for reminder
            cancel_meeting_reminder: Cancel meeting reminder
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"meetingId": meeting_id}
        _query: dict[str, Any] = {}
        if occurrence_id is not None:
            _query["occurrence_id"] = occurrence_id
        if schedule_for_reminder is not None:
            _query["schedule_for_reminder"] = schedule_for_reminder
        if cancel_meeting_reminder is not None:
            _query["cancel_meeting_reminder"] = cancel_meeting_reminder
        _body = None
        rel_path = "/meetings/{meetingId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="DELETE",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # WEBINAR APIs
    # ========================================================================

    async def list_webinars(
        self,
        user_id: str,
        page_size: int | None = None,
        next_page_token: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List all webinars for a user
        HTTP GET /users/{userId}/webinars

        Args:
            user_id: The user ID or email address
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            from_date: Start date in yyyy-MM-dd format
            to_date: End date in yyyy-MM-dd format
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        if from_date is not None:
            _query["from"] = from_date
        if to_date is not None:
            _query["to"] = to_date
        _body = None
        rel_path = "/users/{userId}/webinars"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_webinar(
        self,
        user_id: str,
        webinar_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Create a webinar
        HTTP POST /users/{userId}/webinars

        Args:
            user_id: The user ID or email address
            webinar_info: Webinar information dict
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        _body = webinar_info
        rel_path = "/users/{userId}/webinars"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_webinar(
        self,
        webinar_id: str,
        occurrence_id: str | None = None,
        show_previous_occurrences: bool | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get webinar details
        HTTP GET /webinars/{webinarId}

        Args:
            webinar_id: The webinar ID
            occurrence_id: Webinar occurrence ID
            show_previous_occurrences: Show previous occurrences
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"webinarId": webinar_id}
        _query: dict[str, Any] = {}
        if occurrence_id is not None:
            _query["occurrence_id"] = occurrence_id
        if show_previous_occurrences is not None:
            _query["show_previous_occurrences"] = show_previous_occurrences
        _body = None
        rel_path = "/webinars/{webinarId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # TEAM CHAT APIs
    # ========================================================================

    async def list_chat_channels(
        self,
        user_id: str,
        page_size: int | None = None,
        next_page_token: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List user's chat channels
        HTTP GET /chat/users/{userId}/channels

        Args:
            user_id: The user ID
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        _body = None
        rel_path = "/chat/users/{userId}/channels"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def send_chat_message(
        self,
        user_id: str,
        message_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Send a chat message
        HTTP POST /chat/users/{userId}/messages

        Args:
            user_id: The user ID
            message_info: Message information dict
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        _body = message_info
        rel_path = "/chat/users/{userId}/messages"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # PHONE APIs
    # ========================================================================

    async def list_phone_users(
        self,
        page_size: int | None = None,
        next_page_token: str | None = None,
        site_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List phone users
        HTTP GET /phone/users

        Args:
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            site_id: Unique identifier of the site
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        if site_id is not None:
            _query["site_id"] = site_id
        _body = None
        rel_path = "/phone/users"
        url = self.base_url + rel_path
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_phone_user(
        self,
        user_id: str,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get phone user details
        HTTP GET /phone/users/{userId}

        Args:
            user_id: The user ID
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        _body = None
        rel_path = "/phone/users/{userId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # MAIL APIs
    # ========================================================================

    async def list_mail_messages(
        self,
        user_id: str,
        page_size: int | None = None,
        next_page_token: str | None = None,
        folder_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List mail messages
        HTTP GET /mail/users/{userId}/messages

        Args:
            user_id: The user ID
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            folder_id: Folder ID to filter messages
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        if folder_id is not None:
            _query["folder_id"] = folder_id
        _body = None
        rel_path = "/mail/users/{userId}/messages"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def send_mail_message(
        self,
        user_id: str,
        message_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Send a mail message
        HTTP POST /mail/users/{userId}/messages

        Args:
            user_id: The user ID
            message_info: Message information dict
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        _body = message_info
        rel_path = "/mail/users/{userId}/messages"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # CALENDAR APIs
    # ========================================================================

    async def list_calendar_events(
        self,
        user_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
        page_size: int | None = None,
        next_page_token: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List calendar events
        HTTP GET /calendar/users/{userId}/events

        Args:
            user_id: The user ID
            from_date: Start date in yyyy-MM-dd format
            to_date: End date in yyyy-MM-dd format
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if from_date is not None:
            _query["from"] = from_date
        if to_date is not None:
            _query["to"] = to_date
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        _body = None
        rel_path = "/calendar/users/{userId}/events"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_calendar_event(
        self,
        user_id: str,
        event_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Create a calendar event
        HTTP POST /calendar/users/{userId}/events

        Args:
            user_id: The user ID
            event_info: Event information dict
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        _body = event_info
        rel_path = "/calendar/users/{userId}/events"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # SCHEDULER APIs
    # ========================================================================

    async def list_scheduler_availability(
        self,
        user_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List scheduler availability
        HTTP GET /scheduler/users/{userId}/availability

        Args:
            user_id: The user ID
            from_date: Start date in yyyy-MM-dd format
            to_date: End date in yyyy-MM-dd format
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if from_date is not None:
            _query["from"] = from_date
        if to_date is not None:
            _query["to"] = to_date
        _body = None
        rel_path = "/scheduler/users/{userId}/availability"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_scheduler_booking(
        self,
        user_id: str,
        booking_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Create a scheduler booking
        HTTP POST /scheduler/users/{userId}/bookings

        Args:
            user_id: The user ID
            booking_info: Booking information dict
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        _body = booking_info
        rel_path = "/scheduler/users/{userId}/bookings"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # ROOMS APIs
    # ========================================================================

    async def list_rooms(
        self,
        page_size: int | None = None,
        next_page_token: str | None = None,
        location_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List Zoom Rooms
        HTTP GET /rooms

        Args:
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            location_id: Location ID to filter rooms
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        if location_id is not None:
            _query["location_id"] = location_id
        _body = None
        rel_path = "/rooms"
        url = self.base_url + rel_path
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_room(
        self,
        room_id: str,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get Zoom Room details
        HTTP GET /rooms/{roomId}

        Args:
            room_id: The room ID
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"roomId": room_id}
        _query: dict[str, Any] = {}
        _body = None
        rel_path = "/rooms/{roomId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # CLIPS APIs
    # ========================================================================

    async def list_clips(
        self,
        user_id: str,
        page_size: int | None = None,
        next_page_token: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List user's clips
        HTTP GET /clips/users/{userId}

        Args:
            user_id: The user ID
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        _body = None
        rel_path = "/clips/users/{userId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_clip(
        self,
        clip_id: str,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get clip details
        HTTP GET /clips/{clipId}

        Args:
            clip_id: The clip ID
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"clipId": clip_id}
        _query: dict[str, Any] = {}
        _body = None
        rel_path = "/clips/{clipId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # WHITEBOARD APIs
    # ========================================================================

    async def list_whiteboards(
        self,
        user_id: str,
        page_size: int | None = None,
        next_page_token: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List user's whiteboards
        HTTP GET /whiteboard/users/{userId}

        Args:
            user_id: The user ID
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        _body = None
        rel_path = "/whiteboard/users/{userId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_whiteboard(
        self,
        user_id: str,
        whiteboard_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Create a whiteboard
        HTTP POST /whiteboard/users/{userId}

        Args:
            user_id: The user ID
            whiteboard_info: Whiteboard information dict
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        _body = whiteboard_info
        rel_path = "/whiteboard/users/{userId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # CALL RECORDING (CRC) APIs
    # ========================================================================

    async def list_call_recordings(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        page_size: int | None = None,
        next_page_token: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List call recordings
        HTTP GET /phone/call_logs

        Args:
            from_date: Start date in yyyy-MM-dd format
            to_date: End date in yyyy-MM-dd format
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if from_date is not None:
            _query["from"] = from_date
        if to_date is not None:
            _query["to"] = to_date
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        _body = None
        rel_path = "/phone/call_logs"
        url = self.base_url + rel_path
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_meeting_recordings(
        self,
        meeting_id: str,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get meeting recordings
        HTTP GET /meetings/{meetingId}/recordings

        Args:
            meeting_id: The meeting ID
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"meetingId": meeting_id}
        _query: dict[str, Any] = {}
        _body = None
        rel_path = "/meetings/{meetingId}/recordings"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # CHATBOT APIs
    # ========================================================================

    async def list_chatbots(
        self,
        page_size: int | None = None,
        next_page_token: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List chatbots
        HTTP GET /chatbots

        Args:
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        _body = None
        rel_path = "/chatbots"
        url = self.base_url + rel_path
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def send_chatbot_message(
        self,
        chatbot_id: str,
        message_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Send a chatbot message
        HTTP POST /chatbots/{chatbotId}/messages

        Args:
            chatbot_id: The chatbot ID
            message_info: Message information dict
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {"chatbotId": chatbot_id}
        _query: dict[str, Any] = {}
        _body = message_info
        rel_path = "/chatbots/{chatbotId}/messages"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # AI COMPANION APIs
    # ========================================================================

    async def get_ai_companion_summary(
        self,
        meeting_id: str,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get AI Companion meeting summary
        HTTP GET /meetings/{meetingId}/ai/summary

        Args:
            meeting_id: The meeting ID
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"meetingId": meeting_id}
        _query: dict[str, Any] = {}
        _body = None
        rel_path = "/meetings/{meetingId}/ai/summary"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_ai_companion_insights(
        self,
        user_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get AI Companion insights
        HTTP GET /users/{userId}/ai/insights

        Args:
            user_id: The user ID
            from_date: Start date in yyyy-MM-dd format
            to_date: End date in yyyy-MM-dd format
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if from_date is not None:
            _query["from"] = from_date
        if to_date is not None:
            _query["to"] = to_date
        _body = None
        rel_path = "/users/{userId}/ai/insights"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # ZOOM DOCS APIs
    # ========================================================================

    async def list_documents(
        self,
        user_id: str,
        page_size: int | None = None,
        next_page_token: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List user's documents
        HTTP GET /docs/users/{userId}

        Args:
            user_id: The user ID
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        _body = None
        rel_path = "/docs/users/{userId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_document(
        self,
        user_id: str,
        document_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Create a document
        HTTP POST /docs/users/{userId}

        Args:
            user_id: The user ID
            document_info: Document information dict
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _path: dict[str, Any] = {"userId": user_id}
        _query: dict[str, Any] = {}
        _body = document_info
        rel_path = "/docs/users/{userId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # ACCOUNTS APIs
    # ========================================================================

    async def list_accounts(
        self,
        page_size: int | None = None,
        next_page_token: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List accounts
        HTTP GET /accounts

        Args:
            page_size: Number of records returned per page
            next_page_token: Next page token for pagination
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["page_size"] = page_size
        if next_page_token is not None:
            _query["next_page_token"] = next_page_token
        _body = None
        rel_path = "/accounts"
        url = self.base_url + rel_path
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_account(
        self,
        account_id: str,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get account details
        HTTP GET /accounts/{accountId}

        Args:
            account_id: The account ID
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {"accountId": account_id}
        _query: dict[str, Any] = {}
        _body = None
        rel_path = "/accounts/{accountId}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # SCIM 2 APIs
    # ========================================================================

    async def list_scim_users(
        self,
        page_size: int | None = None,
        start_index: int | None = None,
        filter: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """List SCIM users
        HTTP GET /scim/v2/Users

        Args:
            page_size: Number of records returned per page
            start_index: Starting index for pagination
            filter: SCIM filter expression
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if page_size is not None:
            _query["count"] = page_size
        if start_index is not None:
            _query["startIndex"] = start_index
        if filter is not None:
            _query["filter"] = filter
        _body = None
        rel_path = "/scim/v2/Users"
        url = self.base_url + rel_path
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def create_scim_user(
        self,
        user_info: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Create a SCIM user
        HTTP POST /scim/v2/Users

        Args:
            user_info: SCIM user information dict
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/scim+json")
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        _body = user_info
        rel_path = "/scim/v2/Users"
        url = self.base_url + rel_path
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    # ========================================================================
    # QSS (Quality Service Score) APIs
    # ========================================================================

    async def get_qss_report(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get QSS report
        HTTP GET /report/quality

        Args:
            from_date: Start date in yyyy-MM-dd format
            to_date: End date in yyyy-MM-dd format
            headers: Optional additional headers

        Returns:
            HTTPResponse

        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if from_date is not None:
            _query["from"] = from_date
        if to_date is not None:
            _query["to"] = to_date
        _body = None
        rel_path = "/report/quality"
        url = self.base_url + rel_path
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp


# ---- Helpers used by generated methods ----
def _safe_format_url(template: str, params: dict[str, object]) -> str:
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    try:
        return template.format_map(_SafeDict(params))
    except Exception:
        return template


def _to_bool_str(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _serialize_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple, set)):
        return ",".join(_to_bool_str(x) for x in v)
    return _to_bool_str(v)


def _as_str_dict(d: dict[str, Any]) -> dict[str, str]:
    return {str(k): _serialize_value(v) for k, v in (d or {}).items()}
