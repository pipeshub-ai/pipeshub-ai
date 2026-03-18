# ruff: noqa
"""
15Five REST API DataSource - Auto-generated API wrapper

Generated from 15Five REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.fifteenfive.fifteenfive import FifteenFiveClient, FifteenFiveResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class FifteenFiveDataSource:
    """15Five REST API DataSource

    Provides async wrapper methods for 15Five REST API operations:
    - Users management
    - Reports management
    - Reviews management
    - Objectives management
    - Pulse surveys
    - Groups management
    - Departments management
    - High-fives
    - One-on-ones

    All methods return FifteenFiveResponse objects.
    """

    def __init__(self, client: FifteenFiveClient) -> None:
        """Initialize with FifteenFiveClient.

        Args:
            client: FifteenFiveClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'FifteenFiveDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> FifteenFiveClient:
        """Return the underlying FifteenFiveClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None
    ) -> FifteenFiveResponse:
        """List all users

        HTTP GET /user

        Args:
            page: Page number for pagination
            page_size: Number of items per page

        Returns:
            FifteenFiveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/user"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str
    ) -> FifteenFiveResponse:
        """Get a specific user by ID

        HTTP GET /user/{user_id}

        Args:
            user_id: The user ID

        Returns:
            FifteenFiveResponse with operation result
        """
        url = self.base_url + "/user/{user_id}".format(user_id=user_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute get_user")

    # -----------------------------------------------------------------------
    # Reports
    # -----------------------------------------------------------------------

    async def list_reports(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None
    ) -> FifteenFiveResponse:
        """List all reports

        HTTP GET /report

        Args:
            page: Page number for pagination
            page_size: Number of items per page

        Returns:
            FifteenFiveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/report"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_reports" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute list_reports")

    async def get_report(
        self,
        report_id: str
    ) -> FifteenFiveResponse:
        """Get a specific report by ID

        HTTP GET /report/{report_id}

        Args:
            report_id: The report ID

        Returns:
            FifteenFiveResponse with operation result
        """
        url = self.base_url + "/report/{report_id}".format(report_id=report_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_report" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute get_report")

    # -----------------------------------------------------------------------
    # Reviews
    # -----------------------------------------------------------------------

    async def list_reviews(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None
    ) -> FifteenFiveResponse:
        """List all reviews

        HTTP GET /review

        Args:
            page: Page number for pagination
            page_size: Number of items per page

        Returns:
            FifteenFiveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/review"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_reviews" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute list_reviews")

    async def get_review(
        self,
        review_id: str
    ) -> FifteenFiveResponse:
        """Get a specific review by ID

        HTTP GET /review/{review_id}

        Args:
            review_id: The review ID

        Returns:
            FifteenFiveResponse with operation result
        """
        url = self.base_url + "/review/{review_id}".format(review_id=review_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_review" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute get_review")

    # -----------------------------------------------------------------------
    # Objectives
    # -----------------------------------------------------------------------

    async def list_objectives(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None
    ) -> FifteenFiveResponse:
        """List all objectives

        HTTP GET /objective

        Args:
            page: Page number for pagination
            page_size: Number of items per page

        Returns:
            FifteenFiveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/objective"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_objectives" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute list_objectives")

    async def get_objective(
        self,
        objective_id: str
    ) -> FifteenFiveResponse:
        """Get a specific objective by ID

        HTTP GET /objective/{objective_id}

        Args:
            objective_id: The objective ID

        Returns:
            FifteenFiveResponse with operation result
        """
        url = self.base_url + "/objective/{objective_id}".format(objective_id=objective_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_objective" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute get_objective")

    # -----------------------------------------------------------------------
    # Pulse
    # -----------------------------------------------------------------------

    async def list_pulses(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None
    ) -> FifteenFiveResponse:
        """List all pulse surveys

        HTTP GET /pulse

        Args:
            page: Page number for pagination
            page_size: Number of items per page

        Returns:
            FifteenFiveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/pulse"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_pulses" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute list_pulses")

    async def get_pulse(
        self,
        pulse_id: str
    ) -> FifteenFiveResponse:
        """Get a specific pulse survey by ID

        HTTP GET /pulse/{pulse_id}

        Args:
            pulse_id: The pulse ID

        Returns:
            FifteenFiveResponse with operation result
        """
        url = self.base_url + "/pulse/{pulse_id}".format(pulse_id=pulse_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_pulse" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute get_pulse")

    # -----------------------------------------------------------------------
    # Groups
    # -----------------------------------------------------------------------

    async def list_groups(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None
    ) -> FifteenFiveResponse:
        """List all groups

        HTTP GET /group

        Args:
            page: Page number for pagination
            page_size: Number of items per page

        Returns:
            FifteenFiveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/group"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute list_groups")

    async def get_group(
        self,
        group_id: str
    ) -> FifteenFiveResponse:
        """Get a specific group by ID

        HTTP GET /group/{group_id}

        Args:
            group_id: The group ID

        Returns:
            FifteenFiveResponse with operation result
        """
        url = self.base_url + "/group/{group_id}".format(group_id=group_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute get_group")

    # -----------------------------------------------------------------------
    # Departments
    # -----------------------------------------------------------------------

    async def list_departments(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None
    ) -> FifteenFiveResponse:
        """List all departments

        HTTP GET /department

        Args:
            page: Page number for pagination
            page_size: Number of items per page

        Returns:
            FifteenFiveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/department"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_departments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute list_departments")

    async def get_department(
        self,
        department_id: str
    ) -> FifteenFiveResponse:
        """Get a specific department by ID

        HTTP GET /department/{department_id}

        Args:
            department_id: The department ID

        Returns:
            FifteenFiveResponse with operation result
        """
        url = self.base_url + "/department/{department_id}".format(department_id=department_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_department" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute get_department")

    # -----------------------------------------------------------------------
    # High-Fives
    # -----------------------------------------------------------------------

    async def list_high_fives(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None
    ) -> FifteenFiveResponse:
        """List all high-fives

        HTTP GET /high-five

        Args:
            page: Page number for pagination
            page_size: Number of items per page

        Returns:
            FifteenFiveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/high-five"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_high_fives" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute list_high_fives")

    async def get_high_five(
        self,
        high_five_id: str
    ) -> FifteenFiveResponse:
        """Get a specific high-five by ID

        HTTP GET /high-five/{high_five_id}

        Args:
            high_five_id: The high-five ID

        Returns:
            FifteenFiveResponse with operation result
        """
        url = self.base_url + "/high-five/{high_five_id}".format(high_five_id=high_five_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_high_five" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute get_high_five")

    # -----------------------------------------------------------------------
    # One-on-Ones
    # -----------------------------------------------------------------------

    async def list_one_on_ones(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None
    ) -> FifteenFiveResponse:
        """List all one-on-ones

        HTTP GET /one-on-one

        Args:
            page: Page number for pagination
            page_size: Number of items per page

        Returns:
            FifteenFiveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)

        url = self.base_url + "/one-on-one"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_one_on_ones" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute list_one_on_ones")

    async def get_one_on_one(
        self,
        one_on_one_id: str
    ) -> FifteenFiveResponse:
        """Get a specific one-on-one by ID

        HTTP GET /one-on-one/{one_on_one_id}

        Args:
            one_on_one_id: The one-on-one ID

        Returns:
            FifteenFiveResponse with operation result
        """
        url = self.base_url + "/one-on-one/{one_on_one_id}".format(one_on_one_id=one_on_one_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_one_on_one" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute get_one_on_one")
