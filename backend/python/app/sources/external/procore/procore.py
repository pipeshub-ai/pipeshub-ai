"""
Procore REST API DataSource - Auto-generated API wrapper

Generated from Procore REST API v1.0 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.procore.procore import ProcoreClient, ProcoreResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class ProcoreDataSource:
    """Procore REST API DataSource

    Provides async wrapper methods for Procore REST API operations:
    - Current user
    - Companies
    - Projects
    - RFIs, Submittals
    - Documents, Drawings
    - Daily logs, Incidents
    - Users (company and project level)
    - Tasks, Budgets, Change orders

    The base URL is determined by the ProcoreClient's configuration.

    All methods return ProcoreResponse objects.
    """

    def __init__(self, client: ProcoreClient) -> None:
        """Initialize with ProcoreClient.

        Args:
            client: ProcoreClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'ProcoreDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> ProcoreClient:
        """Return the underlying ProcoreClient."""
        return self._client

    async def get_me(
        self
    ) -> ProcoreResponse:
        """Get the current authenticated user

        Returns:
            ProcoreResponse with operation result
        """
        url = self.base_url + "/me"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_me" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute get_me")

    async def list_companies(
        self
    ) -> ProcoreResponse:
        """List all companies accessible to the current user

        Returns:
            ProcoreResponse with operation result
        """
        url = self.base_url + "/companies"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_companies" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_companies")

    async def get_company(
        self,
        company_id: str
    ) -> ProcoreResponse:
        """Get a specific company by ID

        Args:
            company_id: The company ID

        Returns:
            ProcoreResponse with operation result
        """
        url = self.base_url + "/companies/{company_id}".format(company_id=company_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_company" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute get_company")

    async def list_projects(
        self,
        company_id: str,
        page: int | None = None,
        per_page: int | None = None,
        filters_status_id: str | None = None
    ) -> ProcoreResponse:
        """List projects for a company

        Args:
            company_id: The company ID (required)
            page: Page number for pagination
            per_page: Number of results per page
            filters_status_id: Filter by project status ID

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['company_id'] = company_id
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if filters_status_id is not None:
            query_params['filters_status_id'] = filters_status_id

        url = self.base_url + "/projects"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_projects" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_projects")

    async def get_project(
        self,
        project_id: str
    ) -> ProcoreResponse:
        """Get a specific project by ID

        Args:
            project_id: The project ID

        Returns:
            ProcoreResponse with operation result
        """
        url = self.base_url + "/projects/{project_id}".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_project" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute get_project")

    async def list_rfis(
        self,
        project_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> ProcoreResponse:
        """List RFIs for a project

        Args:
            project_id: The project ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/projects/{project_id}/rfis".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_rfis" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_rfis")

    async def get_rfi(
        self,
        project_id: str,
        rfi_id: str
    ) -> ProcoreResponse:
        """Get a specific RFI by ID

        Args:
            project_id: The project ID
            rfi_id: The RFI ID

        Returns:
            ProcoreResponse with operation result
        """
        url = self.base_url + "/projects/{project_id}/rfis/{rfi_id}".format(project_id=project_id, rfi_id=rfi_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_rfi" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute get_rfi")

    async def list_submittals(
        self,
        project_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> ProcoreResponse:
        """List submittals for a project

        Args:
            project_id: The project ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/projects/{project_id}/submittals".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_submittals" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_submittals")

    async def get_submittal(
        self,
        project_id: str,
        submittal_id: str
    ) -> ProcoreResponse:
        """Get a specific submittal by ID

        Args:
            project_id: The project ID
            submittal_id: The submittal ID

        Returns:
            ProcoreResponse with operation result
        """
        url = self.base_url + "/projects/{project_id}/submittals/{submittal_id}".format(project_id=project_id, submittal_id=submittal_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_submittal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute get_submittal")

    async def list_documents(
        self,
        project_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> ProcoreResponse:
        """List documents for a project

        Args:
            project_id: The project ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/projects/{project_id}/documents".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_documents" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_documents")

    async def list_drawings(
        self,
        project_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> ProcoreResponse:
        """List drawings for a project

        Args:
            project_id: The project ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/projects/{project_id}/drawings".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_drawings" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_drawings")

    async def list_daily_logs(
        self,
        project_id: str,
        page: int | None = None,
        per_page: int | None = None,
        log_date: str | None = None
    ) -> ProcoreResponse:
        """List daily logs for a project

        Args:
            project_id: The project ID
            page: Page number for pagination
            per_page: Number of results per page
            log_date: Filter by log date (YYYY-MM-DD)

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if log_date is not None:
            query_params['log_date'] = log_date

        url = self.base_url + "/projects/{project_id}/daily_logs".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_daily_logs" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_daily_logs")

    async def list_incidents(
        self,
        project_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> ProcoreResponse:
        """List incidents for a project

        Args:
            project_id: The project ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/projects/{project_id}/incidents".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_incidents" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_incidents")

    async def list_company_users(
        self,
        company_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> ProcoreResponse:
        """List users for a company

        Args:
            company_id: The company ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/companies/{company_id}/users".format(company_id=company_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_company_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_company_users")

    async def list_project_users(
        self,
        project_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> ProcoreResponse:
        """List users for a project

        Args:
            project_id: The project ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/projects/{project_id}/users".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_project_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_project_users")

    async def list_tasks(
        self,
        project_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> ProcoreResponse:
        """List tasks for a project

        Args:
            project_id: The project ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/projects/{project_id}/tasks".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_tasks")

    async def list_budgets(
        self,
        project_id: str
    ) -> ProcoreResponse:
        """List budgets for a project

        Args:
            project_id: The project ID

        Returns:
            ProcoreResponse with operation result
        """
        url = self.base_url + "/projects/{project_id}/budgets".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_budgets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_budgets")

    async def list_change_orders(
        self,
        project_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> ProcoreResponse:
        """List change orders for a project

        Args:
            project_id: The project ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            ProcoreResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/projects/{project_id}/change_orders".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ProcoreResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_change_orders" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ProcoreResponse(success=False, error=str(e), message="Failed to execute list_change_orders")
