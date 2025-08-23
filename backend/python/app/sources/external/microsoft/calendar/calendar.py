    # Auto-generated Microsoft Calendar API client.
    # ruff: noqa: BLE001, D417, E501, ANN003, PGH003, PLR0912, C901, PLR0913, G004, TRY400, TRY003, EM101, D100, INP001, PLR0915, D401

import keyword as _kw
import logging
import re
from collections.abc import Mapping

from app.sources.client.microsoft.microsoft import MSGraphClient, MSGraphResponse

# Set up logger
logger = logging.getLogger(__name__)

class CalendarDataSource:
    """Auto-generated Microsoft Calendar API client wrapper.

    - Uses Microsoft Graph SDK client internally
    - Snake_case method names for all Microsoft Calendar API operations
    - Standardized MSGraphResponse format for all responses
    - No direct HTTP calls - all requests go through Graph SDK
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize with Microsoft Graph SDK client."""
        self.client = client.get_client()
        if not hasattr(self.client, "me"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Microsoft Calendar API client initialized successfully")

    def _sanitize_py_name(self, name: str) -> str:
        """Runtime-safe version of sanitize_py_name for mirroring param keys.

        Turns '$select' -> 'dollar_select', 'message-id' -> 'message_id', etc.
        """
        n = name.replace("$", "dollar_").replace("@", "at_").replace(".", "_")
        n = re.sub(r"[^0-9a-zA-Z_]", "_", n)
        if n and n[0].isdigit():
            n = f"_{n}"
        if _kw.iskeyword(n):
            n += "_"
        if n.startswith("__"):
            n = f"_{n}"
        return n

    def _handle_response(self, response: dict[str, str | int | bool | list[object]]) -> MSGraphResponse:
        """Handle Microsoft Graph API response."""
        try:
            if response is None:
                    return MSGraphResponse(success=False, error="Empty response from Microsoft Graph")

            success = True
            error_msg = None

            # Handle error responses
            if hasattr(response, "error"):
                success = False
                error_msg = str(response.error) #type:ignore
            elif isinstance(response, dict) and "error" in response:
                success = False
                error_msg = str(response["error"])
            elif hasattr(response, "code") and hasattr(response, "message"):
                success = False
                error_msg = f"{response.code}: {response.message}" #type:ignore

            return MSGraphResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling Microsoft Calendar API response: {e}")
            return MSGraphResponse(success=False, error=str(e))

    def get_data_source(self) -> "CalendarDataSource":
        """Get the underlying Microsoft Calendar API client."""
        return self


    def me_create_calendar_groups(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create CalendarGroup.

        Microsoft Calendar API method: /me/calendarGroups (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_create_calendars(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create calendar.

        Microsoft Calendar API method: /me/calendars (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_create_events(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create event.

        Microsoft Calendar API method: /me/events (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_delete_calendar_groups(self,
        *,
        calendar_group_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete calendarGroup.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id} (DELETE).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_delete_calendars(self,
        *,
        calendar_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property calendars for me.

        Microsoft Calendar API method: /me/calendars/{calendar-id} (DELETE).

        Args:
            calendar_id (required): The unique identifier of calendar
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_delete_events(self,
        *,
        event_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete event.

        Microsoft Calendar API method: /me/events/{event-id} (DELETE).

        Args:
            event_id (required): The unique identifier of event
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_get_calendar(self,
        *,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendar.

        Microsoft Calendar API method: /me/calendar (GET).

        Args:
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_get_calendar_groups(self,
        *,
        calendar_group_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarGroup.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id} (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_get_calendars(self,
        *,
        calendar_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendars from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id} (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_get_events(self,
        *,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get event.

        Microsoft Calendar API method: /me/events/{event-id} (GET).

        Args:
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_list_calendar_groups(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List calendarGroups.

        Microsoft Calendar API method: /me/calendarGroups (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_list_calendar_view(self,
        *,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List calendarView.

        Microsoft Calendar API method: /me/calendarView (GET).

        Args:
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_view()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_list_calendars(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List calendars.

        Microsoft Calendar API method: /me/calendars (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_list_events(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List events.

        Microsoft Calendar API method: /me/events (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_update_calendar(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Update calendar.

        Microsoft Calendar API method: /me/calendar (PATCH).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar()  # type:ignore
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_update_calendar_groups(self,
        *,
        calendar_group_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update calendargroup.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id} (PATCH).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_update_calendars(self,
        *,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendars in me.

        Microsoft Calendar API method: /me/calendars/{calendar-id} (PATCH).

        Args:
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_update_events(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update event.

        Microsoft Calendar API method: /me/events/{event-id} (PATCH).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_create_calendar_permissions(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create calendarPermission.

        Microsoft Calendar API method: /me/calendar/calendarPermissions (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar()  # type:ignore
            .calendar_permissions().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_create_events(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Create new navigation property to events for me.

        Microsoft Calendar API method: /me/calendar/events (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_delete_calendar_permissions(self,
        *,
        calendar_permission_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property calendarPermissions for me.

        Microsoft Calendar API method: /me/calendar/calendarPermissions/{calendarPermission-id} (DELETE).

        Args:
            calendar_permission_id (required): The unique identifier of calendarPermission
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar()  # type:ignore
            .calendar_permissions().by_id(params.get("calendarPermission_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_delete_events(self,
        *,
        event_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property events for me.

        Microsoft Calendar API method: /me/calendar/events/{event-id} (DELETE).

        Args:
            event_id (required): The unique identifier of event
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_get_calendar_permissions(self,
        *,
        calendar_permission_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermissions from me.

        Microsoft Calendar API method: /me/calendar/calendarPermissions/{calendarPermission-id} (GET).

        Args:
            calendar_permission_id (required): The unique identifier of calendarPermission
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar()  # type:ignore
            .calendar_permissions().by_id(params.get("calendarPermission_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_get_events(self,
        *,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from me.

        Microsoft Calendar API method: /me/calendar/events/{event-id} (GET).

        Args:
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_list_calendar_permissions(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermissions from me.

        Microsoft Calendar API method: /me/calendar/calendarPermissions (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar()  # type:ignore
            .calendar_permissions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_list_calendar_view(self,
        *,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List calendarView.

        Microsoft Calendar API method: /me/calendar/calendarView (GET).

        Args:
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().calendar_view()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_list_events(self,
        *,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List events.

        Microsoft Calendar API method: /me/calendar/events (GET).

        Args:
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_update_calendar_permissions(self,
        *,
        calendar_permission_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendarPermissions in me.

        Microsoft Calendar API method: /me/calendar/calendarPermissions/{calendarPermission-id} (PATCH).

        Args:
            calendar_permission_id (required): The unique identifier of calendarPermission
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar()  # type:ignore
            .calendar_permissions().by_id(params.get("calendarPermission_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_update_events(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property events in me.

        Microsoft Calendar API method: /me/calendar/events/{event-id} (PATCH).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_allowed_calendar_sharing_roles(self,
        *,
        user: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function allowedCalendarSharingRoles.

        Microsoft Calendar API method: /me/calendar/allowedCalendarSharingRoles(User='{User}') (GET).

        Args:
            user (required): Usage: User='{User}'
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user is not None:
            params["User"] = user
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar()  # type:ignore
            .allowed_calendar_sharing_roles(_user="{_user}")().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_calendar_permissions_get_count_7010(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendar/calendarPermissions/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar()  # type:ignore
            .calendar_permissions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_calendar_view_delta(self,
        *,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/calendar/calendarView/delta() (GET).

        Args:
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().calendar_view()  # type:ignore
            .delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_create_attachments(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to attachments for me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/attachments (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).attachments()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_create_extensions(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/extensions (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).extensions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_delete_attachments(self,
        *,
        event_id: str,
        attachment_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property attachments for me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/attachments/{attachment-id} (DELETE).

        Args:
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).attachments().by_id(params
            .get("attachment_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_delete_extensions(self,
        *,
        event_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/extensions/{extension-id} (DELETE).

        Args:
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).extensions().by_id(params
            .get("extension_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_get_attachments(self,
        *,
        event_id: str,
        attachment_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/attachments/{attachment-id} (GET).

        Args:
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).attachments().by_id(params
            .get("attachment_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_get_calendar(self,
        *,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendar from me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/calendar (GET).

        Args:
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).calendar().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_get_count_0c79(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendar/events/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_get_extensions(self,
        *,
        event_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/extensions/{extension-id} (GET).

        Args:
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).extensions().by_id(params
            .get("extension_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_list_attachments(self,
        *,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/attachments (GET).

        Args:
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).attachments().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_list_extensions(self,
        *,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/extensions (GET).

        Args:
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_list_instances(self,
        *,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get instances from me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/instances (GET).

        Args:
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).instances().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_update_extensions(self,
        *,
        event_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in me.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/extensions/{extension-id} (PATCH).

        Args:
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).extensions().by_id(params
            .get("extension_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_attachments_get_count_53a3(self,
        *,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/attachments/$count (GET).

        Args:
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).attachments().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_delta(self,
        *,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/calendar/events/delta() (GET).

        Args:
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events().delta()()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_event_accept(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action accept.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/accept (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).accept()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_event_attachments_create_upload_session(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createUploadSession.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/attachments/createUploadSession (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).attachments().create_upload_session()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_event_cancel(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action cancel.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/cancel (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).cancel()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_event_decline(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action decline.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/decline (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).decline()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_event_dismiss_reminder(self, *, event_id: str, **kwargs) -> MSGraphResponse:
        """Invoke action dismissReminder.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/dismissReminder (POST).

        Args:
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).dismiss_reminder().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_event_forward(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action forward.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/forward (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).forward()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_event_instances_delta(self,
        *,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/instances/delta() (GET).

        Args:
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).instances().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_event_permanent_delete(self, *, event_id: str, **kwargs) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/permanentDelete (POST).

        Args:
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_event_snooze_reminder(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action snoozeReminder.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/snoozeReminder (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).snooze_reminder()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_event_tentatively_accept(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action tentativelyAccept.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/tentativelyAccept (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).tentatively_accept()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_events_extensions_get_count_785d(self,
        *,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendar/events/{event-id}/extensions/$count (GET).

        Args:
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().events()  # type:ignore
            .by_id(params.get("event_id", "")).extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_get_schedule(self, *, request_body: Mapping[str, object] | None = None, **kwargs) -> MSGraphResponse:
        """Invoke action getSchedule.

        Microsoft Calendar API method: /me/calendar/getSchedule (POST).

        Args:
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().get_schedule()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_permanent_delete(self, **kwargs) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /me/calendar/permanentDelete (POST).

        Args:
            (no parameters)

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        # No parameters
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar().permanent_delete()  # type:ignore
            .post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_create_calendars(self,
        *,
        calendar_group_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create Calendar.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_delete_calendars(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property calendars for me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id} (DELETE).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_get_calendars(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendars from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id} (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_get_count_9c6e(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendarGroups/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_list_calendars(self,
        *,
        calendar_group_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List calendars.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_update_calendars(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendars in me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id} (PATCH).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_allowed_calendar_sharing_roles(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        user: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function allowedCalendarSharingRoles.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/allowedCalendarSharingRoles(User='{User}') (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            user (required): Usage: User='{User}'
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if user is not None:
            params["User"] = user
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).allowed_calendar_sharing_roles(_user="{_user}")()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_calendar_view_delta(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarView/delta() (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).calendar_view().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_delta(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/delta() (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_event_accept(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action accept.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/accept (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_event_attachments_create_upload_session(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createUploadSession.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments/createUploadSession (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().create_upload_session()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_event_cancel(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action cancel.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/cancel (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .cancel().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_event_decline(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action decline.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/decline (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .decline().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_event_dismiss_reminder(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action dismissReminder.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/dismissReminder (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .dismiss_reminder().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_event_forward(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action forward.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/forward (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .forward().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_event_instances_delta(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/instances/delta() (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .instances().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_event_permanent_delete(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/permanentDelete (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_event_snooze_reminder(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action snoozeReminder.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/snoozeReminder (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .snooze_reminder().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_events_event_tentatively_accept(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action tentativelyAccept.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/tentativelyAccept (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .tentatively_accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_get_schedule(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action getSchedule.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/getSchedule (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).get_schedule()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendar_group_calendars_calendar_permanent_delete(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/permanentDelete (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_create_calendar_permissions(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to calendarPermissions for me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).calendar_permissions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_create_events(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to events for me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_delete_calendar_permissions(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        calendar_permission_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property calendarPermissions for me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (DELETE).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_delete_events(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property events for me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id} (DELETE).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_get_calendar_permissions(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        calendar_permission_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermissions from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_get_count_9aae(self,
        *,
        calendar_group_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/$count (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_get_events(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id} (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_list_calendar_permissions(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermissions from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).calendar_permissions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_list_calendar_view(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarView from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarView (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).calendar_view().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_list_events(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_update_calendar_permissions(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        calendar_permission_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendarPermissions in me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (PATCH).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_update_events(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property events in me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id} (PATCH).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_calendar_permissions_get_count_3d5b(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions/$count (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).calendar_permissions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_create_attachments(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to attachments for me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_create_extensions(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions (POST).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_delete_attachments(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        attachment_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property attachments for me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments/{attachment-id} (DELETE).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().by_id(params.get("attachment_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_delete_extensions(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (DELETE).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_get_attachments(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        attachment_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments/{attachment-id} (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().by_id(params.get("attachment_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_get_calendar(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendar from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/calendar (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .calendar().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_get_count_4a49(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/$count (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_get_extensions(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_list_attachments(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_list_extensions(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_list_instances(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get instances from me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/instances (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .instances().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_update_extensions(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in me.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (PATCH).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_attachments_get_count_2b84(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments/$count (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_groups_calendars_events_extensions_get_count_baf0(self,
        *,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions/$count (GET).

        Args:
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_groups().by_id(params  # type:ignore
            .get("calendarGroup_id", "")).calendars().by_id(params
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendar_view_delta(self,
        *,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/calendarView/delta() (GET).

        Args:
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_view().delta()()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_create_calendar_permissions(self,
        *,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to calendarPermissions for me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/calendarPermissions (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_create_events(self,
        *,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create event.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_delete_calendar_permissions(self,
        *,
        calendar_id: str,
        calendar_permission_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property calendarPermissions for me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (DELETE).

        Args:
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_delete_events(self,
        *,
        calendar_id: str,
        event_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property events for me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id} (DELETE).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_get_calendar_permissions(self,
        *,
        calendar_id: str,
        calendar_permission_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermissions from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_get_count_669b(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendars/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_get_events(self,
        *,
        calendar_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id} (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_list_calendar_permissions(self,
        *,
        calendar_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermissions from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/calendarPermissions (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_list_calendar_view(self,
        *,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarView from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/calendarView (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_view().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_list_events(self,
        *,
        calendar_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_update_calendar_permissions(self,
        *,
        calendar_id: str,
        calendar_permission_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendarPermissions in me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (PATCH).

        Args:
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_update_events(self,
        *,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property events in me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id} (PATCH).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_allowed_calendar_sharing_roles(self,
        *,
        calendar_id: str,
        user: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function allowedCalendarSharingRoles.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/allowedCalendarSharingRoles(User='{User}') (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            user (required): Usage: User='{User}'
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if user is not None:
            params["User"] = user
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).allowed_calendar_sharing_roles(_user="{_user}")()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_calendar_view_delta(self,
        *,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/calendarView/delta() (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_view().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_delta(self,
        *,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/delta() (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_event_accept(self,
        *,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action accept.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/accept (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_event_attachments_create_upload_session(self,
        *,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createUploadSession.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/attachments/createUploadSession (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().create_upload_session()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_event_cancel(self,
        *,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action cancel.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/cancel (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .cancel().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_event_decline(self,
        *,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action decline.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/decline (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .decline().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_event_dismiss_reminder(self,
        *,
        calendar_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action dismissReminder.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/dismissReminder (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .dismiss_reminder().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_event_forward(self,
        *,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action forward.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/forward (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .forward().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_event_instances_delta(self,
        *,
        calendar_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/instances/delta() (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .instances().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_event_permanent_delete(self,
        *,
        calendar_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/permanentDelete (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_event_snooze_reminder(self,
        *,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action snoozeReminder.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/snoozeReminder (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .snooze_reminder().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_events_event_tentatively_accept(self,
        *,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action tentativelyAccept.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/tentativelyAccept (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .tentatively_accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_get_schedule(self,
        *,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action getSchedule.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/getSchedule (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).get_schedule()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_permanent_delete(self, *, calendar_id: str, **kwargs) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/permanentDelete (POST).

        Args:
            calendar_id (required): The unique identifier of calendar

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_calendar_permissions_get_count_8761(self,
        *,
        calendar_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/calendarPermissions/$count (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_create_attachments(self,
        *,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to attachments for me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/attachments (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_create_extensions(self,
        *,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/extensions (POST).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_delete_attachments(self,
        *,
        calendar_id: str,
        event_id: str,
        attachment_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property attachments for me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/attachments/{attachment-id} (DELETE).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().by_id(params.get("attachment_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_delete_extensions(self,
        *,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (DELETE).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_get_attachments(self,
        *,
        calendar_id: str,
        event_id: str,
        attachment_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/attachments/{attachment-id} (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().by_id(params.get("attachment_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_get_calendar(self,
        *,
        calendar_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendar from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/calendar (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .calendar().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_get_count_0f8c(self,
        *,
        calendar_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/$count (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_get_extensions(self,
        *,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_list_attachments(self,
        *,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/attachments (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_list_extensions(self,
        *,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/extensions (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_list_instances(self,
        *,
        calendar_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get instances from me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/instances (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .instances().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_update_extensions(self,
        *,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in me.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (PATCH).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_attachments_get_count_22f3(self,
        *,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/attachments/$count (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_calendars_events_extensions_get_count_b93c(self,
        *,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/calendars/{calendar-id}/events/{event-id}/extensions/$count (GET).

        Args:
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendars().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_create_attachments(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Add attachment.

        Microsoft Calendar API method: /me/events/{event-id}/attachments (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_create_extensions(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for me.

        Microsoft Calendar API method: /me/events/{event-id}/extensions (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_delete_attachments(self,
        *,
        event_id: str,
        attachment_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete attachment.

        Microsoft Calendar API method: /me/events/{event-id}/attachments/{attachment-id} (DELETE).

        Args:
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().by_id(params.get("attachment_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_delete_extensions(self,
        *,
        event_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for me.

        Microsoft Calendar API method: /me/events/{event-id}/extensions/{extension-id} (DELETE).

        Args:
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().by_id(params.get("extension_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_get_attachments(self,
        *,
        event_id: str,
        attachment_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from me.

        Microsoft Calendar API method: /me/events/{event-id}/attachments/{attachment-id} (GET).

        Args:
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().by_id(params.get("attachment_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_get_calendar(self,
        *,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendar from me.

        Microsoft Calendar API method: /me/events/{event-id}/calendar (GET).

        Args:
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).calendar().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_get_count_ee29(self, *, param: str | None = None, **kwargs) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/events/$count (GET).

        Args:
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_get_extensions(self,
        *,
        event_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Calendar API method: /me/events/{event-id}/extensions/{extension-id} (GET).

        Args:
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().by_id(params.get("extension_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_list_attachments(self,
        *,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List attachments.

        Microsoft Calendar API method: /me/events/{event-id}/attachments (GET).

        Args:
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_list_extensions(self,
        *,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from me.

        Microsoft Calendar API method: /me/events/{event-id}/extensions (GET).

        Args:
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_list_instances(self,
        *,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List instances.

        Microsoft Calendar API method: /me/events/{event-id}/instances (GET).

        Args:
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).instances().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_update_extensions(self,
        *,
        event_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in me.

        Microsoft Calendar API method: /me/events/{event-id}/extensions/{extension-id} (PATCH).

        Args:
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_attachments_get_count_1985(self,
        *,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/events/{event-id}/attachments/$count (GET).

        Args:
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_delta(self,
        *,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/events/delta() (GET).

        Args:
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().delta()()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_event_accept(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action accept.

        Microsoft Calendar API method: /me/events/{event-id}/accept (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_event_attachments_create_upload_session(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createUploadSession.

        Microsoft Calendar API method: /me/events/{event-id}/attachments/createUploadSession (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().create_upload_session()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_event_cancel(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action cancel.

        Microsoft Calendar API method: /me/events/{event-id}/cancel (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).cancel().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_event_decline(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action decline.

        Microsoft Calendar API method: /me/events/{event-id}/decline (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).decline().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_event_dismiss_reminder(self, *, event_id: str, **kwargs) -> MSGraphResponse:
        """Invoke action dismissReminder.

        Microsoft Calendar API method: /me/events/{event-id}/dismissReminder (POST).

        Args:
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).dismiss_reminder().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_event_forward(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action forward.

        Microsoft Calendar API method: /me/events/{event-id}/forward (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).forward().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_event_instances_delta(self,
        *,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /me/events/{event-id}/instances/delta() (GET).

        Args:
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).instances().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_event_permanent_delete(self, *, event_id: str, **kwargs) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /me/events/{event-id}/permanentDelete (POST).

        Args:
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_event_snooze_reminder(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action snoozeReminder.

        Microsoft Calendar API method: /me/events/{event-id}/snoozeReminder (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).snooze_reminder()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_event_tentatively_accept(self,
        *,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action tentativelyAccept.

        Microsoft Calendar API method: /me/events/{event-id}/tentativelyAccept (POST).

        Args:
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).tentatively_accept()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def me_events_extensions_get_count_e2bd(self,
        *,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /me/events/{event-id}/extensions/$count (GET).

        Args:
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_create_calendar_groups(self,
        *,
        user_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to calendarGroups for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups (POST).

        Args:
            user_id (required): The unique identifier of user
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().users().by_id(params  # type:ignore
            .get("user_id", "")).calendar_groups()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_create_calendars(self,
        *,
        user_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to calendars for users.

        Microsoft Calendar API method: /users/{user-id}/calendars (POST).

        Args:
            user_id (required): The unique identifier of user
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_create_events(self,
        *,
        user_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to events for users.

        Microsoft Calendar API method: /users/{user-id}/events (POST).

        Args:
            user_id (required): The unique identifier of user
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_delete_calendar_groups(self,
        *,
        user_id: str,
        calendar_group_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property calendarGroups for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().users().by_id(params  # type:ignore
            .get("user_id", "")).calendar_groups().by_id(params
            .get("calendarGroup_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_delete_calendars(self,
        *,
        user_id: str,
        calendar_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property calendars for users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_delete_events(self,
        *,
        user_id: str,
        event_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property events for users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_get_calendar(self,
        *,
        user_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendar from users.

        Microsoft Calendar API method: /users/{user-id}/calendar (GET).

        Args:
            user_id (required): The unique identifier of user
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_get_calendar_groups(self,
        *,
        user_id: str,
        calendar_group_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarGroups from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().users().by_id(params  # type:ignore
            .get("user_id", "")).calendar_groups().by_id(params
            .get("calendarGroup_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_get_calendars(self,
        *,
        user_id: str,
        calendar_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendars from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_get_events(self,
        *,
        user_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_list_calendar_groups(self,
        *,
        user_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarGroups from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().users().by_id(params  # type:ignore
            .get("user_id", "")).calendar_groups().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_list_calendar_view(self,
        *,
        user_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarView from users.

        Microsoft Calendar API method: /users/{user-id}/calendarView (GET).

        Args:
            user_id (required): The unique identifier of user
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().users().by_id(params  # type:ignore
            .get("user_id", "")).calendar_view().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_list_calendars(self,
        *,
        user_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendars from users.

        Microsoft Calendar API method: /users/{user-id}/calendars (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_list_events(self,
        *,
        user_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from users.

        Microsoft Calendar API method: /users/{user-id}/events (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_update_calendar(self,
        *,
        user_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendar in users.

        Microsoft Calendar API method: /users/{user-id}/calendar (PATCH).

        Args:
            user_id (required): The unique identifier of user
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar()  # type:ignore
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_update_calendar_groups(self,
        *,
        user_id: str,
        calendar_group_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendarGroups in users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().users().by_id(params  # type:ignore
            .get("user_id", "")).calendar_groups().by_id(params
            .get("calendarGroup_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_update_calendars(self,
        *,
        user_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendars in users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_update_events(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property events in users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_create_calendar_permissions(self,
        *,
        user_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to calendarPermissions for users.

        Microsoft Calendar API method: /users/{user-id}/calendar/calendarPermissions (POST).

        Args:
            user_id (required): The unique identifier of user
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_permissions()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_create_events(self,
        *,
        user_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to events for users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events (POST).

        Args:
            user_id (required): The unique identifier of user
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_delete_calendar_permissions(self,
        *,
        user_id: str,
        calendar_permission_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete calendarPermission.

        Microsoft Calendar API method: /users/{user-id}/calendar/calendarPermissions/{calendarPermission-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_permission_id (required): The unique identifier of calendarPermission
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_permissions()  # type:ignore
            .by_id(params.get("calendarPermission_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_delete_events(self,
        *,
        user_id: str,
        event_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property events for users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_get_calendar_permissions(self,
        *,
        user_id: str,
        calendar_permission_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermission.

        Microsoft Calendar API method: /users/{user-id}/calendar/calendarPermissions/{calendarPermission-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_permission_id (required): The unique identifier of calendarPermission
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_permissions()  # type:ignore
            .by_id(params.get("calendarPermission_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_get_events(self,
        *,
        user_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_list_calendar_permissions(self,
        *,
        user_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """List calendarPermissions.

        Microsoft Calendar API method: /users/{user-id}/calendar/calendarPermissions (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_permissions()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_list_calendar_view(self,
        *,
        user_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarView from users.

        Microsoft Calendar API method: /users/{user-id}/calendar/calendarView (GET).

        Args:
            user_id (required): The unique identifier of user
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_view()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_list_events(self,
        *,
        user_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_update_calendar_permissions(self,
        *,
        user_id: str,
        calendar_permission_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update calendarPermission.

        Microsoft Calendar API method: /users/{user-id}/calendar/calendarPermissions/{calendarPermission-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            calendar_permission_id (required): The unique identifier of calendarPermission
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_permissions()  # type:ignore
            .by_id(params.get("calendarPermission_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_update_events(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property events in users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_calendar_permissions_get_count_b877(self,
        *,
        user_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendar/calendarPermissions/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_permissions().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_create_attachments(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to attachments for users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/attachments (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_create_extensions(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/extensions (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_delete_attachments(self,
        *,
        user_id: str,
        event_id: str,
        attachment_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property attachments for users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/attachments/{attachment-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().by_id(params.get("attachment_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_delete_extensions(self,
        *,
        user_id: str,
        event_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/extensions/{extension-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().by_id(params.get("extension_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_get_attachments(self,
        *,
        user_id: str,
        event_id: str,
        attachment_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/attachments/{attachment-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().by_id(params.get("attachment_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_get_calendar(self,
        *,
        user_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendar from users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/calendar (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).calendar().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_get_count_1a22(self,
        *,
        user_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().count()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_get_extensions(self,
        *,
        user_id: str,
        event_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/extensions/{extension-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().by_id(params.get("extension_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_list_attachments(self,
        *,
        user_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/attachments (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_list_extensions(self,
        *,
        user_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/extensions (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_list_instances(self,
        *,
        user_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get instances from users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/instances (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).instances().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_update_extensions(self,
        *,
        user_id: str,
        event_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in users.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/extensions/{extension-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_attachments_get_count_114f(self,
        *,
        user_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/attachments/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_events_extensions_get_count_15ec(self,
        *,
        user_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/extensions/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_create_calendars(self,
        *,
        user_id: str,
        calendar_group_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to calendars for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_delete_calendars(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property calendars for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_get_calendars(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendars from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_get_count_ee80(self,
        *,
        user_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().users().by_id(params  # type:ignore
            .get("user_id", "")).calendar_groups().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_list_calendars(self,
        *,
        user_id: str,
        calendar_group_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendars from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_update_calendars(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendars in users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_create_calendar_permissions(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to calendarPermissions for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_create_events(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to events for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_delete_calendar_permissions(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        calendar_permission_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property calendarPermissions for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_delete_events(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property events for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_get_calendar_permissions(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        calendar_permission_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermissions from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_get_count_8e45(self,
        *,
        user_id: str,
        calendar_group_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().count().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_get_events(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_list_calendar_permissions(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermissions from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_list_calendar_view(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarView from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarView (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_view().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_list_events(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_update_calendar_permissions(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        calendar_permission_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendarPermissions in users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_update_events(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property events in users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_calendar_permissions_get_count_98a8(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarPermissions/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_create_attachments(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to attachments for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_create_extensions(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_delete_attachments(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        attachment_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property attachments for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments/{attachment-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().by_id(params.get("attachment_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_delete_extensions(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_get_attachments(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        attachment_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments/{attachment-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().by_id(params.get("attachment_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_get_calendar(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendar from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/calendar (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .calendar().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_get_count_f3ad(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_get_extensions(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_list_attachments(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_list_extensions(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_list_instances(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get instances from users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/instances (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .instances().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_update_extensions(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in users.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_attachments_get_count_e742(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendar_groups_calendars_events_extensions_get_count_4cca(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/extensions/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_create_calendar_permissions(self,
        *,
        user_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to calendarPermissions for users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/calendarPermissions (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_create_events(self,
        *,
        user_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to events for users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_delete_calendar_permissions(self,
        *,
        user_id: str,
        calendar_id: str,
        calendar_permission_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property calendarPermissions for users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_delete_events(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property events for users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_get_calendar_permissions(self,
        *,
        user_id: str,
        calendar_id: str,
        calendar_permission_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermissions from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_get_count_a1b5(self,
        *,
        user_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendars/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().count().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_get_events(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_list_calendar_permissions(self,
        *,
        user_id: str,
        calendar_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarPermissions from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/calendarPermissions (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_list_calendar_view(self,
        *,
        user_id: str,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendarView from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/calendarView (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_view().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_list_events(self,
        *,
        user_id: str,
        calendar_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get events from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_update_calendar_permissions(self,
        *,
        user_id: str,
        calendar_id: str,
        calendar_permission_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property calendarPermissions in users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/calendarPermissions/{calendarPermission-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            calendar_permission_id (required): The unique identifier of calendarPermission
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if calendar_permission_id is not None:
            params["calendarPermission-id"] = calendar_permission_id
            params["calendarPermission_id"] = calendar_permission_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().by_id(params
            .get("calendarPermission_id", "")).patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_update_events(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property events in users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_calendar_permissions_get_count_a224(self,
        *,
        user_id: str,
        calendar_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/calendarPermissions/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_permissions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_create_attachments(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to attachments for users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/attachments (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_create_extensions(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/extensions (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_delete_attachments(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        attachment_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property attachments for users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/attachments/{attachment-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().by_id(params.get("attachment_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_delete_extensions(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", "")).delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_get_attachments(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        attachment_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/attachments/{attachment-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().by_id(params.get("attachment_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_get_calendar(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendar from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/calendar (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .calendar().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_get_count_efc7(self,
        *,
        user_id: str,
        calendar_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_get_extensions(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", "")).get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_list_attachments(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/attachments (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_list_extensions(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/extensions (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_list_instances(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get instances from users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/instances (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .instances().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_update_extensions(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in users.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/extensions/{extension-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_attachments_get_count_8147(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/attachments/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_calendars_events_extensions_get_count_b44d(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/extensions/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_create_attachments(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to attachments for users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/attachments (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).attachments().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_create_extensions(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Create new navigation property to extensions for users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/extensions (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).extensions().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_delete_attachments(self,
        *,
        user_id: str,
        event_id: str,
        attachment_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property attachments for users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/attachments/{attachment-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).attachments().by_id(params.get("attachment_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_delete_extensions(self,
        *,
        user_id: str,
        event_id: str,
        extension_id: str,
        if_match: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Delete navigation property extensions for users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/extensions/{extension-id} (DELETE).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            if_match (optional): ETag

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if if_match is not None:
            params["If-Match"] = if_match
            params["If_Match"] = if_match
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).extensions().by_id(params.get("extension_id", ""))
            .delete(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_get_attachments(self,
        *,
        user_id: str,
        event_id: str,
        attachment_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/attachments/{attachment-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            attachment_id (required): The unique identifier of attachment
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if attachment_id is not None:
            params["attachment-id"] = attachment_id
            params["attachment_id"] = attachment_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).attachments().by_id(params.get("attachment_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_get_calendar(self,
        *,
        user_id: str,
        event_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get calendar from users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/calendar (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).calendar().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_get_count_d443(self,
        *,
        user_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/events/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().count().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_get_extensions(self,
        *,
        user_id: str,
        event_id: str,
        extension_id: str,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/extensions/{extension-id} (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).extensions().by_id(params.get("extension_id", ""))
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_list_attachments(self,
        *,
        user_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get attachments from users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/attachments (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).attachments().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_list_extensions(self,
        *,
        user_id: str,
        event_id: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get extensions from users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/extensions (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).extensions().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_list_instances(self,
        *,
        user_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_select: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get instances from users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/instances (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T19:00:00-08:00
            end_date_time (required): The end date and time of the time range, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_orderby (optional): Order items by property values
            dollar_select (optional): Select properties to be returned
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).instances().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_update_extensions(self,
        *,
        user_id: str,
        event_id: str,
        extension_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Update the navigation property extensions in users.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/extensions/{extension-id} (PATCH).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            extension_id (required): The unique identifier of extension
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if extension_id is not None:
            params["extension-id"] = extension_id
            params["extension_id"] = extension_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).extensions().by_id(params.get("extension_id", ""))
            .patch(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_attachments_get_count_711f(self,
        *,
        user_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/attachments/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).attachments().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_events_extensions_get_count_0041(self,
        *,
        user_id: str,
        event_id: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Get the number of the resource.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/extensions/$count (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).extensions().count().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_allowed_calendar_sharing_roles(self,
        *,
        user_id: str,
        user: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function allowedCalendarSharingRoles.

        Microsoft Calendar API method: /users/{user-id}/calendar/allowedCalendarSharingRoles(User='{User}') (GET).

        Args:
            user_id (required): The unique identifier of user
            user (required): Usage: User='{User}'
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if user is not None:
            params["User"] = user
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar()  # type:ignore
            .allowed_calendar_sharing_roles(_user="{_user}")().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_calendar_view_delta(self,
        *,
        user_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/calendar/calendarView/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().calendar_view().delta()()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_delta(self,
        *,
        user_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().delta()()  # type:ignore
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_event_accept(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action accept.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/accept (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_event_attachments_create_upload_session(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createUploadSession.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/attachments/createUploadSession (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).attachments().create_upload_session()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_event_cancel(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action cancel.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/cancel (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).cancel().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_event_decline(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action decline.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/decline (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).decline().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_event_dismiss_reminder(self,
        *,
        user_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action dismissReminder.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/dismissReminder (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).dismiss_reminder().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_event_forward(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action forward.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/forward (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).forward().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_event_instances_delta(self,
        *,
        user_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/instances/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).instances().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_event_permanent_delete(self,
        *,
        user_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_event_snooze_reminder(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action snoozeReminder.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/snoozeReminder (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).snooze_reminder()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_events_event_tentatively_accept(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action tentativelyAccept.

        Microsoft Calendar API method: /users/{user-id}/calendar/events/{event-id}/tentativelyAccept (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().events().by_id(params  # type:ignore
            .get("event_id", "")).tentatively_accept()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_get_schedule(self,
        *,
        user_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action getSchedule.

        Microsoft Calendar API method: /users/{user-id}/calendar/getSchedule (POST).

        Args:
            user_id (required): The unique identifier of user
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().get_schedule()  # type:ignore
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_permanent_delete(self, *, user_id: str, **kwargs) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /users/{user-id}/calendar/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().permanent_delete()  # type:ignore
            .post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_allowed_calendar_sharing_roles(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        user: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function allowedCalendarSharingRoles.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/allowedCalendarSharingRoles(User='{User}') (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            user (required): Usage: User='{User}'
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if user is not None:
            params["User"] = user
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).allowed_calendar_sharing_roles(_user="{_user}")()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_calendar_view_delta(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/calendarView/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_view().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_delta(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_event_accept(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action accept.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/accept (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_event_attachments_create_upload_session(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createUploadSession.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/attachments/createUploadSession (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().create_upload_session()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_event_cancel(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action cancel.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/cancel (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .cancel().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_event_decline(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action decline.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/decline (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .decline().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_event_dismiss_reminder(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action dismissReminder.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/dismissReminder (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .dismiss_reminder().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_event_forward(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action forward.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/forward (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .forward().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_event_instances_delta(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/instances/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .instances().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_event_permanent_delete(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_event_snooze_reminder(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action snoozeReminder.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/snoozeReminder (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .snooze_reminder().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_events_event_tentatively_accept(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action tentativelyAccept.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/events/{event-id}/tentativelyAccept (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .tentatively_accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_get_schedule(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action getSchedule.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/getSchedule (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).get_schedule()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_groups_calendar_group_calendars_calendar_permanent_delete(self,
        *,
        user_id: str,
        calendar_group_id: str,
        calendar_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /users/{user-id}/calendarGroups/{calendarGroup-id}/calendars/{calendar-id}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_group_id (required): The unique identifier of calendarGroup
            calendar_id (required): The unique identifier of calendar

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_group_id is not None:
            params["calendarGroup-id"] = calendar_group_id
            params["calendarGroup_id"] = calendar_group_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendar_view_delta(self,
        *,
        user_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/calendarView/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().users().by_id(params  # type:ignore
            .get("user_id", "")).calendar_view().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_allowed_calendar_sharing_roles(self,
        *,
        user_id: str,
        calendar_id: str,
        user: str,
        param: str | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function allowedCalendarSharingRoles.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/allowedCalendarSharingRoles(User='{User}') (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            user (required): Usage: User='{User}'
            param (optional):

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if user is not None:
            params["User"] = user
        if param is not None:
            params["param"] = param
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).allowed_calendar_sharing_roles(_user="{_user}")()
            .get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_calendar_view_delta(self,
        *,
        user_id: str,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/calendarView/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).calendar_view().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_delta(self,
        *,
        user_id: str,
        calendar_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_event_accept(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action accept.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/accept (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_event_attachments_create_upload_session(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createUploadSession.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/attachments/createUploadSession (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .attachments().create_upload_session()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_event_cancel(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action cancel.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/cancel (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .cancel().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_event_decline(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action decline.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/decline (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .decline().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_event_dismiss_reminder(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action dismissReminder.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/dismissReminder (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .dismiss_reminder().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_event_forward(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action forward.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/forward (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .forward().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_event_instances_delta(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/instances/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .instances().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_event_permanent_delete(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_event_snooze_reminder(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action snoozeReminder.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/snoozeReminder (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .snooze_reminder().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_events_event_tentatively_accept(self,
        *,
        user_id: str,
        calendar_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action tentativelyAccept.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/events/{event-id}/tentativelyAccept (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).events().by_id(params.get("event_id", ""))
            .tentatively_accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_get_schedule(self,
        *,
        user_id: str,
        calendar_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action getSchedule.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/getSchedule (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).get_schedule()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_calendars_calendar_permanent_delete(self,
        *,
        user_id: str,
        calendar_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /users/{user-id}/calendars/{calendar-id}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            calendar_id (required): The unique identifier of calendar

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if calendar_id is not None:
            params["calendar-id"] = calendar_id
            params["calendar_id"] = calendar_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("calendar_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_delta(self,
        *,
        user_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/events/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().delta()().get(params=params)  # type:ignore
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_event_accept(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action accept.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/accept (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).accept().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_event_attachments_create_upload_session(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action createUploadSession.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/attachments/createUploadSession (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).attachments().create_upload_session()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_event_cancel(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action cancel.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/cancel (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).cancel().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_event_decline(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action decline.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/decline (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).decline().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_event_dismiss_reminder(self,
        *,
        user_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action dismissReminder.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/dismissReminder (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).dismiss_reminder().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_event_forward(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action forward.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/forward (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).forward().post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_event_instances_delta(self,
        *,
        user_id: str,
        event_id: str,
        start_date_time: str,
        end_date_time: str,
        param: str | None = None,
        dollar_select: list[str] | None = None,
        dollar_orderby: list[str] | None = None,
        dollar_expand: list[str] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke function delta.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/instances/delta() (GET).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            start_date_time (required): The start date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            end_date_time (required): The end date and time of the time range in the function, represented in ISO 8601 format. For example, 2019-11-08T20:00:00-08:00
            param (optional):
            dollar_select (optional): Select properties to be returned
            dollar_orderby (optional): Order items by property values
            dollar_expand (optional): Expand related entities

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if start_date_time is not None:
            params["startDateTime"] = start_date_time
        if end_date_time is not None:
            params["endDateTime"] = end_date_time
        if param is not None:
            params["param"] = param
        if dollar_select is not None:
            _val = ",".join(dollar_select) if isinstance(dollar_select, list) else str(dollar_select)
            params["$select"] = _val
            params["dollar_select"] = _val
        if dollar_orderby is not None:
            _val = ",".join(dollar_orderby) if isinstance(dollar_orderby, list) else str(dollar_orderby)
            params["$orderby"] = _val
            params["dollar_orderby"] = _val
        if dollar_expand is not None:
            _val = ",".join(dollar_expand) if isinstance(dollar_expand, list) else str(dollar_expand)
            params["$expand"] = _val
            params["dollar_expand"] = _val
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).instances().delta()().get(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_event_permanent_delete(self,
        *,
        user_id: str,
        event_id: str,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action permanentDelete.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/permanentDelete (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).permanent_delete().post(params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_event_snooze_reminder(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action snoozeReminder.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/snoozeReminder (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).snooze_reminder()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )

    def users_user_events_event_tentatively_accept(self,
        *,
        user_id: str,
        event_id: str,
        request_body: Mapping[str, object] | None = None,
        **kwargs,
    ) -> MSGraphResponse:
        """Invoke action tentativelyAccept.

        Microsoft Calendar API method: /users/{user-id}/events/{event-id}/tentativelyAccept (POST).

        Args:
            user_id (required): The unique identifier of user
            event_id (required): The unique identifier of event
            request_body (optional): Request body data

        Returns:
            MSGraphResponse: Standardized response wrapper

        """
        params: dict[str, object] = {}
        if user_id is not None:
            params["user-id"] = user_id
            params["user_id"] = user_id
        if event_id is not None:
            params["event-id"] = event_id
            params["event_id"] = event_id
        if kwargs:
            for k, v in kwargs.items():
                if v is None:
                    continue
                # CSV-encode OData list params; mirror hyphenated -> snake
                if k in ("$select", "$expand") and isinstance(v, list):
                    _vv = ",".join(v)
                    params[k] = _vv
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = _vv
                else:
                    params[k] = v
                    _mk = self._sanitize_py_name(k)
                    if _mk != k:
                        params[_mk] = v

        try:
            response = (
            self.client.me().calendar().by_id(params  # type:ignore
            .get("event_id", "")).tentatively_accept()
            .post(request_body or {}, params=params)
            )
            return self._handle_response(response)
        except Exception as e:
            return MSGraphResponse(
                success=False,
                error=f"Microsoft Calendar API API call failed: {e!s}",
            )
