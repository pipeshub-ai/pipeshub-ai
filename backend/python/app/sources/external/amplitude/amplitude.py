"""
Amplitude REST API DataSource - Auto-generated API wrapper

Generated from Amplitude REST API v2/v3 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.amplitude.amplitude import AmplitudeClient, AmplitudeResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class AmplitudeDataSource:
    """Amplitude REST API DataSource

    Provides async wrapper methods for Amplitude REST API operations:
    - Event Segmentation queries
    - User Search and Activity
    - User Deletion management
    - Raw Data Export
    - Event Upload
    - Cohort management
    - Chart queries
    - Annotations and Releases
    - Taxonomy (Event Types, User Properties, Event Properties)

    Uses two base URLs:
    - v2: https://amplitude.com/api/2
    - v3: https://analytics.amplitude.com/api/3

    All methods return AmplitudeResponse objects.
    """

    def __init__(self, client: AmplitudeClient) -> None:
        """Initialize with AmplitudeClient.

        Args:
            client: AmplitudeClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc
        try:
            self.base_url_v3 = self.http.get_base_url_v3().rstrip('/')
        except AttributeError:
            self.base_url_v3 = 'https://analytics.amplitude.com/api/3'

    def get_data_source(self) -> 'AmplitudeDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> AmplitudeClient:
        """Return the underlying AmplitudeClient."""
        return self._client

    async def get_event_segmentation(
        self,
        event: str,
        start: str,
        end: str,
        m: str | None = None,
        i: str | None = None,
        g: str | None = None,
        limit: int | None = None
    ) -> AmplitudeResponse:
        """Get event segmentation data for analytics queries (API v2)

        Args:
            event: Event JSON object (required). Defines the event to segment on
            start: Start date (required), e.g. '20230101'
            end: End date (required), e.g. '20230131'
            m: Metric type (e.g. 'uniques', 'totals', 'avg')
            i: Interval: '-300000', '3600000', '86400000', or '604800000'
            g: Group by property
            limit: Limit the number of group by values returned

        Returns:
            AmplitudeResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['e'] = event
        query_params['start'] = start
        query_params['end'] = end
        if m is not None:
            query_params['m'] = m
        if i is not None:
            query_params['i'] = i
        if g is not None:
            query_params['g'] = g
        if limit is not None:
            query_params['limit'] = str(limit)

        url = self.base_url + "/events/segmentation"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_event_segmentation" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute get_event_segmentation")

    async def search_user(
        self,
        user: str
    ) -> AmplitudeResponse:
        """Search for a user by email or Amplitude ID (API v2)

        Args:
            user: User email address or Amplitude ID (required)

        Returns:
            AmplitudeResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['user'] = user

        url = self.base_url + "/usersearch"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute search_user")

    async def get_user_activity(
        self,
        user: str,
        offset: int | None = None,
        limit: int | None = None
    ) -> AmplitudeResponse:
        """Get a user's event activity (API v2)

        Args:
            user: Amplitude user ID (required)
            offset: Offset for pagination
            limit: Number of events to return (max 1000)

        Returns:
            AmplitudeResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['user'] = user
        if offset is not None:
            query_params['offset'] = str(offset)
        if limit is not None:
            query_params['limit'] = str(limit)

        url = self.base_url + "/useractivity"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user_activity" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute get_user_activity")

    async def get_user_deletion_jobs(
        self,
        start_day: str | None = None,
        end_day: str | None = None
    ) -> AmplitudeResponse:
        """Get user deletion jobs within a date range (API v2)

        Args:
            start_day: Start date for deletion jobs (e.g. '2023-01-01')
            end_day: End date for deletion jobs (e.g. '2023-01-31')

        Returns:
            AmplitudeResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start_day is not None:
            query_params['start_day'] = start_day
        if end_day is not None:
            query_params['end_day'] = end_day

        url = self.base_url + "/deletions/users"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user_deletion_jobs" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute get_user_deletion_jobs")

    async def create_user_deletion(
        self,
        amplitude_ids: list[int] | None = None,
        user_ids: list[str] | None = None,
        requester: str | None = None
    ) -> AmplitudeResponse:
        """Create a user deletion job to delete user data (API v2)

        Args:
            amplitude_ids: List of Amplitude user IDs to delete
            user_ids: List of user IDs to delete
            requester: Email of the requester

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url + "/deletions/users"

        body: dict[str, Any] = {}
        if amplitude_ids is not None:
            body['amplitude_ids'] = amplitude_ids
        if user_ids is not None:
            body['user_ids'] = user_ids
        if requester is not None:
            body['requester'] = requester

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_user_deletion" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute create_user_deletion")

    async def export_raw_data(
        self,
        start: str,
        end: str
    ) -> AmplitudeResponse:
        """Export raw event data for a date range (returns zipped JSON) (API v2)

        Args:
            start: Start date hour (required), e.g. '20230101T00'
            end: End date hour (required), e.g. '20230102T00'

        Returns:
            AmplitudeResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['start'] = start
        query_params['end'] = end

        url = self.base_url + "/export"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed export_raw_data" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute export_raw_data")

    async def upload_events(
        self,
        api_key: str,
        events: list[dict[str, Any]]
    ) -> AmplitudeResponse:
        """Upload events to Amplitude (batch upload) (API v2)

        Args:
            api_key: Amplitude API key
            events: List of event objects to upload

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url + "/events/upload"

        body: dict[str, Any] = {}
        body['api_key'] = api_key
        body['events'] = events

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed upload_events" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute upload_events")

    async def list_cohorts(
        self
    ) -> AmplitudeResponse:
        """List all cohorts in the project (API v3)

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url_v3 + "/cohorts"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_cohorts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute list_cohorts")

    async def get_cohort(
        self,
        cohort_id: str
    ) -> AmplitudeResponse:
        """Get details of a specific cohort (API v3)

        Args:
            cohort_id: The cohort ID

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url_v3 + "/cohorts/{cohort_id}".format(cohort_id=cohort_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_cohort" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute get_cohort")

    async def query_chart(
        self,
        chart_id: str
    ) -> AmplitudeResponse:
        """Query a saved chart by ID (API v3)

        Args:
            chart_id: The chart ID

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url_v3 + "/charts/{chart_id}/query".format(chart_id=chart_id)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed query_chart" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute query_chart")

    async def list_annotations(
        self
    ) -> AmplitudeResponse:
        """List all annotations (API v2)

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url + "/annotations"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_annotations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute list_annotations")

    async def create_annotation(
        self,
        date: str,
        label: str,
        details: str | None = None
    ) -> AmplitudeResponse:
        """Create a new annotation (API v2)

        Args:
            date: Date of the annotation (e.g. '2023-01-15')
            label: Label/title of the annotation
            details: Additional details for the annotation

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url + "/annotations"

        body: dict[str, Any] = {}
        body['date'] = date
        body['label'] = label
        if details is not None:
            body['details'] = details

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_annotation" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute create_annotation")

    async def list_releases(
        self
    ) -> AmplitudeResponse:
        """List all releases (API v2)

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url + "/releases"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_releases" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute list_releases")

    async def create_release(
        self,
        version: str,
        release_start: str,
        release_end: str | None = None,
        title: str | None = None,
        description: str | None = None,
        platforms: list[str] | None = None,
        created_by: str | None = None,
        chart_id: str | None = None
    ) -> AmplitudeResponse:
        """Create a new release (API v2)

        Args:
            version: Release version string
            release_start: Release start date (e.g. '2023-01-15')
            release_end: Release end date (e.g. '2023-01-16')
            title: Title of the release
            description: Description of the release
            platforms: List of platforms for this release
            created_by: Email of the release creator
            chart_id: Chart ID to associate with the release

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url + "/releases"

        body: dict[str, Any] = {}
        body['version'] = version
        body['release_start'] = release_start
        if release_end is not None:
            body['release_end'] = release_end
        if title is not None:
            body['title'] = title
        if description is not None:
            body['description'] = description
        if platforms is not None:
            body['platforms'] = platforms
        if created_by is not None:
            body['created_by'] = created_by
        if chart_id is not None:
            body['chart_id'] = chart_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_release" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute create_release")

    async def list_event_types(
        self
    ) -> AmplitudeResponse:
        """List all event types in the project's taxonomy (API v2)

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url + "/taxonomy/event-type"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_event_types" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute list_event_types")

    async def get_event_type(
        self,
        event_type: str
    ) -> AmplitudeResponse:
        """Get a specific event type from the taxonomy (API v2)

        Args:
            event_type: The event type name

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url + "/taxonomy/event-type/{event_type}".format(event_type=event_type)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_event_type" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute get_event_type")

    async def list_user_properties(
        self
    ) -> AmplitudeResponse:
        """List all user properties in the project's taxonomy (API v2)

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url + "/taxonomy/user-property"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_user_properties" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute list_user_properties")

    async def list_event_properties(
        self
    ) -> AmplitudeResponse:
        """List all event properties in the project's taxonomy (API v2)

        Returns:
            AmplitudeResponse with operation result
        """
        url = self.base_url + "/taxonomy/event-property"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AmplitudeResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_event_properties" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AmplitudeResponse(success=False, error=str(e), message="Failed to execute list_event_properties")
