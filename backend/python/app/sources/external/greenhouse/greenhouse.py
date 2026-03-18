"""
Greenhouse Harvest REST API DataSource - Auto-generated API wrapper

Generated from Greenhouse Harvest API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.greenhouse.greenhouse import (
    GreenhouseClient,
    GreenhouseResponse,
)
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class GreenhouseDataSource:
    """Greenhouse Harvest REST API DataSource

    Provides async wrapper methods for Greenhouse Harvest API operations:
    - Candidates and Applications
    - Jobs and Job Stages
    - Offers
    - Departments and Offices
    - Users
    - Scorecards, Scheduled Interviews
    - Sources, Rejection Reasons, Custom Fields
    - Activity Feed

    The base URL is https://harvest.greenhouse.io/v1.

    All methods return GreenhouseResponse objects.
    """

    def __init__(self, client: GreenhouseClient) -> None:
        """Initialize with GreenhouseClient.

        Args:
            client: GreenhouseClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'GreenhouseDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> GreenhouseClient:
        """Return the underlying GreenhouseClient."""
        return self._client

    async def list_candidates(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        job_id: str | None = None
    ) -> GreenhouseResponse:
        """List all candidates

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve
            created_after: Return candidates created after this date (ISO 8601)
            created_before: Return candidates created before this date (ISO 8601)
            updated_after: Return candidates updated after this date (ISO 8601)
            updated_before: Return candidates updated before this date (ISO 8601)
            job_id: Filter candidates by job ID

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)
        if created_after is not None:
            query_params['created_after'] = created_after
        if created_before is not None:
            query_params['created_before'] = created_before
        if updated_after is not None:
            query_params['updated_after'] = updated_after
        if updated_before is not None:
            query_params['updated_before'] = updated_before
        if job_id is not None:
            query_params['job_id'] = job_id

        url = self.base_url + "/candidates"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_candidates" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_candidates")

    async def get_candidate(
        self,
        candidate_id: str
    ) -> GreenhouseResponse:
        """Get a single candidate by ID

        Args:
            candidate_id: The candidate ID

        Returns:
            GreenhouseResponse with operation result
        """
        url = self.base_url + "/candidates/{candidate_id}".format(candidate_id=candidate_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_candidate" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute get_candidate")

    async def list_applications(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        last_activity_after: str | None = None,
        job_id: str | None = None,
        status: str | None = None
    ) -> GreenhouseResponse:
        """List all applications

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve
            created_after: Return applications created after this date (ISO 8601)
            created_before: Return applications created before this date (ISO 8601)
            last_activity_after: Return applications with activity after this date (ISO 8601)
            job_id: Filter applications by job ID
            status: Filter by application status (active, converted, hired, rejected)

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)
        if created_after is not None:
            query_params['created_after'] = created_after
        if created_before is not None:
            query_params['created_before'] = created_before
        if last_activity_after is not None:
            query_params['last_activity_after'] = last_activity_after
        if job_id is not None:
            query_params['job_id'] = job_id
        if status is not None:
            query_params['status'] = status

        url = self.base_url + "/applications"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_applications" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_applications")

    async def get_application(
        self,
        application_id: str
    ) -> GreenhouseResponse:
        """Get a single application by ID

        Args:
            application_id: The application ID

        Returns:
            GreenhouseResponse with operation result
        """
        url = self.base_url + "/applications/{application_id}".format(application_id=application_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_application" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute get_application")

    async def list_jobs(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None,
        status: str | None = None,
        department_id: str | None = None,
        office_id: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None
    ) -> GreenhouseResponse:
        """List all jobs

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve
            status: Filter by job status (open, closed, draft)
            department_id: Filter jobs by department ID
            office_id: Filter jobs by office ID
            created_after: Return jobs created after this date (ISO 8601)
            created_before: Return jobs created before this date (ISO 8601)
            updated_after: Return jobs updated after this date (ISO 8601)
            updated_before: Return jobs updated before this date (ISO 8601)

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)
        if status is not None:
            query_params['status'] = status
        if department_id is not None:
            query_params['department_id'] = department_id
        if office_id is not None:
            query_params['office_id'] = office_id
        if created_after is not None:
            query_params['created_after'] = created_after
        if created_before is not None:
            query_params['created_before'] = created_before
        if updated_after is not None:
            query_params['updated_after'] = updated_after
        if updated_before is not None:
            query_params['updated_before'] = updated_before

        url = self.base_url + "/jobs"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_jobs" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_jobs")

    async def get_job(
        self,
        job_id: str
    ) -> GreenhouseResponse:
        """Get a single job by ID

        Args:
            job_id: The job ID

        Returns:
            GreenhouseResponse with operation result
        """
        url = self.base_url + "/jobs/{job_id}".format(job_id=job_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_job" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute get_job")

    async def list_job_stages(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None,
        created_after: str | None = None,
        updated_after: str | None = None
    ) -> GreenhouseResponse:
        """List all job stages

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve
            created_after: Return job stages created after this date (ISO 8601)
            updated_after: Return job stages updated after this date (ISO 8601)

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)
        if created_after is not None:
            query_params['created_after'] = created_after
        if updated_after is not None:
            query_params['updated_after'] = updated_after

        url = self.base_url + "/job_stages"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_job_stages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_job_stages")

    async def get_job_stage(
        self,
        job_stage_id: str
    ) -> GreenhouseResponse:
        """Get a single job stage by ID

        Args:
            job_stage_id: The job stage ID

        Returns:
            GreenhouseResponse with operation result
        """
        url = self.base_url + "/job_stages/{job_stage_id}".format(job_stage_id=job_stage_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_job_stage" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute get_job_stage")

    async def list_offers(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        updated_after: str | None = None,
        updated_before: str | None = None,
        status: str | None = None
    ) -> GreenhouseResponse:
        """List all offers

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve
            created_after: Return offers created after this date (ISO 8601)
            created_before: Return offers created before this date (ISO 8601)
            updated_after: Return offers updated after this date (ISO 8601)
            updated_before: Return offers updated before this date (ISO 8601)
            status: Filter by offer status (unresolved, accepted, rejected, deprecated)

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)
        if created_after is not None:
            query_params['created_after'] = created_after
        if created_before is not None:
            query_params['created_before'] = created_before
        if updated_after is not None:
            query_params['updated_after'] = updated_after
        if updated_before is not None:
            query_params['updated_before'] = updated_before
        if status is not None:
            query_params['status'] = status

        url = self.base_url + "/offers"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_offers" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_offers")

    async def get_offer(
        self,
        offer_id: str
    ) -> GreenhouseResponse:
        """Get a single offer by ID

        Args:
            offer_id: The offer ID

        Returns:
            GreenhouseResponse with operation result
        """
        url = self.base_url + "/offers/{offer_id}".format(offer_id=offer_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_offer" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute get_offer")

    async def list_departments(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None
    ) -> GreenhouseResponse:
        """List all departments

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)

        url = self.base_url + "/departments"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_departments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_departments")

    async def get_department(
        self,
        department_id: str
    ) -> GreenhouseResponse:
        """Get a single department by ID

        Args:
            department_id: The department ID

        Returns:
            GreenhouseResponse with operation result
        """
        url = self.base_url + "/departments/{department_id}".format(department_id=department_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_department" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute get_department")

    async def list_offices(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None
    ) -> GreenhouseResponse:
        """List all offices

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)

        url = self.base_url + "/offices"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_offices" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_offices")

    async def get_office(
        self,
        office_id: str
    ) -> GreenhouseResponse:
        """Get a single office by ID

        Args:
            office_id: The office ID

        Returns:
            GreenhouseResponse with operation result
        """
        url = self.base_url + "/offices/{office_id}".format(office_id=office_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_office" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute get_office")

    async def list_users(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None,
        created_after: str | None = None,
        updated_after: str | None = None,
        email: str | None = None
    ) -> GreenhouseResponse:
        """List all users

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve
            created_after: Return users created after this date (ISO 8601)
            updated_after: Return users updated after this date (ISO 8601)
            email: Filter users by email address

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)
        if created_after is not None:
            query_params['created_after'] = created_after
        if updated_after is not None:
            query_params['updated_after'] = updated_after
        if email is not None:
            query_params['email'] = email

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
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str
    ) -> GreenhouseResponse:
        """Get a single user by ID

        Args:
            user_id: The user ID

        Returns:
            GreenhouseResponse with operation result
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
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute get_user")

    async def list_scorecards(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None,
        created_after: str | None = None,
        updated_after: str | None = None,
        application_id: str | None = None
    ) -> GreenhouseResponse:
        """List all scorecards

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve
            created_after: Return scorecards created after this date (ISO 8601)
            updated_after: Return scorecards updated after this date (ISO 8601)
            application_id: Filter scorecards by application ID

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)
        if created_after is not None:
            query_params['created_after'] = created_after
        if updated_after is not None:
            query_params['updated_after'] = updated_after
        if application_id is not None:
            query_params['application_id'] = application_id

        url = self.base_url + "/scorecards"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_scorecards" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_scorecards")

    async def list_scheduled_interviews(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None,
        created_after: str | None = None,
        updated_after: str | None = None,
        starts_after: str | None = None,
        starts_before: str | None = None
    ) -> GreenhouseResponse:
        """List all scheduled interviews

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve
            created_after: Return interviews created after this date (ISO 8601)
            updated_after: Return interviews updated after this date (ISO 8601)
            starts_after: Return interviews starting after this date (ISO 8601)
            starts_before: Return interviews starting before this date (ISO 8601)

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)
        if created_after is not None:
            query_params['created_after'] = created_after
        if updated_after is not None:
            query_params['updated_after'] = updated_after
        if starts_after is not None:
            query_params['starts_after'] = starts_after
        if starts_before is not None:
            query_params['starts_before'] = starts_before

        url = self.base_url + "/scheduled_interviews"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_scheduled_interviews" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_scheduled_interviews")

    async def list_sources(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None
    ) -> GreenhouseResponse:
        """List all sources

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)

        url = self.base_url + "/sources"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_sources" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_sources")

    async def list_rejection_reasons(
        self,
        *,
        per_page: int | None = None,
        page: int | None = None
    ) -> GreenhouseResponse:
        """List all rejection reasons

        Args:
            per_page: Number of results per page (max 500)
            page: Page number to retrieve

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)

        url = self.base_url + "/rejection_reasons"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_rejection_reasons" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_rejection_reasons")

    async def list_custom_fields(
        self,
        *,
        field_type: str | None = None
    ) -> GreenhouseResponse:
        """List all custom fields

        Args:
            field_type: Filter by field type (candidate, application, offer, job, etc.)

        Returns:
            GreenhouseResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if field_type is not None:
            query_params['field_type'] = field_type

        url = self.base_url + "/custom_fields"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_custom_fields" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute list_custom_fields")

    async def get_activity_feed(
        self,
        candidate_id: str
    ) -> GreenhouseResponse:
        """Get the activity feed for a candidate

        Args:
            candidate_id: The candidate ID

        Returns:
            GreenhouseResponse with operation result
        """
        url = self.base_url + "/candidates/{candidate_id}/activity_feed".format(candidate_id=candidate_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return GreenhouseResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_activity_feed" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return GreenhouseResponse(success=False, error=str(e), message="Failed to execute get_activity_feed")
