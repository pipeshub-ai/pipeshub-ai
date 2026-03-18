"""
Ironclad REST API DataSource - Auto-generated API wrapper

Generated from Ironclad REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.ironclad.ironclad import IroncladClient, IroncladResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class IroncladDataSource:
    """Ironclad REST API DataSource

    Provides async wrapper methods for Ironclad REST API operations:
    - Workflow management (list, get, launch, update)
    - Workflow approvals
    - Records management
    - Templates
    - Webhooks
    - Users and Groups

    The base URL is determined by the IroncladClient's configuration.

    All methods return IroncladResponse objects.
    """

    def __init__(self, client: IroncladClient) -> None:
        """Initialize with IroncladClient.

        Args:
            client: IroncladClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'IroncladDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> IroncladClient:
        """Return the underlying IroncladClient."""
        return self._client

    async def list_workflows(
        self,
        page: int | None = None,
        page_size: int | None = None,
        status: str | None = None,
        template_id: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None
    ) -> IroncladResponse:
        """List workflows with optional filters

        Args:
            page: Page number for pagination
            page_size: Number of results per page
            status: Filter by workflow status
            template_id: Filter by template ID
            created_after: Filter workflows created after this ISO 8601 date
            created_before: Filter workflows created before this ISO 8601 date

        Returns:
            IroncladResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if status is not None:
            query_params['status'] = status
        if template_id is not None:
            query_params['template_id'] = template_id
        if created_after is not None:
            query_params['created_after'] = created_after
        if created_before is not None:
            query_params['created_before'] = created_before

        url = self.base_url + "/workflows"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_workflows" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute list_workflows")

    async def get_workflow(
        self,
        workflow_id: str
    ) -> IroncladResponse:
        """Get a specific workflow by ID

        Args:
            workflow_id: The workflow ID

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/workflows/{workflow_id}".format(workflow_id=workflow_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_workflow" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute get_workflow")

    async def launch_workflow(
        self,
        template_id: str,
        attributes: dict[str, Any] | None = None,
        creator: dict[str, Any] | None = None
    ) -> IroncladResponse:
        """Launch a new workflow

        Args:
            template_id: The template ID to launch the workflow from
            attributes: Workflow attribute values
            creator: Creator information

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/workflows"

        body: dict[str, Any] = {}
        body['template_id'] = template_id
        if attributes is not None:
            body['attributes'] = attributes
        if creator is not None:
            body['creator'] = creator

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed launch_workflow" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute launch_workflow")

    async def update_workflow(
        self,
        workflow_id: str,
        attributes: dict[str, Any] | None = None
    ) -> IroncladResponse:
        """Update a workflow

        Args:
            workflow_id: The workflow ID
            attributes: Workflow attribute values to update

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/workflows/{workflow_id}".format(workflow_id=workflow_id)

        body: dict[str, Any] = {}
        if attributes is not None:
            body['attributes'] = attributes

        try:
            request = HTTPRequest(
                method="PATCH",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_workflow" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute update_workflow")

    async def list_workflow_approvals(
        self,
        workflow_id: str
    ) -> IroncladResponse:
        """List approvals for a workflow

        Args:
            workflow_id: The workflow ID

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/workflows/{workflow_id}/approvals".format(workflow_id=workflow_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_workflow_approvals" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute list_workflow_approvals")

    async def create_workflow_approval(
        self,
        workflow_id: str,
        role_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None
    ) -> IroncladResponse:
        """Create an approval for a workflow

        Args:
            workflow_id: The workflow ID
            role_id: The role ID for the approval
            user_id: The user ID for the approval
            status: Approval status

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/workflows/{workflow_id}/approvals".format(workflow_id=workflow_id)

        body: dict[str, Any] = {}
        if role_id is not None:
            body['role_id'] = role_id
        if user_id is not None:
            body['user_id'] = user_id
        if status is not None:
            body['status'] = status

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_workflow_approval" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute create_workflow_approval")

    async def list_records(
        self,
        page: int | None = None,
        page_size: int | None = None,
        template_id: str | None = None,
        filter_value: str | None = None
    ) -> IroncladResponse:
        """List records with optional filters

        Args:
            page: Page number for pagination
            page_size: Number of results per page
            template_id: Filter by template ID
            filter_value: Filter expression

        Returns:
            IroncladResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if template_id is not None:
            query_params['template_id'] = template_id
        if filter_value is not None:
            query_params['filter'] = filter_value

        url = self.base_url + "/records"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_records" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute list_records")

    async def get_record(
        self,
        record_id: str
    ) -> IroncladResponse:
        """Get a specific record by ID

        Args:
            record_id: The record ID

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/records/{record_id}".format(record_id=record_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_record" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute get_record")

    async def update_record(
        self,
        record_id: str,
        attributes: dict[str, Any] | None = None
    ) -> IroncladResponse:
        """Update a record

        Args:
            record_id: The record ID
            attributes: Record attribute values to update

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/records/{record_id}".format(record_id=record_id)

        body: dict[str, Any] = {}
        if attributes is not None:
            body['attributes'] = attributes

        try:
            request = HTTPRequest(
                method="PATCH",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_record" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute update_record")

    async def list_templates(
        self
    ) -> IroncladResponse:
        """List all templates

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/templates"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_templates" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute list_templates")

    async def get_template(
        self,
        template_id: str
    ) -> IroncladResponse:
        """Get a specific template by ID

        Args:
            template_id: The template ID

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/templates/{template_id}".format(template_id=template_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_template" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute get_template")

    async def list_webhooks(
        self
    ) -> IroncladResponse:
        """List all webhooks

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/webhooks"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_webhooks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute list_webhooks")

    async def create_webhook(
        self,
        target_url: str,
        events: list[str] | None = None
    ) -> IroncladResponse:
        """Create a webhook

        Args:
            target_url: The URL to send webhook events to
            events: List of event types to subscribe to

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/webhooks"

        body: dict[str, Any] = {}
        body['target_url'] = target_url
        if events is not None:
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
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute create_webhook")

    async def delete_webhook(
        self,
        webhook_id: str
    ) -> IroncladResponse:
        """Delete a webhook

        Args:
            webhook_id: The webhook ID

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/webhooks/{webhook_id}".format(webhook_id=webhook_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute delete_webhook")

    async def list_users(
        self,
        page: int | None = None,
        page_size: int | None = None
    ) -> IroncladResponse:
        """List users with optional pagination

        Args:
            page: Page number for pagination
            page_size: Number of results per page

        Returns:
            IroncladResponse with operation result
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
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def list_groups(
        self
    ) -> IroncladResponse:
        """List all groups

        Returns:
            IroncladResponse with operation result
        """
        url = self.base_url + "/groups"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IroncladResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IroncladResponse(success=False, error=str(e), message="Failed to execute list_groups")
