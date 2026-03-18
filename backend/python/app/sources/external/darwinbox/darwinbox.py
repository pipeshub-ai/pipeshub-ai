"""
DarwinBox REST API DataSource - Auto-generated API wrapper

Generated from DarwinBox REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.darwinbox.darwinbox import (
    DarwinBoxClient,
    DarwinBoxResponse,
)
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class DarwinBoxDataSource:
    """DarwinBox REST API DataSource

    Provides async wrapper methods for DarwinBox REST API operations:
    - Employee management
    - Department and designation management
    - Location management
    - Attendance tracking
    - Leave management
    - Payroll / salary
    - Recruitment (openings, applications)

    The base URL is determined by the domain configured in the client.
    All methods return DarwinBoxResponse objects.
    """

    def __init__(self, client: DarwinBoxClient) -> None:
        """Initialize with DarwinBoxClient.

        Args:
            client: DarwinBoxClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError(
                "HTTP client does not have get_base_url method"
            ) from exc

    def get_data_source(self) -> "DarwinBoxDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> DarwinBoxClient:
        """Return the underlying DarwinBoxClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Employees
    # -----------------------------------------------------------------------

    async def get_employees(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
    ) -> DarwinBoxResponse:
        """Get a list of employees.

        Args:
            page: Page number
            per_page: Number of results per page

        Returns:
            DarwinBoxResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)

        url = self.base_url + "/employees"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_employees"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_employees",
            )

    async def get_employee(self, employee_id: str) -> DarwinBoxResponse:
        """Get an employee by ID.

        Args:
            employee_id: The employee ID

        Returns:
            DarwinBoxResponse with operation result
        """
        url = self.base_url + "/employees/{employee_id}".format(
            employee_id=employee_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_employee"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_employee",
            )

    # -----------------------------------------------------------------------
    # Departments
    # -----------------------------------------------------------------------

    async def get_departments(self) -> DarwinBoxResponse:
        """Get a list of departments.

        Returns:
            DarwinBoxResponse with operation result
        """
        url = self.base_url + "/departments"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_departments"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_departments",
            )

    async def get_department(
        self, department_id: str
    ) -> DarwinBoxResponse:
        """Get a department by ID.

        Args:
            department_id: The department ID

        Returns:
            DarwinBoxResponse with operation result
        """
        url = self.base_url + "/departments/{department_id}".format(
            department_id=department_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_department"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_department",
            )

    # -----------------------------------------------------------------------
    # Designations
    # -----------------------------------------------------------------------

    async def get_designations(self) -> DarwinBoxResponse:
        """Get a list of designations.

        Returns:
            DarwinBoxResponse with operation result
        """
        url = self.base_url + "/designations"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_designations"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_designations",
            )

    # -----------------------------------------------------------------------
    # Locations
    # -----------------------------------------------------------------------

    async def get_locations(self) -> DarwinBoxResponse:
        """Get a list of locations.

        Returns:
            DarwinBoxResponse with operation result
        """
        url = self.base_url + "/locations"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_locations"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_locations",
            )

    # -----------------------------------------------------------------------
    # Attendance
    # -----------------------------------------------------------------------

    async def get_attendance(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
    ) -> DarwinBoxResponse:
        """Get attendance records.

        Args:
            page: Page number
            per_page: Number of results per page

        Returns:
            DarwinBoxResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)

        url = self.base_url + "/attendance"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_attendance"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_attendance",
            )

    async def get_employee_attendance(
        self, employee_id: str
    ) -> DarwinBoxResponse:
        """Get attendance records for a specific employee.

        Args:
            employee_id: The employee ID

        Returns:
            DarwinBoxResponse with operation result
        """
        url = self.base_url + "/attendance/{employee_id}".format(
            employee_id=employee_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_employee_attendance"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_employee_attendance",
            )

    # -----------------------------------------------------------------------
    # Leave
    # -----------------------------------------------------------------------

    async def get_leave_balance(
        self, employee_id: str
    ) -> DarwinBoxResponse:
        """Get leave balance for an employee.

        Args:
            employee_id: The employee ID

        Returns:
            DarwinBoxResponse with operation result
        """
        url = self.base_url + "/leave/balance/{employee_id}".format(
            employee_id=employee_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_leave_balance"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_leave_balance",
            )

    async def get_leave_requests(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
    ) -> DarwinBoxResponse:
        """Get leave requests.

        Args:
            page: Page number
            per_page: Number of results per page

        Returns:
            DarwinBoxResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)

        url = self.base_url + "/leave/requests"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_leave_requests"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_leave_requests",
            )

    # -----------------------------------------------------------------------
    # Payroll
    # -----------------------------------------------------------------------

    async def get_employee_salary(
        self, employee_id: str
    ) -> DarwinBoxResponse:
        """Get salary details for an employee.

        Args:
            employee_id: The employee ID

        Returns:
            DarwinBoxResponse with operation result
        """
        url = self.base_url + "/payroll/salary/{employee_id}".format(
            employee_id=employee_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_employee_salary"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_employee_salary",
            )

    # -----------------------------------------------------------------------
    # Recruitment
    # -----------------------------------------------------------------------

    async def get_recruitment_openings(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
    ) -> DarwinBoxResponse:
        """Get recruitment openings.

        Args:
            page: Page number
            per_page: Number of results per page

        Returns:
            DarwinBoxResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)

        url = self.base_url + "/recruitment/openings"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_recruitment_openings"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_recruitment_openings",
            )

    async def get_recruitment_applications(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
    ) -> DarwinBoxResponse:
        """Get recruitment applications.

        Args:
            page: Page number
            per_page: Number of results per page

        Returns:
            DarwinBoxResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)

        url = self.base_url + "/recruitment/applications"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return DarwinBoxResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_recruitment_applications"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return DarwinBoxResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_recruitment_applications",
            )
