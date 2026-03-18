"""
Marketo REST API DataSource - Auto-generated API wrapper

Generated from Marketo REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.marketo.marketo import MarketoClient, MarketoResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class MarketoDataSource:
    """Marketo REST API DataSource

    Provides async wrapper methods for Marketo REST API operations:
    - Lead management (describe, query, create/update)
    - Activity types and activities
    - Campaigns
    - Static lists and list membership
    - Programs
    - Custom objects
    - Folders
    - Tokens

    The base URL is determined by the MarketoClient's configured
    munchkin_id. All methods return MarketoResponse objects.

    Important: The client must call ensure_authenticated() before making
    API requests. This is handled automatically in the _execute helper.
    """

    def __init__(self, client: MarketoClient) -> None:
        """Initialize with MarketoClient.

        Args:
            client: MarketoClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError(
                "HTTP client does not have get_base_url method"
            ) from exc

    def get_data_source(self) -> "MarketoDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> MarketoClient:
        """Return the underlying MarketoClient."""
        return self._client

    # ------------------------------------------------------------------
    # Leads
    # ------------------------------------------------------------------

    async def describe_leads(self) -> MarketoResponse:
        """Describe the lead object schema

        HTTP GET /v1/lead/describe.json

        Returns:
            MarketoResponse with lead field metadata
        """
        url = self.base_url + "/v1/lead/describe.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed describe_leads"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute describe_leads",
            )

    async def get_leads(
        self,
        filter_type: str,
        filter_values: str,
        *,
        fields: str | None = None,
        batch_size: int | None = None,
        next_page_token: str | None = None,
    ) -> MarketoResponse:
        """Get leads by filter type and values

        HTTP GET /v1/leads.json

        Args:
            filter_type: The lead field to filter on (e.g. "email", "id")
            filter_values: Comma-separated list of filter values
            fields: Comma-separated list of field names to return
            batch_size: Maximum number of records to return (max 300)
            next_page_token: Paging token from a previous response

        Returns:
            MarketoResponse with matching leads
        """
        query_params: dict[str, Any] = {
            "filterType": filter_type,
            "filterValues": filter_values,
        }
        if fields is not None:
            query_params["fields"] = fields
        if batch_size is not None:
            query_params["batchSize"] = str(batch_size)
        if next_page_token is not None:
            query_params["nextPageToken"] = next_page_token

        url = self.base_url + "/v1/leads.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_leads"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_leads",
            )

    async def get_lead_by_id(
        self,
        lead_id: int | str,
        *,
        fields: str | None = None,
    ) -> MarketoResponse:
        """Get a single lead by ID

        HTTP GET /v1/lead/{id}.json

        Args:
            lead_id: The lead ID
            fields: Comma-separated list of field names to return

        Returns:
            MarketoResponse with lead data
        """
        query_params: dict[str, Any] = {}
        if fields is not None:
            query_params["fields"] = fields

        url = self.base_url + "/v1/lead/{lead_id}.json".format(
            lead_id=lead_id
        )

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_lead_by_id"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_lead_by_id",
            )

    async def create_or_update_leads(
        self,
        input_data: list[dict[str, Any]],
        *,
        action: str | None = None,
        lookup_field: str | None = None,
        partition_name: str | None = None,
        async_processing: bool | None = None,
    ) -> MarketoResponse:
        """Create or update leads

        HTTP POST /v1/leads.json

        Args:
            input_data: List of lead records to create or update
            action: Action to perform: createOnly, updateOnly,
                    createOrUpdate (default), createDuplicate
            lookup_field: Field to use for deduplication
            partition_name: Lead partition name
            async_processing: Process asynchronously

        Returns:
            MarketoResponse with operation result
        """
        url = self.base_url + "/v1/leads.json"

        body: dict[str, Any] = {"input": input_data}
        if action is not None:
            body["action"] = action
        if lookup_field is not None:
            body["lookupField"] = lookup_field
        if partition_name is not None:
            body["partitionName"] = partition_name
        if async_processing is not None:
            body["asyncProcessing"] = async_processing

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_or_update_leads"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_or_update_leads",
            )

    # ------------------------------------------------------------------
    # Activities
    # ------------------------------------------------------------------

    async def get_activity_types(self) -> MarketoResponse:
        """Get all activity types

        HTTP GET /v1/activities/types.json

        Returns:
            MarketoResponse with activity type definitions
        """
        url = self.base_url + "/v1/activities/types.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_activity_types"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_activity_types",
            )

    async def get_activities(
        self,
        activity_type_ids: str,
        next_page_token: str,
        *,
        since_datetime: str | None = None,
        batch_size: int | None = None,
        list_id: int | None = None,
        lead_ids: str | None = None,
    ) -> MarketoResponse:
        """Get lead activities

        HTTP GET /v1/activities.json

        Args:
            activity_type_ids: Comma-separated activity type IDs
            next_page_token: Paging token (use get_paging_token to obtain)
            since_datetime: Earliest datetime for activities (ISO 8601)
            batch_size: Maximum number of records to return (max 300)
            list_id: Filter by static list ID
            lead_ids: Comma-separated lead IDs to filter

        Returns:
            MarketoResponse with activities
        """
        query_params: dict[str, Any] = {
            "activityTypeIds": activity_type_ids,
            "nextPageToken": next_page_token,
        }
        if since_datetime is not None:
            query_params["sinceDatetime"] = since_datetime
        if batch_size is not None:
            query_params["batchSize"] = str(batch_size)
        if list_id is not None:
            query_params["listId"] = str(list_id)
        if lead_ids is not None:
            query_params["leadIds"] = lead_ids

        url = self.base_url + "/v1/activities.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_activities"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_activities",
            )

    async def get_paging_token(
        self,
        since_datetime: str,
    ) -> MarketoResponse:
        """Get a paging token for use with activity APIs

        HTTP GET /v1/activities/pagingtoken.json

        Args:
            since_datetime: Earliest datetime (ISO 8601)

        Returns:
            MarketoResponse with nextPageToken
        """
        query_params: dict[str, Any] = {
            "sinceDatetime": since_datetime,
        }

        url = self.base_url + "/v1/activities/pagingtoken.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_paging_token"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_paging_token",
            )

    # ------------------------------------------------------------------
    # Campaigns
    # ------------------------------------------------------------------

    async def get_campaigns(
        self,
        *,
        batch_size: int | None = None,
        next_page_token: str | None = None,
        is_triggerable: bool | None = None,
    ) -> MarketoResponse:
        """Get all campaigns

        HTTP GET /v1/campaigns.json

        Args:
            batch_size: Maximum number of records to return
            next_page_token: Paging token from a previous response
            is_triggerable: Filter to only triggerable campaigns

        Returns:
            MarketoResponse with campaigns
        """
        query_params: dict[str, Any] = {}
        if batch_size is not None:
            query_params["batchSize"] = str(batch_size)
        if next_page_token is not None:
            query_params["nextPageToken"] = next_page_token
        if is_triggerable is not None:
            query_params["isTriggerable"] = str(is_triggerable).lower()

        url = self.base_url + "/v1/campaigns.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_campaigns"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_campaigns",
            )

    async def get_campaign_by_id(
        self,
        campaign_id: int | str,
    ) -> MarketoResponse:
        """Get a campaign by ID

        HTTP GET /v1/campaigns/{id}.json

        Args:
            campaign_id: The campaign ID

        Returns:
            MarketoResponse with campaign data
        """
        url = self.base_url + "/v1/campaigns/{campaign_id}.json".format(
            campaign_id=campaign_id
        )

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_campaign_by_id"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_campaign_by_id",
            )

    # ------------------------------------------------------------------
    # Static Lists
    # ------------------------------------------------------------------

    async def get_lists(
        self,
        *,
        batch_size: int | None = None,
        next_page_token: str | None = None,
        name: str | None = None,
        program_name: str | None = None,
        workspace_name: str | None = None,
    ) -> MarketoResponse:
        """Get all static lists

        HTTP GET /v1/lists.json

        Args:
            batch_size: Maximum number of records to return
            next_page_token: Paging token from a previous response
            name: Filter by list name
            program_name: Filter by program name
            workspace_name: Filter by workspace name

        Returns:
            MarketoResponse with static lists
        """
        query_params: dict[str, Any] = {}
        if batch_size is not None:
            query_params["batchSize"] = str(batch_size)
        if next_page_token is not None:
            query_params["nextPageToken"] = next_page_token
        if name is not None:
            query_params["name"] = name
        if program_name is not None:
            query_params["programName"] = program_name
        if workspace_name is not None:
            query_params["workspaceName"] = workspace_name

        url = self.base_url + "/v1/lists.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_lists"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_lists",
            )

    async def get_list_by_id(
        self,
        list_id: int | str,
    ) -> MarketoResponse:
        """Get a static list by ID

        HTTP GET /v1/lists/{id}.json

        Args:
            list_id: The list ID

        Returns:
            MarketoResponse with list data
        """
        url = self.base_url + "/v1/lists/{list_id}.json".format(
            list_id=list_id
        )

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_list_by_id"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_list_by_id",
            )

    async def get_leads_in_list(
        self,
        list_id: int | str,
        *,
        fields: str | None = None,
        batch_size: int | None = None,
        next_page_token: str | None = None,
    ) -> MarketoResponse:
        """Get leads that are members of a static list

        HTTP GET /v1/lists/{id}/leads.json

        Args:
            list_id: The list ID
            fields: Comma-separated list of field names to return
            batch_size: Maximum number of records to return
            next_page_token: Paging token from a previous response

        Returns:
            MarketoResponse with leads in the list
        """
        query_params: dict[str, Any] = {}
        if fields is not None:
            query_params["fields"] = fields
        if batch_size is not None:
            query_params["batchSize"] = str(batch_size)
        if next_page_token is not None:
            query_params["nextPageToken"] = next_page_token

        url = self.base_url + "/v1/lists/{list_id}/leads.json".format(
            list_id=list_id
        )

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_leads_in_list"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_leads_in_list",
            )

    # ------------------------------------------------------------------
    # Programs
    # ------------------------------------------------------------------

    async def get_programs(
        self,
        *,
        offset: int | None = None,
        max_return: int | None = None,
        filter_type: str | None = None,
        filter_values: str | None = None,
        earliest_updated_at: str | None = None,
        latest_updated_at: str | None = None,
    ) -> MarketoResponse:
        """Get all programs

        HTTP GET /v1/programs.json

        Args:
            offset: Integer offset for paging
            max_return: Maximum number of programs to return (max 200)
            filter_type: Filter type (e.g. "id", "name")
            filter_values: Comma-separated filter values
            earliest_updated_at: Earliest updatedAt datetime (ISO 8601)
            latest_updated_at: Latest updatedAt datetime (ISO 8601)

        Returns:
            MarketoResponse with programs
        """
        query_params: dict[str, Any] = {}
        if offset is not None:
            query_params["offset"] = str(offset)
        if max_return is not None:
            query_params["maxReturn"] = str(max_return)
        if filter_type is not None:
            query_params["filterType"] = filter_type
        if filter_values is not None:
            query_params["filterValues"] = filter_values
        if earliest_updated_at is not None:
            query_params["earliestUpdatedAt"] = earliest_updated_at
        if latest_updated_at is not None:
            query_params["latestUpdatedAt"] = latest_updated_at

        url = self.base_url + "/v1/programs.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_programs"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_programs",
            )

    async def get_program_by_id(
        self,
        program_id: int | str,
    ) -> MarketoResponse:
        """Get a program by ID

        HTTP GET /v1/programs/{id}.json

        Args:
            program_id: The program ID

        Returns:
            MarketoResponse with program data
        """
        url = self.base_url + "/v1/programs/{program_id}.json".format(
            program_id=program_id
        )

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_program_by_id"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_program_by_id",
            )

    # ------------------------------------------------------------------
    # Custom Objects
    # ------------------------------------------------------------------

    async def get_custom_objects(
        self,
        *,
        names: str | None = None,
    ) -> MarketoResponse:
        """List custom object types

        HTTP GET /v1/customobjects.json

        Args:
            names: Comma-separated list of custom object API names

        Returns:
            MarketoResponse with custom object type definitions
        """
        query_params: dict[str, Any] = {}
        if names is not None:
            query_params["names"] = names

        url = self.base_url + "/v1/customobjects.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_custom_objects"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_custom_objects",
            )

    # ------------------------------------------------------------------
    # Folders
    # ------------------------------------------------------------------

    async def get_folders(
        self,
        *,
        root: int | None = None,
        max_depth: int | None = None,
        max_return: int | None = None,
        offset: int | None = None,
        workspace: str | None = None,
    ) -> MarketoResponse:
        """Get folders

        HTTP GET /v1/folders.json

        Args:
            root: Parent folder ID
            max_depth: Maximum folder depth to traverse
            max_return: Maximum number of folders to return
            offset: Integer offset for paging
            workspace: Workspace name filter

        Returns:
            MarketoResponse with folders
        """
        query_params: dict[str, Any] = {}
        if root is not None:
            query_params["root"] = str(root)
        if max_depth is not None:
            query_params["maxDepth"] = str(max_depth)
        if max_return is not None:
            query_params["maxReturn"] = str(max_return)
        if offset is not None:
            query_params["offset"] = str(offset)
        if workspace is not None:
            query_params["workspace"] = workspace

        url = self.base_url + "/v1/folders.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folders"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_folders",
            )

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------

    async def get_tokens(
        self,
        folder_id: int | str,
        folder_type: str,
    ) -> MarketoResponse:
        """Get tokens for a folder or program

        HTTP GET /v1/tokens.json

        Args:
            folder_id: The folder or program ID
            folder_type: The folder type (e.g. "Folder", "Program")

        Returns:
            MarketoResponse with tokens
        """
        query_params: dict[str, Any] = {
            "folderId": str(folder_id),
            "folderType": folder_type,
        }

        url = self.base_url + "/v1/tokens.json"

        try:
            await self.http.ensure_authenticated()
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MarketoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_tokens"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return MarketoResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_tokens",
            )
