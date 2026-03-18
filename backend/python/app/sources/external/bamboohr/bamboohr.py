"""
BambooHR REST API DataSource - Auto-generated API wrapper

Generated from BambooHR REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.bamboohr.bamboohr import BambooHRClient, BambooHRResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class BambooHRDataSource:
    """BambooHR REST API DataSource

    Provides async wrapper methods for BambooHR REST API operations:
    - Employee directory and management
    - Employee files
    - Metadata (fields, tables, lists, users)
    - Custom reports and company reports
    - Time off requests and policies
    - Changed employees tracking
    - Applicant tracking (applications, job summaries)

    The base URL is determined by the BambooHRClient's configured company domain.

    All methods return BambooHRResponse objects.
    """

    def __init__(self, client: BambooHRClient) -> None:
        """Initialize with BambooHRClient.

        Args:
            client: BambooHRClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'BambooHRDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> BambooHRClient:
        """Return the underlying BambooHRClient."""
        return self._client

    async def get_employee_directory(
        self
    ) -> BambooHRResponse:
        """Get employee directory listing all active employees (API v1)

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/employees/directory"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_employee_directory" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_employee_directory")

    async def get_employee(
        self,
        employee_id: str,
        fields: str | None = None
    ) -> BambooHRResponse:
        """Get a single employee by ID (API v1)

        Args:
            employee_id: The employee ID
            fields: Comma-separated list of fields to return

        Returns:
            BambooHRResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/employees/{employee_id}".format(employee_id=employee_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_employee" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_employee")

    async def add_employee(
        self,
        employee_data: dict[str, Any]
    ) -> BambooHRResponse:
        """Add a new employee (API v1)

        Args:
            employee_data: Employee data fields (firstName, lastName, etc.)

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/employees/"

        body: dict[str, Any] = {}
        body.update(employee_data)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Accept": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed add_employee" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute add_employee")

    async def update_employee(
        self,
        employee_id: str,
        employee_data: dict[str, Any]
    ) -> BambooHRResponse:
        """Update an existing employee (API v1)

        Args:
            employee_id: The employee ID
            employee_data: Employee data fields to update

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/employees/{employee_id}".format(employee_id=employee_id)

        body: dict[str, Any] = {}
        body.update(employee_data)

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Accept": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_employee" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute update_employee")

    async def get_changed_employees(
        self,
        since: str,
        change_type: str | None = None
    ) -> BambooHRResponse:
        """Get employees that have changed since a given date (API v1)

        Args:
            since: ISO 8601 date string (e.g., 2024-01-01T00:00:00Z)
            change_type: Type of changes to return (e.g., 'inserted', 'updated', 'deleted')

        Returns:
            BambooHRResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['since'] = since
        if change_type is not None:
            query_params['type'] = change_type

        url = self.base_url + "/employees/changed"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_changed_employees" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_changed_employees")

    async def list_employee_files(
        self,
        employee_id: str
    ) -> BambooHRResponse:
        """List all files for an employee (API v1)

        Args:
            employee_id: The employee ID

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/employees/{employee_id}/files/view/".format(employee_id=employee_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_employee_files" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute list_employee_files")

    async def get_metadata_fields(
        self
    ) -> BambooHRResponse:
        """Get list of all metadata fields (API v1)

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/meta/fields/"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_metadata_fields" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_metadata_fields")

    async def get_metadata_tables(
        self
    ) -> BambooHRResponse:
        """Get list of all metadata tables (API v1)

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/meta/tables/"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_metadata_tables" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_metadata_tables")

    async def get_metadata_lists(
        self
    ) -> BambooHRResponse:
        """Get list of all metadata lists (dropdown options) (API v1)

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/meta/lists/"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_metadata_lists" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_metadata_lists")

    async def get_metadata_users(
        self
    ) -> BambooHRResponse:
        """Get list of all users with access to BambooHR (API v1)

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/meta/users/"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_metadata_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_metadata_users")

    async def run_custom_report(
        self,
        report_data: dict[str, Any],
        output_format: str | None = None
    ) -> BambooHRResponse:
        """Run a custom report with specified fields and filters (API v1)

        Args:
            output_format: Output format (e.g., 'JSON', 'CSV', 'XLS', 'XML', 'PDF')
            report_data: Report configuration (fields, filters, title, etc.)

        Returns:
            BambooHRResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if output_format is not None:
            query_params['format'] = output_format

        url = self.base_url + "/reports/custom"

        body: dict[str, Any] = {}
        body.update(report_data)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Accept": "application/json"},
                query=query_params,
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed run_custom_report" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute run_custom_report")

    async def get_company_report(
        self,
        report_id: str,
        output_format: str | None = None,
        fd: str | None = None
    ) -> BambooHRResponse:
        """Get a saved company report by ID (API v1)

        Args:
            report_id: The report ID
            output_format: Output format (e.g., 'JSON', 'CSV', 'XLS', 'XML', 'PDF')
            fd: Set to 'yes' to include field data in the response

        Returns:
            BambooHRResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if output_format is not None:
            query_params['format'] = output_format
        if fd is not None:
            query_params['fd'] = fd

        url = self.base_url + "/reports/{report_id}".format(report_id=report_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_company_report" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_company_report")

    async def get_time_off_requests(
        self,
        start: str | None = None,
        end: str | None = None,
        status: str | None = None,
        action: str | None = None,
        employeeId: str | None = None,
        time_off_type: str | None = None
    ) -> BambooHRResponse:
        """Get time off requests within a date range (API v1)

        Args:
            start: Start date (YYYY-MM-DD)
            end: End date (YYYY-MM-DD)
            status: Filter by status (approved, denied, superceded, requested, canceled)
            action: Filter by action (view, approve)
            employeeId: Filter by employee ID
            time_off_type: Filter by time off type ID

        Returns:
            BambooHRResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start is not None:
            query_params['start'] = start
        if end is not None:
            query_params['end'] = end
        if status is not None:
            query_params['status'] = status
        if action is not None:
            query_params['action'] = action
        if employeeId is not None:
            query_params['employeeId'] = employeeId
        if time_off_type is not None:
            query_params['type'] = time_off_type

        url = self.base_url + "/time_off/requests/"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_time_off_requests" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_time_off_requests")

    async def get_time_off_policies(
        self
    ) -> BambooHRResponse:
        """Get list of time off policies (API v1)

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/time_off/policies/"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_time_off_policies" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_time_off_policies")

    async def list_applications(
        self,
        page: int | None = None,
        jobId: str | None = None,
        applicationStatusId: str | None = None,
        applicationStatus: str | None = None,
        jobStatusGroups: str | None = None,
        newSince: str | None = None,
        sortBy: str | None = None,
        sortOrder: str | None = None
    ) -> BambooHRResponse:
        """List applicant tracking applications (API v1)

        Args:
            page: Page number for pagination
            jobId: Filter by job ID
            applicationStatusId: Filter by application status ID
            applicationStatus: Filter by application status name
            jobStatusGroups: Filter by job status groups (e.g., 'Active', 'Inactive')
            newSince: Filter applications created since this date (ISO 8601)
            sortBy: Sort field (e.g., 'created_date', 'first_name', 'last_name')
            sortOrder: Sort order ('ASC' or 'DESC')

        Returns:
            BambooHRResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if jobId is not None:
            query_params['jobId'] = jobId
        if applicationStatusId is not None:
            query_params['applicationStatusId'] = applicationStatusId
        if applicationStatus is not None:
            query_params['applicationStatus'] = applicationStatus
        if jobStatusGroups is not None:
            query_params['jobStatusGroups'] = jobStatusGroups
        if newSince is not None:
            query_params['newSince'] = newSince
        if sortBy is not None:
            query_params['sortBy'] = sortBy
        if sortOrder is not None:
            query_params['sortOrder'] = sortOrder

        url = self.base_url + "/applicant_tracking/applications"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_applications" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute list_applications")

    async def get_application(
        self,
        application_id: str
    ) -> BambooHRResponse:
        """Get a specific applicant tracking application (API v1)

        Args:
            application_id: The application ID

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/applicant_tracking/applications/{application_id}".format(application_id=application_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_application" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_application")

    async def get_job_summaries(
        self
    ) -> BambooHRResponse:
        """Get job summaries for applicant tracking (API v1)

        Returns:
            BambooHRResponse with operation result
        """
        url = self.base_url + "/applicant_tracking/job_summaries"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return BambooHRResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_job_summaries" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return BambooHRResponse(success=False, error=str(e), message="Failed to execute get_job_summaries")
