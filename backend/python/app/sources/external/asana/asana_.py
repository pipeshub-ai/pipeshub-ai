"""
Asana API DataSource

Auto-generated comprehensive Asana API client using official Python SDK.
Covers all Asana API endpoints with strongly-typed parameters.

Total API Classes: 31
Total Methods: 166
"""

import asyncio
from typing import Any, Dict, Optional

import asana
from asana.rest import ApiException

# Import from our client module
from app.sources.client.asana.asana import AsanaClient, AsanaResponse


class AsanaDataSource:
    """Comprehensive Asana API DataSource wrapper.

    Uses the official Asana Python SDK through our AsanaClient wrapper.
    Covers 31 API classes with 166 methods.

    All methods are async and return AsanaResponse objects.

    Example:
        >>> from app.sources.client.asana.asana import AsanaClient, AsanaTokenConfig
        >>> from app.sources.external.asana.asana import AsanaDataSource
        >>>
        >>> # Create client with token
        >>> client = AsanaClient.build_with_config(
        ...     AsanaTokenConfig(access_token="your_token_here")
        ... )
        >>>
        >>> # Create datasource
        >>> datasource = AsanaDataSource(client)
        >>>
        >>> # Use the datasource
        >>> response = await datasource.get_user(user_gid="me")
        >>> if response.success:
        ...     print(response.data)
    """

    def __init__(self, client: AsanaClient) -> None:
        """Initialize AsanaDataSource with an AsanaClient instance.

        Args:
            client: AsanaClient instance (created via build_with_config or build_from_services)
        """
        self.client = client

    def _get_api_client(self) -> asana.ApiClient:
        """Get the underlying Asana SDK API client.

        Returns:
            asana.ApiClient instance from the wrapped client
        """
        return self.client.get_api_client()

    def get_client(self) -> AsanaClient:
        """Get the wrapped AsanaClient instance.

        Returns:
            AsanaClient instance
        """
        return self.client

    # ========================================================================
    # AccessRequestsApi - 4 methods
    # ========================================================================

    async def approve_access_request(self, access_request_gid: str) -> AsanaResponse:
        """
        Approve an access request

        Args:
            access_request_gid: Access request GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AccessRequestsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.approve_access_request(access_request_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_access_request(self, body: Dict[str, Any]) -> AsanaResponse:
        """
        Create an access request

        Args:
            body: Access request data

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AccessRequestsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_access_request(body)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_access_requests(self, target: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get access requests

        Args:
            target: Target object GID
            opts: Options including user, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AccessRequestsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_access_requests(target, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def reject_access_request(self, access_request_gid: str) -> AsanaResponse:
        """
        Reject an access request

        Args:
            access_request_gid: Access request GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AccessRequestsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.reject_access_request(access_request_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # AllocationsApi - 5 methods
    # ========================================================================

    async def create_allocation(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create an allocation

        Args:
            body: Allocation data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AllocationsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_allocation(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_allocation(self, allocation_gid: str) -> AsanaResponse:
        """
        Delete an allocation

        Args:
            allocation_gid: Allocation GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AllocationsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_allocation(allocation_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_allocation(self, allocation_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get an allocation by GID

        Args:
            allocation_gid: Allocation GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AllocationsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_allocation(allocation_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_allocations(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple allocations

        Args:
            opts: Options including parent, assignee, workspace, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AllocationsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_allocations(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_allocation(self, body: Dict[str, Any], allocation_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update an allocation

        Args:
            body: Allocation updates
            allocation_gid: Allocation GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AllocationsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_allocation(body, allocation_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # AttachmentsApi - 4 methods
    # ========================================================================

    async def create_attachment_for_object(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Upload an attachment

        Args:
            opts: Options including resource_subtype, file, parent, url, name

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AttachmentsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_attachment_for_object(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_attachment(self, attachment_gid: str) -> AsanaResponse:
        """
        Delete an attachment

        Args:
            attachment_gid: Attachment GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AttachmentsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_attachment(attachment_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_attachment(self, attachment_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get an attachment by GID

        Args:
            attachment_gid: Attachment GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AttachmentsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_attachment(attachment_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_attachments_for_object(self, parent: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get attachments for an object

        Args:
            parent: Parent GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AttachmentsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_attachments_for_object(parent, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # AuditLogAPIApi - 1 methods
    # ========================================================================

    async def get_audit_log_events(self, workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get audit log events

        Args:
            workspace_gid: Workspace GID
            opts: Options including start_at, end_at, event_type, actor_type, actor_gid, resource_gid, limit, offset

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.AuditLogAPIApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_audit_log_events(workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # BatchAPIApi - 1 methods
    # ========================================================================

    async def create_batch_request(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Submit parallel requests

        Args:
            body: Batch request data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.BatchAPIApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_batch_request(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # CustomFieldSettingsApi - 2 methods
    # ========================================================================

    async def get_custom_field_settings_for_portfolio(self, portfolio_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get custom field settings for a portfolio

        Args:
            portfolio_gid: Portfolio GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.CustomFieldSettingsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_custom_field_settings_for_portfolio(portfolio_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_custom_field_settings_for_project(self, project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get custom field settings for a project

        Args:
            project_gid: Project GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.CustomFieldSettingsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_custom_field_settings_for_project(project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # CustomFieldsApi - 8 methods
    # ========================================================================

    async def create_custom_field(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a custom field

        Args:
            body: Custom field data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.CustomFieldsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_custom_field(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_enum_option_for_custom_field(self, custom_field_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create an enum option

        Args:
            custom_field_gid: Custom field GID
            opts: Options including body and opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.CustomFieldsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_enum_option_for_custom_field(custom_field_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_custom_field(self, custom_field_gid: str) -> AsanaResponse:
        """
        Delete a custom field

        Args:
            custom_field_gid: Custom field GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.CustomFieldsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_custom_field(custom_field_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_custom_field(self, custom_field_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a custom field by GID

        Args:
            custom_field_gid: Custom field GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.CustomFieldsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_custom_field(custom_field_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_custom_fields_for_workspace(self, workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get custom fields in a workspace

        Args:
            workspace_gid: Workspace GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.CustomFieldsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_custom_fields_for_workspace(workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def insert_enum_option_for_custom_field(self, custom_field_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Reorder a custom field enum

        Args:
            custom_field_gid: Custom field GID
            opts: Options including body and opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.CustomFieldsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.insert_enum_option_for_custom_field(custom_field_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_custom_field(self, custom_field_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a custom field

        Args:
            custom_field_gid: Custom field GID
            opts: Options including body and opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.CustomFieldsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_custom_field(custom_field_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_enum_option(self, enum_option_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update an enum option

        Args:
            enum_option_gid: Enum option GID
            opts: Options including body and opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.CustomFieldsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_enum_option(enum_option_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # EventsApi - 1 methods
    # ========================================================================

    async def get_events(self, resource: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get events

        Args:
            resource: Resource GID to watch
            opts: Options including sync

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.EventsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_events(resource, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # ExportsApi - 2 methods
    # ========================================================================

    async def create_graph_export(self, body: Dict[str, Any]) -> AsanaResponse:
        """
        Initiate a graph export

        Args:
            body: Export configuration

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ExportsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_graph_export(body)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_resource_export(self, body: Dict[str, Any]) -> AsanaResponse:
        """
        Initiate a resource export

        Args:
            body: Export configuration

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ExportsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_resource_export(body)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # GoalsApi - 13 methods
    # ========================================================================

    async def add_followers(self, body: Dict[str, Any], goal_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add followers to a goal

        Args:
            body: Followers to add
            goal_gid: Goal GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_followers(body, goal_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def add_subgoal(self, body: Dict[str, Any], goal_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add a subgoal to a goal

        Args:
            body: Subgoal to add
            goal_gid: Goal GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_subgoal(body, goal_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def add_supporting_work_for_goal(self, body: Dict[str, Any], goal_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add supporting work to a goal

        Args:
            body: Supporting work to add
            goal_gid: Goal GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_supporting_work_for_goal(body, goal_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_goal(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a goal

        Args:
            body: Goal data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_goal(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_goal_metric(self, body: Dict[str, Any], goal_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a goal metric

        Args:
            body: Metric data
            goal_gid: Goal GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_goal_metric(body, goal_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_goal(self, goal_gid: str) -> AsanaResponse:
        """
        Delete a goal

        Args:
            goal_gid: Goal GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_goal(goal_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_goal(self, goal_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a goal by GID

        Args:
            goal_gid: Goal GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_goal(goal_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_goals(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple goals

        Args:
            opts: Options including portfolio, project, task, is_workspace_level, team, workspace, time_periods, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_goals(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_parent_goals_for_goal(self, goal_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get parent goals from a goal

        Args:
            goal_gid: Goal GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_parent_goals_for_goal(goal_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_followers(self, body: Dict[str, Any], goal_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Remove followers from a goal

        Args:
            body: Followers to remove
            goal_gid: Goal GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_followers(body, goal_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_supporting_work_for_goal(self, body: Dict[str, Any], goal_gid: str) -> AsanaResponse:
        """
        Remove supporting work from a goal

        Args:
            body: Supporting work to remove
            goal_gid: Goal GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_supporting_work_for_goal(body, goal_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_goal(self, body: Dict[str, Any], goal_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a goal

        Args:
            body: Goal updates
            goal_gid: Goal GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_goal(body, goal_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_goal_metric(self, body: Dict[str, Any], goal_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a goal metric

        Args:
            body: Metric updates
            goal_gid: Goal GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.GoalsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_goal_metric(body, goal_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # JobsApi - 1 methods
    # ========================================================================

    async def get_job(self, job_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a job by GID

        Args:
            job_gid: Job GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.JobsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_job(job_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # MembershipsApi - 5 methods
    # ========================================================================

    async def create_membership(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a membership

        Args:
            opts: Options including body

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.MembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_membership(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_membership(self, membership_gid: str) -> AsanaResponse:
        """
        Delete a membership

        Args:
            membership_gid: Membership GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.MembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_membership(membership_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_membership(self, membership_gid: str) -> AsanaResponse:
        """
        Get a membership by GID

        Args:
            membership_gid: Membership GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.MembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_membership(membership_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_memberships(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple memberships

        Args:
            opts: Options including parent, member, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.MembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_memberships(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_membership(self, body: Dict[str, Any], membership_gid: str) -> AsanaResponse:
        """
        Update a membership

        Args:
            body: Membership updates
            membership_gid: Membership GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.MembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_membership(body, membership_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # OrganizationExportsApi - 2 methods
    # ========================================================================

    async def create_organization_export(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create an organization export request

        Args:
            body: Export configuration
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.OrganizationExportsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_organization_export(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_organization_export(self, organization_export_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get details on an org export request

        Args:
            organization_export_gid: Organization export GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.OrganizationExportsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_organization_export(organization_export_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # PortfoliosApi - 12 methods
    # ========================================================================

    async def add_custom_field_setting_for_portfolio(self, body: Dict[str, Any], portfolio_gid: str) -> AsanaResponse:
        """
        Add a custom field to a portfolio

        Args:
            body: Custom field setting
            portfolio_gid: Portfolio GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_custom_field_setting_for_portfolio(body, portfolio_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def add_item_for_portfolio(self, body: Dict[str, Any], portfolio_gid: str) -> AsanaResponse:
        """
        Add an item to a portfolio

        Args:
            body: Item to add
            portfolio_gid: Portfolio GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_item_for_portfolio(body, portfolio_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def add_members_for_portfolio(self, body: Dict[str, Any], portfolio_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add members to a portfolio

        Args:
            body: Members to add
            portfolio_gid: Portfolio GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_members_for_portfolio(body, portfolio_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_portfolio(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a portfolio

        Args:
            body: Portfolio data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_portfolio(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_portfolio(self, portfolio_gid: str) -> AsanaResponse:
        """
        Delete a portfolio

        Args:
            portfolio_gid: Portfolio GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_portfolio(portfolio_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_items_for_portfolio(self, portfolio_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get items in a portfolio

        Args:
            portfolio_gid: Portfolio GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_items_for_portfolio(portfolio_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_portfolio(self, portfolio_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a portfolio by GID

        Args:
            portfolio_gid: Portfolio GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_portfolio(portfolio_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_portfolios(self, workspace: str, owner: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple portfolios

        Args:
            workspace: Workspace GID
            owner: Owner GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_portfolios(workspace, owner, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_custom_field_setting_for_portfolio(self, body: Dict[str, Any], portfolio_gid: str) -> AsanaResponse:
        """
        Remove a custom field from a portfolio

        Args:
            body: Custom field to remove
            portfolio_gid: Portfolio GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_custom_field_setting_for_portfolio(body, portfolio_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_item_for_portfolio(self, body: Dict[str, Any], portfolio_gid: str) -> AsanaResponse:
        """
        Remove an item from a portfolio

        Args:
            body: Item to remove
            portfolio_gid: Portfolio GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_item_for_portfolio(body, portfolio_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_members_for_portfolio(self, body: Dict[str, Any], portfolio_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Remove members from a portfolio

        Args:
            body: Members to remove
            portfolio_gid: Portfolio GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_members_for_portfolio(body, portfolio_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_portfolio(self, body: Dict[str, Any], portfolio_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a portfolio

        Args:
            body: Portfolio updates
            portfolio_gid: Portfolio GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.PortfoliosApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_portfolio(body, portfolio_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # ProjectBriefsApi - 4 methods
    # ========================================================================

    async def create_project_brief(self, body: Dict[str, Any], project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a project brief

        Args:
            body: Project brief data
            project_gid: Project GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectBriefsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_project_brief(body, project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_project_brief(self, project_brief_gid: str) -> AsanaResponse:
        """
        Delete a project brief

        Args:
            project_brief_gid: Project brief GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectBriefsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_project_brief(project_brief_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_project_brief(self, project_brief_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a project brief by GID

        Args:
            project_brief_gid: Project brief GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectBriefsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_project_brief(project_brief_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_project_brief(self, body: Dict[str, Any], project_brief_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a project brief

        Args:
            body: Project brief updates
            project_brief_gid: Project brief GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectBriefsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_project_brief(body, project_brief_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # ProjectTemplatesApi - 4 methods
    # ========================================================================

    async def get_project_template(self, project_template_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a project template by GID

        Args:
            project_template_gid: Project template GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectTemplatesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_project_template(project_template_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_project_templates(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple project templates

        Args:
            opts: Options including workspace, team, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectTemplatesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_project_templates(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_project_templates_for_team(self, team_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get project templates for a team

        Args:
            team_gid: Team GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectTemplatesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_project_templates_for_team(team_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def instantiate_project(self, project_template_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Instantiate a project from a template

        Args:
            project_template_gid: Project template GID
            opts: Options including body and opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectTemplatesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.instantiate_project(project_template_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # ProjectsApi - 16 methods
    # ========================================================================

    async def add_custom_field_setting_for_project(self, body: Dict[str, Any], project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add a custom field to a project

        Args:
            body: Custom field setting
            project_gid: Project GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_custom_field_setting_for_project(body, project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def add_followers_for_project(self, body: Dict[str, Any], project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add followers to a project

        Args:
            body: Followers to add
            project_gid: Project GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_followers_for_project(body, project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def add_members_for_project(self, body: Dict[str, Any], project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add members to a project

        Args:
            body: Members to add
            project_gid: Project GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_members_for_project(body, project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_project(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a new project

        Args:
            body: Project data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_project(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_project(self, project_gid: str) -> AsanaResponse:
        """
        Delete a project

        Args:
            project_gid: Project GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_project(project_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def duplicate_project(self, body: Dict[str, Any], project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Duplicate a project

        Args:
            body: Duplication configuration
            project_gid: Project GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.duplicate_project(body, project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_project(self, project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a project by GID

        Args:
            project_gid: Project GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_project(project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_projects(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple projects

        Args:
            opts: Options including workspace, team, archived, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_projects(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_projects_for_team(self, team_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get projects in a team

        Args:
            team_gid: Team GID
            opts: Options including archived, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_projects_for_team(team_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_projects_for_workspace(self, workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get projects in a workspace

        Args:
            workspace_gid: Workspace GID
            opts: Options including archived, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_projects_for_workspace(workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_task_counts_for_project(self, project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get task counts for a project

        Args:
            project_gid: Project GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_task_counts_for_project(project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def project_save_as_template(self, body: Dict[str, Any], project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a template from a project

        Args:
            body: Template configuration
            project_gid: Project GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.project_save_as_template(body, project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_custom_field_setting_for_project(self, body: Dict[str, Any], project_gid: str) -> AsanaResponse:
        """
        Remove a custom field from a project

        Args:
            body: Custom field to remove
            project_gid: Project GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_custom_field_setting_for_project(body, project_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_followers_for_project(self, body: Dict[str, Any], project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Remove followers from a project

        Args:
            body: Followers to remove
            project_gid: Project GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_followers_for_project(body, project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_members_for_project(self, body: Dict[str, Any], project_gid: str) -> AsanaResponse:
        """
        Remove members from a project

        Args:
            body: Members to remove
            project_gid: Project GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_members_for_project(body, project_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_project(self, body: Dict[str, Any], project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a project

        Args:
            body: Project updates
            project_gid: Project GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.ProjectsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_project(body, project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # SectionsApi - 7 methods
    # ========================================================================

    async def add_task_for_section(self, section_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add a task to a section

        Args:
            section_gid: Section GID
            opts: Options including body

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.SectionsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_task_for_section(section_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_section_for_project(self, project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a section in a project

        Args:
            project_gid: Project GID
            opts: Options including body and opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.SectionsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_section_for_project(project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_section(self, section_gid: str) -> AsanaResponse:
        """
        Delete a section

        Args:
            section_gid: Section GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.SectionsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_section(section_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_section(self, section_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a section by GID

        Args:
            section_gid: Section GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.SectionsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_section(section_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_sections_for_project(self, project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get sections in a project

        Args:
            project_gid: Project GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.SectionsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_sections_for_project(project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def insert_section_for_project(self, body: Dict[str, Any], project_gid: str) -> AsanaResponse:
        """
        Move or insert a section

        Args:
            body: Section and position
            project_gid: Project GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.SectionsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.insert_section_for_project(body, project_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_section(self, body: Dict[str, Any], section_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a section

        Args:
            body: Section updates
            section_gid: Section GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.SectionsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_section(body, section_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # StatusUpdatesApi - 4 methods
    # ========================================================================

    async def create_status_for_object(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a status update

        Args:
            body: Status update data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.StatusUpdatesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_status_for_object(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_status(self, status_update_gid: str) -> AsanaResponse:
        """
        Delete a status update

        Args:
            status_update_gid: Status update GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.StatusUpdatesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_status(status_update_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_status(self, status_update_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a status update by GID

        Args:
            status_update_gid: Status update GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.StatusUpdatesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_status(status_update_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_statuses_for_object(self, parent: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get status updates for an object

        Args:
            parent: Parent object GID
            opts: Options including created_since, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.StatusUpdatesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_statuses_for_object(parent, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # StoriesApi - 5 methods
    # ========================================================================

    async def create_story_for_task(self, body: Dict[str, Any], task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a story on a task

        Args:
            body: Story data
            task_gid: Task GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.StoriesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_story_for_task(body, task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_story(self, story_gid: str) -> AsanaResponse:
        """
        Delete a story

        Args:
            story_gid: Story GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.StoriesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_story(story_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_stories_for_task(self, task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get stories for a task

        Args:
            task_gid: Task GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.StoriesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_stories_for_task(task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_story(self, story_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a story by GID

        Args:
            story_gid: Story GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.StoriesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_story(story_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_story(self, body: Dict[str, Any], story_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a story

        Args:
            body: Story updates
            story_gid: Story GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.StoriesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_story(body, story_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # TagsApi - 8 methods
    # ========================================================================

    async def create_tag(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a tag

        Args:
            body: Tag data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TagsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_tag(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_tag_for_workspace(self, body: Dict[str, Any], workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a tag in a workspace

        Args:
            body: Tag data
            workspace_gid: Workspace GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TagsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_tag_for_workspace(body, workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_tag(self, tag_gid: str) -> AsanaResponse:
        """
        Delete a tag

        Args:
            tag_gid: Tag GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TagsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_tag(tag_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_tag(self, tag_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a tag by GID

        Args:
            tag_gid: Tag GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TagsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_tag(tag_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_tags(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple tags

        Args:
            opts: Options including workspace, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TagsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_tags(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_tags_for_task(self, task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get tags for a task

        Args:
            task_gid: Task GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TagsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_tags_for_task(task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_tags_for_workspace(self, workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get tags in a workspace

        Args:
            workspace_gid: Workspace GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TagsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_tags_for_workspace(workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_tag(self, body: Dict[str, Any], tag_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a tag

        Args:
            body: Tag updates
            tag_gid: Tag GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TagsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_tag(body, tag_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # TasksApi - 24 methods
    # ========================================================================

    async def add_dependencies_for_task(self, body: Dict[str, Any], task_gid: str) -> AsanaResponse:
        """
        Set dependencies for a task

        Args:
            body: Dependencies to add
            task_gid: Task GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_dependencies_for_task(body, task_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def add_dependents_for_task(self, body: Dict[str, Any], task_gid: str) -> AsanaResponse:
        """
        Set dependents for a task

        Args:
            body: Dependents to add
            task_gid: Task GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_dependents_for_task(body, task_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def add_followers_for_task(self, body: Dict[str, Any], task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add followers to a task

        Args:
            body: Followers to add
            task_gid: Task GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_followers_for_task(body, task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def add_project_for_task(self, body: Dict[str, Any], task_gid: str) -> AsanaResponse:
        """
        Add a task to a project

        Args:
            body: Project and insertion details
            task_gid: Task GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_project_for_task(body, task_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def add_tag_for_task(self, body: Dict[str, Any], task_gid: str) -> AsanaResponse:
        """
        Add a tag to a task

        Args:
            body: Tag to add
            task_gid: Task GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_tag_for_task(body, task_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_task(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a new task

        Args:
            body: Task data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_task(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_task(self, task_gid: str) -> AsanaResponse:
        """
        Delete a task

        Args:
            task_gid: Task GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_task(task_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def duplicate_task(self, body: Dict[str, Any], task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Duplicate a task

        Args:
            body: Duplicate configuration
            task_gid: Task GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.duplicate_task(body, task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_dependencies_for_task(self, task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get task dependencies

        Args:
            task_gid: Task GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_dependencies_for_task(task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_dependents_for_task(self, task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get task dependents

        Args:
            task_gid: Task GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_dependents_for_task(task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_subtasks_for_task(self, task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get subtasks of a task

        Args:
            task_gid: Task GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_subtasks_for_task(task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_task(self, task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a task by GID

        Args:
            task_gid: Task GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_task(task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_tasks(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple tasks with filters

        Args:
            opts: Options including assignee, project, workspace, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_tasks(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_tasks_for_project(self, project_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get tasks in a project

        Args:
            project_gid: Project GID
            opts: Options including completed_since, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_tasks_for_project(project_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_tasks_for_section(self, section_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get tasks in a section

        Args:
            section_gid: Section GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_tasks_for_section(section_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_tasks_for_tag(self, tag_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get tasks with a tag

        Args:
            tag_gid: Tag GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_tasks_for_tag(tag_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_tasks_for_user_task_list(self, user_task_list_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get tasks from a user task list

        Args:
            user_task_list_gid: User task list GID
            opts: Options including completed_since, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_tasks_for_user_task_list(user_task_list_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_dependencies_for_task(self, body: Dict[str, Any], task_gid: str) -> AsanaResponse:
        """
        Unlink dependencies from a task

        Args:
            body: Dependencies to remove
            task_gid: Task GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_dependencies_for_task(body, task_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_dependents_for_task(self, body: Dict[str, Any], task_gid: str) -> AsanaResponse:
        """
        Unlink dependents from a task

        Args:
            body: Dependents to remove
            task_gid: Task GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_dependents_for_task(body, task_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_follower_for_task(self, body: Dict[str, Any], task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Remove followers from a task

        Args:
            body: Followers to remove
            task_gid: Task GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_follower_for_task(body, task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_project_for_task(self, body: Dict[str, Any], task_gid: str) -> AsanaResponse:
        """
        Remove a task from a project

        Args:
            body: Project to remove
            task_gid: Task GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_project_for_task(body, task_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_tag_for_task(self, body: Dict[str, Any], task_gid: str) -> AsanaResponse:
        """
        Remove a tag from a task

        Args:
            body: Tag to remove
            task_gid: Task GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_tag_for_task(body, task_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def search_tasks_for_workspace(self, workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Search for tasks in a workspace

        Args:
            workspace_gid: Workspace GID
            opts: Search parameters and filters

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.search_tasks_for_workspace(workspace_gid, opts)
            )
            print('the response is', response)
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_task(self, body: Dict[str, Any], task_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a task

        Args:
            body: Task updates
            task_gid: Task GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TasksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_task(body, task_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # TeamMembershipsApi - 4 methods
    # ========================================================================

    async def get_team_membership(self, team_membership_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a team membership by GID

        Args:
            team_membership_gid: Team membership GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TeamMembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_team_membership(team_membership_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_team_memberships(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple team memberships

        Args:
            opts: Options including team, user, workspace, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TeamMembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_team_memberships(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_team_memberships_for_team(self, team_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get team memberships for a team

        Args:
            team_gid: Team GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TeamMembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_team_memberships_for_team(team_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_team_memberships_for_user(self, user_gid: str, workspace: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get team memberships for a user

        Args:
            user_gid: User GID
            workspace: Workspace GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TeamMembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_team_memberships_for_user(user_gid, workspace, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # TeamsApi - 6 methods
    # ========================================================================

    async def add_user_for_team(self, body: Dict[str, Any], team_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add a user to a team

        Args:
            body: User to add
            team_gid: Team GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TeamsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_user_for_team(body, team_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def create_team(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Create a new team

        Args:
            body: Team data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TeamsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_team(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_team(self, team_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a team by GID

        Args:
            team_gid: Team GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TeamsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_team(team_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_teams_for_user(self, user_gid: str, organization: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get teams for a user

        Args:
            user_gid: User GID
            organization: Organization GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TeamsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_teams_for_user(user_gid, organization, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_teams_for_workspace(self, workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get teams in a workspace

        Args:
            workspace_gid: Workspace GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TeamsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_teams_for_workspace(workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_user_for_team(self, body: Dict[str, Any], team_gid: str) -> AsanaResponse:
        """
        Remove a user from a team

        Args:
            body: User to remove
            team_gid: Team GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TeamsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_user_for_team(body, team_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # TimePeriodsApi - 2 methods
    # ========================================================================

    async def get_time_period(self, time_period_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a time period by GID

        Args:
            time_period_gid: Time period GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TimePeriodsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_time_period(time_period_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_time_periods(self, workspace: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get time periods

        Args:
            workspace: Workspace GID
            opts: Options including start_on, end_on, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TimePeriodsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_time_periods(workspace, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # TypeaheadApi - 1 methods
    # ========================================================================

    async def typeahead_for_workspace(self, workspace_gid: str, resource_type: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get objects via typeahead

        Args:
            workspace_gid: Workspace GID
            resource_type: Resource type to search for
            opts: Options including type, query, count, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.TypeaheadApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.typeahead_for_workspace(workspace_gid, resource_type, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # UserTaskListsApi - 2 methods
    # ========================================================================

    async def get_user_task_list(self, user_task_list_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a user task list by GID

        Args:
            user_task_list_gid: User task list GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.UserTaskListsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_user_task_list(user_task_list_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_user_task_list_for_user(self, user_gid: str, workspace: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a user task list for a user

        Args:
            user_gid: User GID
            workspace: Workspace GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.UserTaskListsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_user_task_list_for_user(user_gid, workspace, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # UsersApi - 5 methods
    # ========================================================================

    async def get_favorites_for_user(self, user_gid: str, resource_type: str, workspace: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get user favorites

        Args:
            user_gid: User GID
            resource_type: Resource type (project, portfolio, etc.)
            workspace: Workspace GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.UsersApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_favorites_for_user(user_gid, resource_type, workspace, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_user(self, user_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a user by GID

        Args:
            user_gid: User GID or "me"
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.UsersApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_user(user_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_users(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple users

        Args:
            opts: Options including workspace, team, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.UsersApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_users(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_users_for_team(self, team_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get users in a team

        Args:
            team_gid: Team GID
            opts: Options including offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.UsersApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_users_for_team(team_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_users_for_workspace(self, workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get users in a workspace

        Args:
            workspace_gid: Workspace GID
            opts: Options including offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.UsersApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_users_for_workspace(workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # WebhooksApi - 5 methods
    # ========================================================================

    async def create_webhook(self, body: Dict[str, Any], opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Establish a webhook

        Args:
            body: Webhook data
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WebhooksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.create_webhook(body, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def delete_webhook(self, webhook_gid: str) -> AsanaResponse:
        """
        Delete a webhook

        Args:
            webhook_gid: Webhook GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WebhooksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.delete_webhook(webhook_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_webhook(self, webhook_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a webhook by GID

        Args:
            webhook_gid: Webhook GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WebhooksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_webhook(webhook_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_webhooks(self, workspace: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple webhooks

        Args:
            workspace: Workspace GID
            opts: Options including limit, offset, resource, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WebhooksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_webhooks(workspace, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_webhook(self, body: Dict[str, Any], webhook_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a webhook

        Args:
            body: Webhook updates
            webhook_gid: Webhook GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WebhooksApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_webhook(body, webhook_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # WorkspaceMembershipsApi - 3 methods
    # ========================================================================

    async def get_workspace_membership(self, workspace_membership_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a workspace membership by GID

        Args:
            workspace_membership_gid: Workspace membership GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WorkspaceMembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_workspace_membership(workspace_membership_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_workspace_memberships_for_user(self, user_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get workspace memberships for a user

        Args:
            user_gid: User GID
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WorkspaceMembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_workspace_memberships_for_user(user_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_workspace_memberships_for_workspace(self, workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get workspace memberships for a workspace

        Args:
            workspace_gid: Workspace GID
            opts: Options including user, limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WorkspaceMembershipsApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_workspace_memberships_for_workspace(workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    # ========================================================================
    # WorkspacesApi - 5 methods
    # ========================================================================

    async def add_user_for_workspace(self, body: Dict[str, Any], workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Add a user to a workspace

        Args:
            body: User to add
            workspace_gid: Workspace GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WorkspacesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.add_user_for_workspace(body, workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_workspace(self, workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get a workspace by GID

        Args:
            workspace_gid: Workspace GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WorkspacesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_workspace(workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def get_workspaces(self, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Get multiple workspaces

        Args:
            opts: Options including limit, offset, opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WorkspacesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.get_workspaces(opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def remove_user_for_workspace(self, body: Dict[str, Any], workspace_gid: str) -> AsanaResponse:
        """
        Remove a user from a workspace

        Args:
            body: User to remove
            workspace_gid: Workspace GID

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WorkspacesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.remove_user_for_workspace(body, workspace_gid)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))

    async def update_workspace(self, body: Dict[str, Any], workspace_gid: str, opts: Optional[Dict[str, Any]] = None) -> AsanaResponse:
        """
        Update a workspace

        Args:
            body: Workspace updates
            workspace_gid: Workspace GID
            opts: Options including opt_fields

        Returns:
            AsanaResponse: Standardized response wrapper with success status and data
        """
        api_client = self._get_api_client()
        api_instance = asana.WorkspacesApi(api_client)

        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: api_instance.update_workspace(body, workspace_gid, opts)
            )
            return AsanaResponse(success=True, data=response)
        except ApiException as e:
            return AsanaResponse(success=False, error=str(e))
        except Exception as e:
            return AsanaResponse(success=False, error=str(e))
