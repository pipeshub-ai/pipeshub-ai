# ruff: noqa
"""
Fellow REST API DataSource - Auto-generated API wrapper

Generated from Fellow REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.fellow.fellow import FellowClient, FellowResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class FellowDataSource:
    """Fellow REST API DataSource

    Provides async wrapper methods for Fellow REST API operations:
    - Meetings management
    - Meeting notes and action items
    - Users management
    - Streams management
    - Feedback management
    - Objectives management
    - One-on-Ones management

    All methods return FellowResponse objects.
    """

    def __init__(self, client: FellowClient) -> None:
        """Initialize with FellowClient.

        Args:
            client: FellowClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'FellowDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> FellowClient:
        """Return the underlying FellowClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Meetings
    # -----------------------------------------------------------------------

    async def list_meetings(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> FellowResponse:
        """List all meetings

        HTTP GET /meetings

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            FellowResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

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
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_meetings" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute list_meetings")


    async def get_meeting(
        self,
        meeting_id: str
    ) -> FellowResponse:
        """Get a specific meeting by ID

        HTTP GET /meetings/{meeting_id}

        Args:
            meeting_id: The meeting id

        Returns:
            FellowResponse with operation result
        """

        url = self.base_url + "/meetings/{meeting_id}".format(meeting_id=meeting_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_meeting" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute get_meeting")


    async def get_meeting_notes(
        self,
        meeting_id: str
    ) -> FellowResponse:
        """Get notes for a specific meeting

        HTTP GET /meetings/{meeting_id}/notes

        Args:
            meeting_id: The meeting id

        Returns:
            FellowResponse with operation result
        """

        url = self.base_url + "/meetings/{meeting_id}/notes".format(meeting_id=meeting_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_meeting_notes" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute get_meeting_notes")


    async def get_meeting_action_items(
        self,
        meeting_id: str
    ) -> FellowResponse:
        """Get action items for a specific meeting

        HTTP GET /meetings/{meeting_id}/action-items

        Args:
            meeting_id: The meeting id

        Returns:
            FellowResponse with operation result
        """

        url = self.base_url + "/meetings/{meeting_id}/action-items".format(meeting_id=meeting_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_meeting_action_items" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute get_meeting_action_items")


    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> FellowResponse:
        """List all users

        HTTP GET /users

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            FellowResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

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
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute list_users")


    async def get_user(
        self,
        user_id: str
    ) -> FellowResponse:
        """Get a specific user by ID

        HTTP GET /users/{user_id}

        Args:
            user_id: The user id

        Returns:
            FellowResponse with operation result
        """

        url = self.base_url + "/users/{user_id}".format(user_id=user_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute get_user")


    # -----------------------------------------------------------------------
    # Streams
    # -----------------------------------------------------------------------

    async def list_streams(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> FellowResponse:
        """List all streams

        HTTP GET /streams

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            FellowResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/streams"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_streams" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute list_streams")


    async def get_stream(
        self,
        stream_id: str
    ) -> FellowResponse:
        """Get a specific stream by ID

        HTTP GET /streams/{stream_id}

        Args:
            stream_id: The stream id

        Returns:
            FellowResponse with operation result
        """

        url = self.base_url + "/streams/{stream_id}".format(stream_id=stream_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_stream" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute get_stream")


    # -----------------------------------------------------------------------
    # Feedback
    # -----------------------------------------------------------------------

    async def list_feedback(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> FellowResponse:
        """List all feedback

        HTTP GET /feedback

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            FellowResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/feedback"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_feedback" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute list_feedback")


    async def get_feedback(
        self,
        feedback_id: str
    ) -> FellowResponse:
        """Get a specific feedback item by ID

        HTTP GET /feedback/{feedback_id}

        Args:
            feedback_id: The feedback id

        Returns:
            FellowResponse with operation result
        """

        url = self.base_url + "/feedback/{feedback_id}".format(feedback_id=feedback_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_feedback" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute get_feedback")


    # -----------------------------------------------------------------------
    # Objectives
    # -----------------------------------------------------------------------

    async def list_objectives(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> FellowResponse:
        """List all objectives

        HTTP GET /objectives

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            FellowResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/objectives"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_objectives" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute list_objectives")


    async def get_objective(
        self,
        objective_id: str
    ) -> FellowResponse:
        """Get a specific objective by ID

        HTTP GET /objectives/{objective_id}

        Args:
            objective_id: The objective id

        Returns:
            FellowResponse with operation result
        """

        url = self.base_url + "/objectives/{objective_id}".format(objective_id=objective_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_objective" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute get_objective")


    # -----------------------------------------------------------------------
    # One-on-Ones
    # -----------------------------------------------------------------------

    async def list_one_on_ones(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> FellowResponse:
        """List all one-on-ones

        HTTP GET /one-on-ones

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            FellowResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/one-on-ones"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_one_on_ones" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute list_one_on_ones")


    async def get_one_on_one(
        self,
        one_on_one_id: str
    ) -> FellowResponse:
        """Get a specific one-on-one by ID

        HTTP GET /one-on-ones/{one_on_one_id}

        Args:
            one_on_one_id: The one on one id

        Returns:
            FellowResponse with operation result
        """

        url = self.base_url + "/one-on-ones/{one_on_one_id}".format(one_on_one_id=one_on_one_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_one_on_one" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute get_one_on_one")

