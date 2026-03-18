# ruff: noqa
"""
Mindtickle REST API DataSource - Auto-generated API wrapper

Generated from Mindtickle REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.mindtickle.mindtickle import MindtickleClient, MindtickleResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class MindtickleDataSource:
    """Mindtickle REST API DataSource

    Provides async wrapper methods for Mindtickle REST API operations:
    - Users management
    - Courses and modules
    - Quizzes and assessments
    - Content management
    - Leaderboard
    - Analytics (completion and engagement)
    - Series management

    All methods return MindtickleResponse objects.
    """

    def __init__(self, client: MindtickleClient) -> None:
        """Initialize with MindtickleClient.

        Args:
            client: MindtickleClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'MindtickleDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> MindtickleClient:
        """Return the underlying MindtickleClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def get_users(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> MindtickleResponse:
        """Get all users

        Args:
            page: Page number for pagination
            page_size: Number of records per page

        Returns:
            MindtickleResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

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
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_users")

    async def get_user(
        self,
        user_id: str,
    ) -> MindtickleResponse:
        """Get a specific user by ID

        Args:
            user_id: The user ID

        Returns:
            MindtickleResponse with operation result
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
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_user")

    # -----------------------------------------------------------------------
    # Courses
    # -----------------------------------------------------------------------

    async def get_courses(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> MindtickleResponse:
        """Get all courses

        Args:
            page: Page number for pagination
            page_size: Number of records per page

        Returns:
            MindtickleResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/courses"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_courses" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_courses")

    async def get_course(
        self,
        course_id: str,
    ) -> MindtickleResponse:
        """Get a specific course by ID

        Args:
            course_id: The course ID

        Returns:
            MindtickleResponse with operation result
        """
        url = self.base_url + "/courses/{course_id}".format(course_id=course_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_course" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_course")

    # -----------------------------------------------------------------------
    # Modules
    # -----------------------------------------------------------------------

    async def get_modules(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> MindtickleResponse:
        """Get all modules

        Args:
            page: Page number for pagination
            page_size: Number of records per page

        Returns:
            MindtickleResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/modules"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_modules" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_modules")

    async def get_module(
        self,
        module_id: str,
    ) -> MindtickleResponse:
        """Get a specific module by ID

        Args:
            module_id: The module ID

        Returns:
            MindtickleResponse with operation result
        """
        url = self.base_url + "/modules/{module_id}".format(module_id=module_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_module" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_module")

    # -----------------------------------------------------------------------
    # Quizzes
    # -----------------------------------------------------------------------

    async def get_quizzes(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> MindtickleResponse:
        """Get all quizzes

        Args:
            page: Page number for pagination
            page_size: Number of records per page

        Returns:
            MindtickleResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/quizzes"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_quizzes" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_quizzes")

    async def get_quiz(
        self,
        quiz_id: str,
    ) -> MindtickleResponse:
        """Get a specific quiz by ID

        Args:
            quiz_id: The quiz ID

        Returns:
            MindtickleResponse with operation result
        """
        url = self.base_url + "/quizzes/{quiz_id}".format(quiz_id=quiz_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_quiz" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_quiz")

    # -----------------------------------------------------------------------
    # Assessments
    # -----------------------------------------------------------------------

    async def get_assessments(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> MindtickleResponse:
        """Get all assessments

        Args:
            page: Page number for pagination
            page_size: Number of records per page

        Returns:
            MindtickleResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/assessments"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_assessments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_assessments")

    async def get_assessment(
        self,
        assessment_id: str,
    ) -> MindtickleResponse:
        """Get a specific assessment by ID

        Args:
            assessment_id: The assessment ID

        Returns:
            MindtickleResponse with operation result
        """
        url = self.base_url + "/assessments/{assessment_id}".format(assessment_id=assessment_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_assessment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_assessment")

    # -----------------------------------------------------------------------
    # Content
    # -----------------------------------------------------------------------

    async def get_content(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> MindtickleResponse:
        """Get all content items

        Args:
            page: Page number for pagination
            page_size: Number of records per page

        Returns:
            MindtickleResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/content"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_content")

    async def get_content_item(
        self,
        content_id: str,
    ) -> MindtickleResponse:
        """Get a specific content item by ID

        Args:
            content_id: The content item ID

        Returns:
            MindtickleResponse with operation result
        """
        url = self.base_url + "/content/{content_id}".format(content_id=content_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_content_item" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_content_item")

    # -----------------------------------------------------------------------
    # Leaderboard
    # -----------------------------------------------------------------------

    async def get_leaderboard(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> MindtickleResponse:
        """Get the leaderboard

        Args:
            page: Page number for pagination
            page_size: Number of records per page

        Returns:
            MindtickleResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/leaderboard"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_leaderboard" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_leaderboard")

    # -----------------------------------------------------------------------
    # Analytics
    # -----------------------------------------------------------------------

    async def get_completion_analytics(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> MindtickleResponse:
        """Get completion analytics

        Args:
            page: Page number for pagination
            page_size: Number of records per page
            start_date: Start date filter (ISO 8601)
            end_date: End date filter (ISO 8601)

        Returns:
            MindtickleResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if start_date is not None:
            query_params['start_date'] = start_date
        if end_date is not None:
            query_params['end_date'] = end_date

        url = self.base_url + "/analytics/completion"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_completion_analytics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_completion_analytics")

    async def get_engagement_analytics(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> MindtickleResponse:
        """Get engagement analytics

        Args:
            page: Page number for pagination
            page_size: Number of records per page
            start_date: Start date filter (ISO 8601)
            end_date: End date filter (ISO 8601)

        Returns:
            MindtickleResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if start_date is not None:
            query_params['start_date'] = start_date
        if end_date is not None:
            query_params['end_date'] = end_date

        url = self.base_url + "/analytics/engagement"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_engagement_analytics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_engagement_analytics")

    # -----------------------------------------------------------------------
    # Series
    # -----------------------------------------------------------------------

    async def get_series(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
    ) -> MindtickleResponse:
        """Get all series

        Args:
            page: Page number for pagination
            page_size: Number of records per page

        Returns:
            MindtickleResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/series"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_series" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_series")

    async def get_series_item(
        self,
        series_id: str,
    ) -> MindtickleResponse:
        """Get a specific series by ID

        Args:
            series_id: The series ID

        Returns:
            MindtickleResponse with operation result
        """
        url = self.base_url + "/series/{series_id}".format(series_id=series_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_series_item" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute get_series_item")
