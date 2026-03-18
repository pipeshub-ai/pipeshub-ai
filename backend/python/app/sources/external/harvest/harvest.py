"""
Harvest REST API DataSource - Auto-generated API wrapper

Generated from Harvest REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.harvest.harvest import HarvestClient, HarvestResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class HarvestDataSource:
    """Harvest REST API DataSource

    Provides async wrapper methods for Harvest REST API operations:
    - Users and user management
    - Time entries CRUD
    - Projects and clients
    - Tasks, invoices, expenses
    - Company info, roles
    - Project assignments

    All requests require a Harvest-Account-Id header, which is set
    by the HarvestClient during initialization.

    All methods return HarvestResponse objects.
    """

    def __init__(self, client: HarvestClient) -> None:
        """Initialize with HarvestClient.

        Args:
            client: HarvestClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'HarvestDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> HarvestClient:
        """Return the underlying HarvestClient."""
        return self._client

    async def get_current_user(
        self
    ) -> HarvestResponse:
        """Get the currently authenticated user

        Returns:
            HarvestResponse with operation result
        """
        url = self.base_url + "/users/me"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_current_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute get_current_user")

    async def list_users(
        self,
        *,
        is_active: bool | None = None,
        page: int | None = None,
        per_page: int | None = None,
        updated_since: str | None = None
    ) -> HarvestResponse:
        """List all users

        Args:
            is_active: Filter by active status
            page: Page number for pagination
            per_page: Number of records per page
            updated_since: Only return users updated since this datetime (ISO 8601)

        Returns:
            HarvestResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if is_active is not None:
            query_params['is_active'] = str(is_active).lower()
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if updated_since is not None:
            query_params['updated_since'] = updated_since

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
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str
    ) -> HarvestResponse:
        """Get a specific user by ID

        Args:
            user_id: The user ID

        Returns:
            HarvestResponse with operation result
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
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute get_user")

    async def list_time_entries(
        self,
        *,
        user_id: str | None = None,
        client_id_: str | None = None,
        project_id: str | None = None,
        is_billed: bool | None = None,
        is_running: bool | None = None,
        updated_since: str | None = None,
        from_: str | None = None,
        to_: str | None = None,
        page: int | None = None,
        per_page: int | None = None
    ) -> HarvestResponse:
        """List all time entries

        Args:
            user_id: Filter by user ID
            client_id_: Filter by client ID
            project_id: Filter by project ID
            is_billed: Filter by billed status
            is_running: Filter by running status
            updated_since: Only return time entries updated since this datetime (ISO 8601)
            from_: Start date for filtering (YYYY-MM-DD)
            to_: End date for filtering (YYYY-MM-DD)
            page: Page number for pagination
            per_page: Number of records per page

        Returns:
            HarvestResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if user_id is not None:
            query_params['user_id'] = user_id
        if client_id_ is not None:
            query_params['client_id'] = client_id_
        if project_id is not None:
            query_params['project_id'] = project_id
        if is_billed is not None:
            query_params['is_billed'] = str(is_billed).lower()
        if is_running is not None:
            query_params['is_running'] = str(is_running).lower()
        if updated_since is not None:
            query_params['updated_since'] = updated_since
        if from_ is not None:
            query_params['from'] = from_
        if to_ is not None:
            query_params['to'] = to_
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/time_entries"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_time_entries" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute list_time_entries")

    async def get_time_entry(
        self,
        time_entry_id: str
    ) -> HarvestResponse:
        """Get a specific time entry by ID

        Args:
            time_entry_id: The time entry ID

        Returns:
            HarvestResponse with operation result
        """
        url = self.base_url + "/time_entries/{time_entry_id}".format(time_entry_id=time_entry_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_time_entry" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute get_time_entry")

    async def create_time_entry(
        self,
        body: dict[str, Any]
    ) -> HarvestResponse:
        """Create a new time entry

        Args:
            body: Time entry data (project_id, task_id, spent_date, etc.)

        Returns:
            HarvestResponse with operation result
        """
        url = self.base_url + "/time_entries"

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_time_entry" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute create_time_entry")

    async def update_time_entry(
        self,
        time_entry_id: str,
        body: dict[str, Any]
    ) -> HarvestResponse:
        """Update an existing time entry

        Args:
            time_entry_id: The time entry ID
            body: Time entry fields to update

        Returns:
            HarvestResponse with operation result
        """
        url = self.base_url + "/time_entries/{time_entry_id}".format(time_entry_id=time_entry_id)

        try:
            request = HTTPRequest(
                method="PATCH",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_time_entry" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute update_time_entry")

    async def delete_time_entry(
        self,
        time_entry_id: str
    ) -> HarvestResponse:
        """Delete a time entry

        Args:
            time_entry_id: The time entry ID

        Returns:
            HarvestResponse with operation result
        """
        url = self.base_url + "/time_entries/{time_entry_id}".format(time_entry_id=time_entry_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_time_entry" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute delete_time_entry")

    async def list_projects(
        self,
        *,
        is_active: bool | None = None,
        client_id_: str | None = None,
        updated_since: str | None = None,
        page: int | None = None,
        per_page: int | None = None
    ) -> HarvestResponse:
        """List all projects

        Args:
            is_active: Filter by active status
            client_id_: Filter by client ID
            updated_since: Only return projects updated since this datetime (ISO 8601)
            page: Page number for pagination
            per_page: Number of records per page

        Returns:
            HarvestResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if is_active is not None:
            query_params['is_active'] = str(is_active).lower()
        if client_id_ is not None:
            query_params['client_id'] = client_id_
        if updated_since is not None:
            query_params['updated_since'] = updated_since
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

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
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_projects" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute list_projects")

    async def get_project(
        self,
        project_id: str
    ) -> HarvestResponse:
        """Get a specific project by ID

        Args:
            project_id: The project ID

        Returns:
            HarvestResponse with operation result
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
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_project" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute get_project")

    async def list_clients(
        self,
        *,
        is_active: bool | None = None,
        updated_since: str | None = None,
        page: int | None = None,
        per_page: int | None = None
    ) -> HarvestResponse:
        """List all clients

        Args:
            is_active: Filter by active status
            updated_since: Only return clients updated since this datetime (ISO 8601)
            page: Page number for pagination
            per_page: Number of records per page

        Returns:
            HarvestResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if is_active is not None:
            query_params['is_active'] = str(is_active).lower()
        if updated_since is not None:
            query_params['updated_since'] = updated_since
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/clients"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_clients" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute list_clients")

    async def get_client_by_id(
        self,
        client_id_param: str
    ) -> HarvestResponse:
        """Get a specific client by ID

        Args:
            client_id_param: The client ID

        Returns:
            HarvestResponse with operation result
        """
        url = self.base_url + "/clients/{client_id_param}".format(client_id_param=client_id_param)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_client_by_id" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute get_client_by_id")

    async def list_tasks(
        self,
        *,
        is_active: bool | None = None,
        updated_since: str | None = None,
        page: int | None = None,
        per_page: int | None = None
    ) -> HarvestResponse:
        """List all tasks

        Args:
            is_active: Filter by active status
            updated_since: Only return tasks updated since this datetime (ISO 8601)
            page: Page number for pagination
            per_page: Number of records per page

        Returns:
            HarvestResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if is_active is not None:
            query_params['is_active'] = str(is_active).lower()
        if updated_since is not None:
            query_params['updated_since'] = updated_since
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/tasks"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute list_tasks")

    async def get_task(
        self,
        task_id: str
    ) -> HarvestResponse:
        """Get a specific task by ID

        Args:
            task_id: The task ID

        Returns:
            HarvestResponse with operation result
        """
        url = self.base_url + "/tasks/{task_id}".format(task_id=task_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute get_task")

    async def list_invoices(
        self,
        client_id_: str | None = None,
        project_id: str | None = None,
        updated_since: str | None = None,
        from_: str | None = None,
        to_: str | None = None,
        state: str | None = None,
        page: int | None = None,
        per_page: int | None = None
    ) -> HarvestResponse:
        """List all invoices

        Args:
            client_id_: Filter by client ID
            project_id: Filter by project ID
            updated_since: Only return invoices updated since this datetime (ISO 8601)
            from_: Start date for filtering (YYYY-MM-DD)
            to_: End date for filtering (YYYY-MM-DD)
            state: Filter by invoice state (draft, open, paid, closed)
            page: Page number for pagination
            per_page: Number of records per page

        Returns:
            HarvestResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if client_id_ is not None:
            query_params['client_id'] = client_id_
        if project_id is not None:
            query_params['project_id'] = project_id
        if updated_since is not None:
            query_params['updated_since'] = updated_since
        if from_ is not None:
            query_params['from'] = from_
        if to_ is not None:
            query_params['to'] = to_
        if state is not None:
            query_params['state'] = state
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/invoices"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_invoices" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute list_invoices")

    async def get_invoice(
        self,
        invoice_id: str
    ) -> HarvestResponse:
        """Get a specific invoice by ID

        Args:
            invoice_id: The invoice ID

        Returns:
            HarvestResponse with operation result
        """
        url = self.base_url + "/invoices/{invoice_id}".format(invoice_id=invoice_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_invoice" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute get_invoice")

    async def list_expenses(
        self,
        *,
        user_id: str | None = None,
        client_id_: str | None = None,
        project_id: str | None = None,
        is_billed: bool | None = None,
        updated_since: str | None = None,
        from_: str | None = None,
        to_: str | None = None,
        page: int | None = None,
        per_page: int | None = None
    ) -> HarvestResponse:
        """List all expenses

        Args:
            user_id: Filter by user ID
            client_id_: Filter by client ID
            project_id: Filter by project ID
            is_billed: Filter by billed status
            updated_since: Only return expenses updated since this datetime (ISO 8601)
            from_: Start date for filtering (YYYY-MM-DD)
            to_: End date for filtering (YYYY-MM-DD)
            page: Page number for pagination
            per_page: Number of records per page

        Returns:
            HarvestResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if user_id is not None:
            query_params['user_id'] = user_id
        if client_id_ is not None:
            query_params['client_id'] = client_id_
        if project_id is not None:
            query_params['project_id'] = project_id
        if is_billed is not None:
            query_params['is_billed'] = str(is_billed).lower()
        if updated_since is not None:
            query_params['updated_since'] = updated_since
        if from_ is not None:
            query_params['from'] = from_
        if to_ is not None:
            query_params['to'] = to_
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/expenses"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_expenses" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute list_expenses")

    async def get_expense(
        self,
        expense_id: str
    ) -> HarvestResponse:
        """Get a specific expense by ID

        Args:
            expense_id: The expense ID

        Returns:
            HarvestResponse with operation result
        """
        url = self.base_url + "/expenses/{expense_id}".format(expense_id=expense_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_expense" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute get_expense")

    async def get_company(
        self
    ) -> HarvestResponse:
        """Get the company information for the authenticated user's account

        Returns:
            HarvestResponse with operation result
        """
        url = self.base_url + "/company"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_company" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute get_company")

    async def list_roles(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> HarvestResponse:
        """List all roles

        Args:
            page: Page number for pagination
            per_page: Number of records per page

        Returns:
            HarvestResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/roles"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_roles" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute list_roles")

    async def list_project_assignments(
        self,
        page: int | None = None,
        per_page: int | None = None,
        updated_since: str | None = None
    ) -> HarvestResponse:
        """List project assignments for the currently authenticated user

        Args:
            page: Page number for pagination
            per_page: Number of records per page
            updated_since: Only return assignments updated since this datetime (ISO 8601)

        Returns:
            HarvestResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if updated_since is not None:
            query_params['updated_since'] = updated_since

        url = self.base_url + "/project_assignments"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_project_assignments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute list_project_assignments")

    async def list_user_project_assignments(
        self,
        user_id: str,
        page: int | None = None,
        per_page: int | None = None,
        updated_since: str | None = None
    ) -> HarvestResponse:
        """List project assignments for a specific user

        Args:
            user_id: The user ID
            page: Page number for pagination
            per_page: Number of records per page
            updated_since: Only return assignments updated since this datetime (ISO 8601)

        Returns:
            HarvestResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if updated_since is not None:
            query_params['updated_since'] = updated_since

        url = self.base_url + "/users/{user_id}/project_assignments".format(user_id=user_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return HarvestResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_user_project_assignments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return HarvestResponse(success=False, error=str(e), message="Failed to execute list_user_project_assignments")
