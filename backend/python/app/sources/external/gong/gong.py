"""
Gong REST API DataSource - Auto-generated API wrapper

Generated from Gong REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.gong.gong import GongClient, GongResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class GongDataSource:
    """Gong REST API DataSource

    Provides async wrapper methods for Gong REST API v2 operations:
    - Calls (list, get, extensive, add, transcripts, CRM associations, recording, sharing)
    - Users (list, get, history, extensive)
    - Stats (aggregate activity, day-by-day, scorecards, interaction)
    - Library (folders, folder calls)
    - Meetings
    - Settings (scorecards, workspaces, trackers, smart trackers)
    - CRM (objects, upload data/schema, integration, request status)
    - Data Privacy (find people, purge email/phone)
    - Engagement Data (content shared, customer engagement)
    - Flows
    - Digital Interactions
    - Permission Profiles (list, get, create, update, delete)
    - Company Hierarchy
    - Coaching (daily briefs)
    - Emails (extensive)
    - Forecasting (submissions)

    The base URL is determined by the GongClient's configured base URL
    (default: https://api.gong.io/v2).

    All methods return GongResponse objects.
    """

    def __init__(self, client: GongClient) -> None:
        """Initialize with GongClient.

        Args:
            client: GongClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'GongDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> GongClient:
        """Return the underlying GongClient."""
        return self._client

    async def list_calls(
        self,
        cursor: str | None = None
    ) -> GongResponse:
        """List calls with optional cursor pagination

        Args:
            cursor: Pagination cursor from previous response

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/calls"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_calls" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_calls")

    async def get_call(
        self,
        id: str
    ) -> GongResponse:
        """Get a specific call

        Args:
            id: The call ID

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/calls/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_call" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_call")

    async def get_calls_extensive(
        self,
        content_selector: dict[str, Any] | None = None,
        filter: dict[str, Any] | None = None,
        cursor: str | None = None
    ) -> GongResponse:
        """Get detailed call data with content selectors

        Args:
            content_selector: Content selection (context, exposedFields)
            filter: Filter criteria (fromDateTime, toDateTime, callIds, primaryUserIds, workspaceId)
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/calls/extensive"

        body: dict[str, Any] = {}
        if content_selector is not None:
            body['contentSelector'] = content_selector
        if filter is not None:
            body['filter'] = filter
        if cursor is not None:
            body['cursor'] = cursor

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_calls_extensive" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_calls_extensive")

    async def add_call(
        self,
        actual_start: str,
        client_unique_id: str,
        call_provider_code: str | None = None,
        download_media_url: str | None = None,
        custom_data: str | None = None,
        direction: str | None = None,
        disposition: str | None = None,
        parties: list[Any] | None = None,
        primary_user: str | None = None,
        title: str | None = None,
        purpose: str | None = None,
        meeting_url: str | None = None,
        scheduled_start: str | None = None,
        scheduled_end: str | None = None,
        language: str | None = None
    ) -> GongResponse:
        """Add a new call recording

        Args:
            actual_start: ISO 8601 start time
            client_unique_id: Unique ID from client
            call_provider_code: Provider code
            download_media_url: URL to download media
            custom_data: Custom data
            direction: Inbound/Outbound
            disposition: Call disposition
            parties: Call participants
            primary_user: Primary user ID
            title: Call title
            purpose: Call purpose
            meeting_url: Meeting URL
            scheduled_start: Scheduled start time
            scheduled_end: Scheduled end time
            language: Language code

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/calls"

        body: dict[str, Any] = {}
        body['actualStart'] = actual_start
        body['clientUniqueId'] = client_unique_id
        if call_provider_code is not None:
            body['callProviderCode'] = call_provider_code
        if download_media_url is not None:
            body['downloadMediaUrl'] = download_media_url
        if custom_data is not None:
            body['customData'] = custom_data
        if direction is not None:
            body['direction'] = direction
        if disposition is not None:
            body['disposition'] = disposition
        if parties is not None:
            body['parties'] = parties
        if primary_user is not None:
            body['primaryUser'] = primary_user
        if title is not None:
            body['title'] = title
        if purpose is not None:
            body['purpose'] = purpose
        if meeting_url is not None:
            body['meetingUrl'] = meeting_url
        if scheduled_start is not None:
            body['scheduledStart'] = scheduled_start
        if scheduled_end is not None:
            body['scheduledEnd'] = scheduled_end
        if language is not None:
            body['language'] = language

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed add_call" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute add_call")

    async def get_call_transcripts(
        self,
        filter: dict[str, Any]
    ) -> GongResponse:
        """Get call transcripts

        Args:
            filter: Filter with callIds array

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/calls/transcript"

        body: dict[str, Any] = {}
        body['filter'] = filter

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_call_transcripts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_call_transcripts")

    async def get_manual_crm_associations(
        self,
        cursor: str | None = None
    ) -> GongResponse:
        """Get manual CRM associations for calls

        Args:
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/calls/manual-crm-associations"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_manual_crm_associations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_manual_crm_associations")

    async def add_call_recording(
        self,
        id: str,
        media_url: str | None = None
    ) -> GongResponse:
        """Upload media for a call

        Args:
            id: The call ID
            media_url: Media URL

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/calls/{id}/media".format(id=id)

        body: dict[str, Any] = {}
        if media_url is not None:
            body['mediaUrl'] = media_url

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed add_call_recording" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute add_call_recording")

    async def get_call_sharing(
        self,
        filter: dict[str, Any] | None = None,
        cursor: str | None = None
    ) -> GongResponse:
        """Get shared calls

        Args:
            filter: Filter criteria
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/calls/sharing"

        body: dict[str, Any] = {}
        if filter is not None:
            body['filter'] = filter
        if cursor is not None:
            body['cursor'] = cursor

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_call_sharing" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_call_sharing")

    async def list_users(
        self,
        *,
        cursor: str | None = None,
        include_avatars: bool | None = None
    ) -> GongResponse:
        """List all users

        Args:
            cursor: Pagination cursor
            include_avatars: Include avatar URLs

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor
        if include_avatars is not None:
            query_params['includeAvatars'] = str(include_avatars).lower()

        url = self.base_url + "/users"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        id: str
    ) -> GongResponse:
        """Get a specific user

        Args:
            id: The user ID

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/users/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_user")

    async def get_user_history(
        self,
        id: str
    ) -> GongResponse:
        """Get user settings history

        Args:
            id: The user ID

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/users/{id}/settings-history".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user_history" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_user_history")

    async def list_users_extensive(
        self,
        *,
        filter: dict[str, Any] | None = None,
        cursor: str | None = None,
        include_avatars: bool | None = None
    ) -> GongResponse:
        """List users by filter

        Args:
            filter: Filter (fromDateTime, toDateTime, createdFromDateTime, createdToDateTime, userIds)
            cursor: Pagination cursor
            include_avatars: Include avatar URLs

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/users/extensive"

        body: dict[str, Any] = {}
        if filter is not None:
            body['filter'] = filter
        if cursor is not None:
            body['cursor'] = cursor
        if include_avatars is not None:
            body['includeAvatars'] = include_avatars

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users_extensive" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_users_extensive")

    async def get_aggregate_activity(
        self,
        filter: dict[str, Any]
    ) -> GongResponse:
        """Get aggregated activity stats by users

        Args:
            filter: Filter (fromDate, toDate, userIds)

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/stats/activity/aggregate"

        body: dict[str, Any] = {}
        body['filter'] = filter

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_aggregate_activity" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_aggregate_activity")

    async def get_aggregate_activity_by_period(
        self,
        filter: dict[str, Any],
        aggregation_period: str | None = None
    ) -> GongResponse:
        """Get activity aggregated by period

        Args:
            filter: Filter (fromDate, toDate, userIds)
            aggregation_period: Aggregation period (DAY, WEEK, MONTH, QUARTER)

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/stats/activity/aggregate-by-period"

        body: dict[str, Any] = {}
        body['filter'] = filter
        if aggregation_period is not None:
            body['aggregationPeriod'] = aggregation_period

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_aggregate_activity_by_period" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_aggregate_activity_by_period")

    async def get_activity_day_by_day(
        self,
        filter: dict[str, Any],
        aggregation_period: str | None = None
    ) -> GongResponse:
        """Get day-by-day activity

        Args:
            filter: Filter (fromDate, toDate, userIds)
            aggregation_period: Unused but accepted for consistency

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/stats/activity/day-by-day"

        body: dict[str, Any] = {}
        body['filter'] = filter
        if aggregation_period is not None:
            body['aggregationPeriod'] = aggregation_period

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_activity_day_by_day" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_activity_day_by_day")

    async def get_answered_scorecards(
        self,
        filter: dict[str, Any],
        aggregation_period: str | None = None
    ) -> GongResponse:
        """Get answered scorecards by user

        Args:
            filter: Filter (fromDate, toDate, userIds, scorecardIds)
            aggregation_period: Aggregation period

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/stats/activity/scorecards"

        body: dict[str, Any] = {}
        body['filter'] = filter
        if aggregation_period is not None:
            body['aggregationPeriod'] = aggregation_period

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_answered_scorecards" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_answered_scorecards")

    async def get_interaction_stats(
        self,
        filter: dict[str, Any]
    ) -> GongResponse:
        """Get interaction stats by user

        Args:
            filter: Filter (fromDate, toDate, userIds)

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/stats/interaction"

        body: dict[str, Any] = {}
        body['filter'] = filter

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_interaction_stats" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_interaction_stats")

    async def list_library_folders(
        self,
        cursor: str | None = None,
        workspace_id: str | None = None
    ) -> GongResponse:
        """List library folders

        Args:
            cursor: Pagination cursor
            workspace_id: Workspace ID

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor
        if workspace_id is not None:
            query_params['workspaceId'] = workspace_id

        url = self.base_url + "/library/folders"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_library_folders" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_library_folders")

    async def get_library_folder_calls(
        self,
        folder_id: str,
        cursor: str | None = None
    ) -> GongResponse:
        """Get calls in a library folder

        Args:
            folder_id: The folder ID
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['folderId'] = folder_id
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/library/folder-calls"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_library_folder_calls" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_library_folder_calls")

    async def list_meetings(
        self,
        cursor: str | None = None,
        from_date_time: str | None = None,
        to_date_time: str | None = None
    ) -> GongResponse:
        """List meetings

        Args:
            cursor: Pagination cursor
            from_date_time: ISO 8601 start filter
            to_date_time: ISO 8601 end filter

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor
        if from_date_time is not None:
            query_params['fromDateTime'] = from_date_time
        if to_date_time is not None:
            query_params['toDateTime'] = to_date_time

        url = self.base_url + "/meetings"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_meetings" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_meetings")

    async def list_scorecards(
        self,
        cursor: str | None = None
    ) -> GongResponse:
        """List scorecards

        Args:
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/settings/scorecards"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_scorecards" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_scorecards")

    async def list_workspaces(
        self
    ) -> GongResponse:
        """List workspaces

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/settings/workspaces"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_workspaces" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_workspaces")

    async def list_trackers(
        self,
        cursor: str | None = None
    ) -> GongResponse:
        """List trackers (keywords)

        Args:
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/settings/trackers"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_trackers" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_trackers")

    async def list_smart_trackers(
        self,
        cursor: str | None = None
    ) -> GongResponse:
        """List smart trackers

        Args:
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/settings/trackers/smart-trackers"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_smart_trackers" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_smart_trackers")

    async def get_crm_objects(
        self,
        cursor: str | None = None,
        object_type: str | None = None
    ) -> GongResponse:
        """Get CRM objects synced to Gong

        Args:
            cursor: Pagination cursor
            object_type: e.g., deals, accounts, contacts, leads

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor
        if object_type is not None:
            query_params['objectType'] = object_type

        url = self.base_url + "/crm/objects"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_crm_objects" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_crm_objects")

    async def upload_crm_data(
        self,
        integration_id: str,
        objects: list[Any]
    ) -> GongResponse:
        """Upload CRM object data (for custom CRM integrations)

        Args:
            integration_id: Integration ID
            objects: CRM objects to upload

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/crm/object/list"

        body: dict[str, Any] = {}
        body['integrationId'] = integration_id
        body['objects'] = objects

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed upload_crm_data" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute upload_crm_data")

    async def upload_crm_schema(
        self,
        integration_id: str,
        schemas: list[Any]
    ) -> GongResponse:
        """Upload CRM object schema

        Args:
            integration_id: Integration ID
            schemas: CRM schemas

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/crm/object/schema/list"

        body: dict[str, Any] = {}
        body['integrationId'] = integration_id
        body['schemas'] = schemas

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed upload_crm_schema" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute upload_crm_schema")

    async def delete_crm_integration(
        self,
        integration_id: str
    ) -> GongResponse:
        """Delete a CRM integration

        Args:
            integration_id: Integration ID

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['integrationId'] = integration_id

        url = self.base_url + "/crm/integration/delete"

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_crm_integration" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute delete_crm_integration")

    async def register_crm_integration(
        self,
        integration_id: str,
        integration_name: str,
        crm_system_type: str | None = None
    ) -> GongResponse:
        """Register a CRM integration

        Args:
            integration_id: Integration ID
            integration_name: Integration name
            crm_system_type: CRM type

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/crm/integration/register"

        body: dict[str, Any] = {}
        body['integrationId'] = integration_id
        body['integrationName'] = integration_name
        if crm_system_type is not None:
            body['crmSystemType'] = crm_system_type

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed register_crm_integration" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute register_crm_integration")

    async def get_request_status(
        self,
        integration_id: str,
        client_request_id: str
    ) -> GongResponse:
        """Get status of a CRM data upload

        Args:
            integration_id: Integration ID
            client_request_id: Client request ID

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['integrationId'] = integration_id
        query_params['clientRequestId'] = client_request_id

        url = self.base_url + "/crm/request-status"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_request_status" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_request_status")

    async def find_people_by_email_or_phone(
        self,
        email_address: str | None = None,
        phone_number: str | None = None
    ) -> GongResponse:
        """Find people matching email/phone for GDPR

        Args:
            email_address: Email to search
            phone_number: Phone to search

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/data-privacy/data-for-email-address"

        body: dict[str, Any] = {}
        if email_address is not None:
            body['emailAddress'] = email_address
        if phone_number is not None:
            body['phoneNumber'] = phone_number

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed find_people_by_email_or_phone" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute find_people_by_email_or_phone")

    async def purge_email_address(
        self,
        email_address: str
    ) -> GongResponse:
        """Purge data by email

        Args:
            email_address: Email address to purge

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/data-privacy/erase-data-for-email-address"

        body: dict[str, Any] = {}
        body['emailAddress'] = email_address

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed purge_email_address" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute purge_email_address")

    async def purge_phone_number(
        self,
        phone_number: str
    ) -> GongResponse:
        """Purge data by phone number

        Args:
            phone_number: Phone number to purge

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/data-privacy/erase-data-for-phone-number"

        body: dict[str, Any] = {}
        body['phoneNumber'] = phone_number

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed purge_phone_number" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute purge_phone_number")

    async def get_content_shared_with_external(
        self,
        filter: dict[str, Any],
        cursor: str | None = None
    ) -> GongResponse:
        """Get content shared with external parties

        Args:
            filter: Filter criteria
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/engagement-data/content-shared"

        body: dict[str, Any] = {}
        body['filter'] = filter
        if cursor is not None:
            body['cursor'] = cursor

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_content_shared_with_external" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_content_shared_with_external")

    async def upload_customer_engagement(
        self,
        entries: list[Any]
    ) -> GongResponse:
        """Upload customer engagement data

        Args:
            entries: Engagement entries

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/engagement-data/customer-engagement"

        body: dict[str, Any] = {}
        body['entries'] = entries

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed upload_customer_engagement" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute upload_customer_engagement")

    async def list_flows(
        self,
        cursor: str | None = None
    ) -> GongResponse:
        """List flows (Gong Engage flows)

        Args:
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/flows"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_flows" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_flows")

    async def upload_digital_interactions(
        self,
        records: list[Any]
    ) -> GongResponse:
        """Upload digital interaction records

        Args:
            records: Digital interaction records

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/digital-interactions"

        body: dict[str, Any] = {}
        body['records'] = records

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed upload_digital_interactions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute upload_digital_interactions")

    async def list_permission_profiles(
        self
    ) -> GongResponse:
        """List permission profiles

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/permission-profile"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_permission_profiles" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_permission_profiles")

    async def get_permission_profile(
        self,
        id: str
    ) -> GongResponse:
        """Get a permission profile

        Args:
            id: The permission profile ID

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/permission-profile/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_permission_profile" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_permission_profile")

    async def create_permission_profile(
        self,
        name: str,
        permissions: list[Any]
    ) -> GongResponse:
        """Create a permission profile

        Args:
            name: Profile name
            permissions: Permissions list

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/permission-profile"

        body: dict[str, Any] = {}
        body['name'] = name
        body['permissions'] = permissions

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_permission_profile" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute create_permission_profile")

    async def update_permission_profile(
        self,
        id: str,
        name: str | None = None,
        permissions: list[Any] | None = None
    ) -> GongResponse:
        """Update a permission profile

        Args:
            id: The permission profile ID
            name: Profile name
            permissions: Permissions list

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/permission-profile/{id}".format(id=id)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if permissions is not None:
            body['permissions'] = permissions

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_permission_profile" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute update_permission_profile")

    async def delete_permission_profile(
        self,
        id: str
    ) -> GongResponse:
        """Delete a permission profile

        Args:
            id: The permission profile ID

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/permission-profile/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_permission_profile" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute delete_permission_profile")

    async def list_company_users(
        self,
        cursor: str | None = None
    ) -> GongResponse:
        """List company users with hierarchy info

        Args:
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/company/users"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_company_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_company_users")

    async def get_daily_briefs(
        self,
        filter: dict[str, Any]
    ) -> GongResponse:
        """Get daily brief summaries

        Args:
            filter: Filter criteria

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/coaching/daily-briefs"

        body: dict[str, Any] = {}
        body['filter'] = filter

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_daily_briefs" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_daily_briefs")

    async def get_emails_extensive(
        self,
        filter: dict[str, Any],
        content_selector: dict[str, Any] | None = None,
        cursor: str | None = None
    ) -> GongResponse:
        """Get detailed email activity data

        Args:
            filter: Filter criteria
            content_selector: Content selection
            cursor: Pagination cursor

        Returns:
            GongResponse with operation result
        """
        url = self.base_url + "/emails/extensive"

        body: dict[str, Any] = {}
        body['filter'] = filter
        if content_selector is not None:
            body['contentSelector'] = content_selector
        if cursor is not None:
            body['cursor'] = cursor

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_emails_extensive" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute get_emails_extensive")

    async def list_forecast_submissions(
        self,
        cursor: str | None = None,
        from_date_time: str | None = None,
        to_date_time: str | None = None
    ) -> GongResponse:
        """List forecast submissions

        Args:
            cursor: Pagination cursor
            from_date_time: ISO 8601 start filter
            to_date_time: ISO 8601 end filter

        Returns:
            GongResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor
        if from_date_time is not None:
            query_params['fromDateTime'] = from_date_time
        if to_date_time is not None:
            query_params['toDateTime'] = to_date_time

        url = self.base_url + "/forecast/submissions"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GongResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_forecast_submissions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GongResponse(success=False, error=str(e), message="Failed to execute list_forecast_submissions")
