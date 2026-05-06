

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional

from kiota_abstractions.base_request_configuration import (  # type: ignore
    RequestConfiguration,
)
from msgraph.generated.sites.item.columns.columns_request_builder import (  # type: ignore
    ColumnsRequestBuilder,
)
from msgraph.generated.sites.item.drives.drives_request_builder import (  # type: ignore
    DrivesRequestBuilder,
)
from msgraph.generated.sites.item.lists.lists_request_builder import (  # type: ignore
    ListsRequestBuilder,
)
from msgraph.generated.sites.item.pages.pages_request_builder import (  # type: ignore
    PagesRequestBuilder,
)

# Import MS Graph specific query parameter classes for SharePoint
from msgraph.generated.sites.sites_request_builder import (  # type: ignore
    SitesRequestBuilder,
)

from app.sources.client.microsoft.microsoft import MSGraphClient


# SharePoint-specific response wrapper
class SharePointResponse:
    """Standardized SharePoint API response wrapper."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def __init__(self, success: bool, data: Optional[Dict[str, Any]] = None, error: Optional[str] = None, message: Optional[str] = None) -> None:
        self.success = success
        self.data = data
        self.error = error
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

# Set up logger
logger = logging.getLogger(__name__)

class SharePointDataSource:
    """
    Comprehensive Microsoft SharePoint API client with complete Sites, Lists, and Libraries coverage.

    Features:
    - Complete SharePoint API coverage with 251 methods organized by operation type
    - Support for Sites, Site Collections, and Subsites
    - Complete List operations: lists, items, content types, columns
    - Complete Document Library operations: drives, folders, files
    - Modern Page operations: pages, canvas layout, web parts
    - Site-specific OneNote operations: notebooks, sections, pages
    - Site Analytics and Activity tracking
    - Site Permissions and Information Protection
    - Term Store and Metadata management
    - Site Search and Discovery capabilities
    - Microsoft Graph SDK integration with SharePoint-specific optimizations
    - Async snake_case method names for all operations
    - Standardized SharePointResponse format for all responses
    - Comprehensive error handling and SharePoint-specific response processing

    EXCLUDED OPERATIONS (modify EXCLUDED_KEYWORDS list to change):
    - Personal OneDrive operations (/me/drive, /users/{user-id}/drive)
    - Outlook operations (messages, events, contacts, calendar, mail folders)
    - Teams operations (chats, teams, channels)
    - Personal OneNote operations (/me/onenote, /users/{user-id}/onenote)
    - Planner operations (plans, tasks, buckets)
    - Directory operations (users, groups, directory objects)
    - Device management operations (devices, device management)
    - Admin operations (admin, compliance, security)
    - Generic drives operations (drives without site context)
    - User activity analytics (keep site analytics)
    - Communications operations (communications, education, identity)

    Operation Types:
    - Sites operations: Site collections, subsites, site information
    - Lists operations: Lists, list items, fields, content types
    - Drives operations: Document libraries, folders, files
    - Pages operations: Modern pages, canvas layout, web parts
    - Content Types operations: Site and list content types
    - Columns operations: Site and list columns, column definitions
    - OneNote operations: Site-specific notebooks, sections, pages
    - Permissions operations: Site and item permissions
    - Analytics operations: Site analytics and activity stats
    - Term Store operations: Managed metadata, term sets, terms
    - Operations operations: Long-running operations, subscriptions
    - Recycle Bin operations: Deleted items and restoration
    - Information Protection operations: Labels and policies
    - General operations: Base SharePoint functionality
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize with Microsoft Graph SDK client optimized for SharePoint."""
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "sites"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("SharePoint client initialized with 251 methods")

    def _handle_sharepoint_response(self, response: object) -> SharePointResponse:
        """Handle SharePoint API response with comprehensive error handling."""
        try:
            if response is None:
                return SharePointResponse(success=False, error="Empty response from SharePoint API")

            success = True
            error_msg = None

            # Enhanced error response handling for SharePoint operations
            if hasattr(response, 'error'):
                success = False
                error_msg = str(response.error)
            elif isinstance(response, dict) and 'error' in response:
                success = False
                error_info = response['error']
                if isinstance(error_info, dict):
                    error_code = error_info.get('code', 'Unknown')
                    error_message = error_info.get('message', 'No message')
                    error_msg = f"{error_code}: {error_message}"
                else:
                    error_msg = str(error_info)
            elif hasattr(response, 'code') and hasattr(response, 'message'):
                success = False
                error_msg = f"{response.code}: {response.message}"

            return SharePointResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling SharePoint response: {e}")
            return SharePointResponse(success=False, error=str(e))

    def get_data_source(self) -> 'SharePointDataSource':
        """Get the underlying SharePoint client."""
        return self

    # ========== SITES OPERATIONS (17 methods) ==========

    async def sites_add(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action add.
        SharePoint operation: POST /sites/add
        Operation type: sites
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.add.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delta(
        self,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function delta.
        SharePoint operation: GET /sites/delta()
        Operation type: sites
        Args:
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.delta().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_all_sites(
        self,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getAllSites.
        SharePoint operation: GET /sites/getAllSites()
        Operation type: sites
        Args:
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.get_all_sites.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_remove(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action remove.
        SharePoint operation: POST /sites/remove
        Operation type: sites
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.remove.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_update_site(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update entity in sites.
        SharePoint operation: PATCH /sites/{site-id}
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_created_by_user_update_mailbox_settings(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/createdByUser/mailboxSettings
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).created_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_created_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/createdByUser/serviceProvisioningErrors
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).created_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_activities_by_interval_4c35(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/getActivitiesByInterval()
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_activities_by_interval().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_activities_by_interval_ad27(
        self,
        site_id: str,
        startDateTime: str,
        endDateTime: str,
        interval: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/getActivitiesByInterval(startDateTime='{startDateTime}',endDateTime='{endDateTime}',interval='{interval}')
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            startDateTime (str, required): SharePoint path parameter: startDateTime
            endDateTime (str, required): SharePoint path parameter: endDateTime
            interval (str, required): SharePoint path parameter: interval
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_activities_by_interval(start_date_time='{start_date_time}',end_date_time='{end_date_time}',interval='{interval}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_by_path(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getByPath.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_by_path_get_activities_by_interval_4c35(
        self,
        site_id: str,
        path: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/getActivitiesByInterval()
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.get_activities_by_interval().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_by_path_get_activities_by_interval_ad27(
        self,
        site_id: str,
        path: str,
        startDateTime: str,
        endDateTime: str,
        interval: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/getActivitiesByInterval(startDateTime='{startDateTime}',endDateTime='{endDateTime}',interval='{interval}')
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            startDateTime (str, required): SharePoint path parameter: startDateTime
            endDateTime (str, required): SharePoint path parameter: endDateTime
            interval (str, required): SharePoint path parameter: interval
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.get_activities_by_interval(start_date_time='{start_date_time}',end_date_time='{end_date_time}',interval='{interval}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_sites(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sites from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/sites
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.sites.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_last_modified_by_user_update_mailbox_settings(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/lastModifiedByUser/mailboxSettings
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).last_modified_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_last_modified_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/lastModifiedByUser/serviceProvisioningErrors
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).last_modified_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_sites(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List subsites for a site.
        SharePoint operation: GET /sites/{site-id}/sites
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).sites.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_sites(
        self,
        site_id: str,
        site_id1: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sites from sites.
        SharePoint operation: GET /sites/{site-id}/sites/{site-id1}
        Operation type: sites
        Args:
            site_id (str, required): SharePoint site id identifier
            site_id1 (str, required): SharePoint site id1 identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).sites.by_site_id(site_id1).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== LISTS OPERATIONS (101 methods) ==========

    async def sites_site_get_applicable_content_types_for_list(
        self,
        site_id: str,
        listId: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getApplicableContentTypesForList.
        SharePoint operation: GET /sites/{site-id}/getApplicableContentTypesForList(listId='{listId}')
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            listId (str, required): SharePoint listId identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_applicable_content_types_for_list(list_id='{list_id}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_get_by_path_get_applicable_content_types_for_list(
        self,
        site_id: str,
        path: str,
        listId: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getApplicableContentTypesForList.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/getApplicableContentTypesForList(listId='{listId}')
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            listId (str, required): SharePoint listId identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.get_applicable_content_types_for_list(list_id='{list_id}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_items(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get items from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/items
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.items.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_create_lists(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to lists for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/lists
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.lists.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_lists(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get lists from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/lists
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.lists.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_items(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get items from sites.
        SharePoint operation: GET /sites/{site-id}/items
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).items.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_items(
        self,
        site_id: str,
        baseItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get items from sites.
        SharePoint operation: GET /sites/{site-id}/items/{baseItem-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            baseItem_id (str, required): SharePoint baseItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).items.by_list_item_id(baseItem_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_create_lists(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a new list.
        SharePoint operation: POST /sites/{site-id}/lists
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_lists(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get lists in a site.
        SharePoint operation: GET /sites/{site-id}/lists
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_lists(
        self,
        site_id: str,
        list_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property lists for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_lists(
        self,
        site_id: str,
        list_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List operations on a list.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_lists(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property lists in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_create_columns(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a columnDefinition in a list.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/columns
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_list_columns(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List columnDefinitions in a list.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/columns
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_delete_columns(
        self,
        site_id: str,
        list_id: str,
        columnDefinition_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columns for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.by_column_definition_id(columnDefinition_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_columns(
        self,
        site_id: str,
        list_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.by_column_definition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_update_columns(
        self,
        site_id: str,
        list_id: str,
        columnDefinition_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columns in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.by_column_definition_id(columnDefinition_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_columns_get_source_column(
        self,
        site_id: str,
        list_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sourceColumn from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/columns/{columnDefinition-id}/sourceColumn
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).columns.by_column_definition_id(columnDefinition_id).source_column.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_create_content_types(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to contentTypes for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_list_content_types(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List contentTypes in a list.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_add_copy(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action addCopy.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/addCopy
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.add_copy.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_add_copy_from_content_type_hub(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action addCopyFromContentTypeHub.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/addCopyFromContentTypeHub
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.add_copy_from_content_type_hub.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_get_compatible_hub_content_types(
        self,
        site_id: str,
        list_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getCompatibleHubContentTypes.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/getCompatibleHubContentTypes()
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.get_compatible_hub_content_types().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_delete_content_types(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property contentTypes for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_content_types(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get contentTypes from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_update_content_types(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property contentTypes in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_content_type_associate_with_hub_sites(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action associateWithHubSites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/associateWithHubSites
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).associate_with_hub_sites.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_get_base(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get base from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/base
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).base.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_list_base_types(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get baseTypes from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/baseTypes
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).base_types.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_get_base_types(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        contentType_id1: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get baseTypes from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/baseTypes/{contentType-id1}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            contentType_id1 (str, required): SharePoint contentType id1 identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).base_types.by_baseType_id(contentType_id1).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_create_column_links(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to columnLinks for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnLinks
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_links.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_list_column_links(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnLinks from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnLinks
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_links.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_delete_column_links(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnLink_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columnLinks for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_get_column_links(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnLink_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnLinks from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_update_column_links(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnLink_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columnLinks in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_list_column_positions(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnPositions from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnPositions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_positions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_get_column_positions(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnPositions from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columnPositions/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).column_positions.by_columnPosition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_create_columns(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to columns for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_list_columns(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_delete_columns(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columns for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_get_columns(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_update_columns(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columns in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_content_types_columns_get_source_column(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sourceColumn from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}/sourceColumn
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).source_column.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_content_type_copy_to_default_content_location(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action copyToDefaultContentLocation.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/copyToDefaultContentLocation
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).copy_to_default_content_location.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_content_type_is_published(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function isPublished.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/isPublished()
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).is_published().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_content_type_publish(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action publish.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/publish
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).publish.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_content_types_content_type_unpublish(
        self,
        site_id: str,
        list_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action unpublish.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/contentTypes/{contentType-id}/unpublish
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).content_types.by_content_type_id(contentType_id).unpublish.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_created_by_user_update_mailbox_settings(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/createdByUser/mailboxSettings
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).created_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_created_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/createdByUser/serviceProvisioningErrors
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).created_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_drive(
        self,
        site_id: str,
        list_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get drive from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/drive
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).drive.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_create_items(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a new item in a list.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_list_items(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List items.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_delta_fa14(
        self,
        site_id: str,
        list_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function delta.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/delta()
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.delta().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_delta_9846(
        self,
        site_id: str,
        list_id: str,
        token: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function delta.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/delta(token='{token}')
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            token (str, required): SharePoint path parameter: token
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.delta(token='{token}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_delete_items(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete an item from a list.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_items(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get listItem.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_update_items(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property items in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_analytics(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get analytics from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/analytics
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).analytics.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_list_item_create_link(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action createLink.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items/{listItem-id}/createLink
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).create_link.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_created_by_user_update_mailbox_settings(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/createdByUser/mailboxSettings
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).created_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_created_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/createdByUser/serviceProvisioningErrors
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).created_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_create_document_set_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create documentSetVersion.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_list_document_set_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List documentSetVersions.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_delete_document_set_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete documentSetVersion.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_document_set_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get documentSetVersion.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_update_document_set_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property documentSetVersions in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_document_set_versions_delete_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property fields for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).fields.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_document_set_versions_get_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get fields from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).fields.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_document_set_versions_update_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property fields in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).fields.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_list_item_document_set_versions_document_set_version_restore(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        documentSetVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action restore.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items/{listItem-id}/documentSetVersions/{documentSetVersion-id}/restore
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            documentSetVersion_id (str, required): SharePoint documentSetVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).document_set_versions.by_documentSetVersion_id(documentSetVersion_id).restore.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_drive_item(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get driveItem from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/driveItem
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).drive_item.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_drive_item_content(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_format: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get content for the navigation property driveItem from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/driveItem/content
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_format (str, optional): Format of the content
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).drive_item.content.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_update_drive_item_content(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update content for the navigation property driveItem in sites.
        SharePoint operation: PUT /sites/{site-id}/lists/{list-id}/items/{listItem-id}/driveItem/content
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).drive_item.content.put(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_delete_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property fields for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).fields.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get fields from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).fields.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_update_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update listItem.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).fields.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_list_item_get_activities_by_interval_4c35(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/getActivitiesByInterval()
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).get_activities_by_interval().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_list_item_get_activities_by_interval_ad27(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        startDateTime: str,
        endDateTime: str,
        interval: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getActivitiesByInterval.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/getActivitiesByInterval(startDateTime='{startDateTime}',endDateTime='{endDateTime}',interval='{interval}')
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            startDateTime (str, required): SharePoint path parameter: startDateTime
            endDateTime (str, required): SharePoint path parameter: endDateTime
            interval (str, required): SharePoint path parameter: interval
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).get_activities_by_interval(start_date_time='{start_date_time}',end_date_time='{end_date_time}',interval='{interval}').get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_last_modified_by_user_update_mailbox_settings(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/lastModifiedByUser/mailboxSettings
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).last_modified_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_last_modified_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/lastModifiedByUser/serviceProvisioningErrors
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).last_modified_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_create_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to versions for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_delete_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property versions for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_get_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get a ListItemVersion resource.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_update_versions(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property versions in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_versions_delete_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property fields for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).fields.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_versions_get_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get fields from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).fields.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_items_versions_update_fields(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property fields in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}/fields
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).fields.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_items_list_item_versions_list_item_version_restore_version(
        self,
        site_id: str,
        list_id: str,
        listItem_id: str,
        listItemVersion_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action restoreVersion.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/items/{listItem-id}/versions/{listItemVersion-id}/restoreVersion
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            listItem_id (str, required): SharePoint listItem id identifier
            listItemVersion_id (str, required): SharePoint listItemVersion id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).items.by_list_item_id(listItem_id).versions.by_list_item_version_id(listItemVersion_id).restore_version.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_last_modified_by_user_update_mailbox_settings(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/lastModifiedByUser/mailboxSettings
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).last_modified_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_last_modified_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/lastModifiedByUser/serviceProvisioningErrors
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).last_modified_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_create_operations(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to operations for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/operations
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).operations.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_list_operations(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get operations from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/operations
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).operations.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_delete_operations(
        self,
        site_id: str,
        list_id: str,
        richLongRunningOperation_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property operations for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/operations/{richLongRunningOperation-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_operations(
        self,
        site_id: str,
        list_id: str,
        richLongRunningOperation_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get operations from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/operations/{richLongRunningOperation-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_update_operations(
        self,
        site_id: str,
        list_id: str,
        richLongRunningOperation_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property operations in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/operations/{richLongRunningOperation-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_create_subscriptions(
        self,
        site_id: str,
        list_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to subscriptions for sites.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/subscriptions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_list_subscriptions(
        self,
        site_id: str,
        list_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get subscriptions from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/subscriptions
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_delete_subscriptions(
        self,
        site_id: str,
        list_id: str,
        subscription_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property subscriptions for sites.
        SharePoint operation: DELETE /sites/{site-id}/lists/{list-id}/subscriptions/{subscription-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            subscription_id (str, required): SharePoint subscription id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.by_subscription_id(subscription_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_get_subscriptions(
        self,
        site_id: str,
        list_id: str,
        subscription_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get subscriptions from sites.
        SharePoint operation: GET /sites/{site-id}/lists/{list-id}/subscriptions/{subscription-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            subscription_id (str, required): SharePoint subscription id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ListsRequestBuilder.ListsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ListsRequestBuilder.ListsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.by_subscription_id(subscription_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_lists_update_subscriptions(
        self,
        site_id: str,
        list_id: str,
        subscription_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property subscriptions in sites.
        SharePoint operation: PATCH /sites/{site-id}/lists/{list-id}/subscriptions/{subscription-id}
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            subscription_id (str, required): SharePoint subscription id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.by_subscription_id(subscription_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_lists_list_subscriptions_subscription_reauthorize(
        self,
        site_id: str,
        list_id: str,
        subscription_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action reauthorize.
        SharePoint operation: POST /sites/{site-id}/lists/{list-id}/subscriptions/{subscription-id}/reauthorize
        Operation type: lists
        Args:
            site_id (str, required): SharePoint site id identifier
            list_id (str, required): SharePoint list id identifier
            subscription_id (str, required): SharePoint subscription id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).lists.by_list_id(list_id).subscriptions.by_subscription_id(subscription_id).reauthorize.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== DRIVES OPERATIONS (7 methods) ==========

    async def sites_analytics_item_activity_stats_activities_get_drive_item(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get driveItem from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}/driveItem
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).drive_item.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_activities_get_drive_item_content(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        dollar_format: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get content for the navigation property driveItem from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}/driveItem/content
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            dollar_format (str, optional): Format of the content
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).drive_item.content.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_activities_update_drive_item_content(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update content for the navigation property driveItem in sites.
        SharePoint operation: PUT /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}/driveItem/content
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).drive_item.content.put(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_drive(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get drive from sites.
        SharePoint operation: GET /sites/{site-id}/drive
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).drive.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_drives(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get drives from sites.
        SharePoint operation: GET /sites/{site-id}/drives
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = DrivesRequestBuilder.DrivesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = DrivesRequestBuilder.DrivesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).drives.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_get_drive(
        self,
        site_id: str,
        path: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get drive from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/drive
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.drive.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_drives(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get drives from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/drives
        Operation type: drives
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.drives.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== PAGES OPERATIONS (51 methods) ==========

    async def sites_get_by_path_create_pages(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to pages for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/pages
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.pages.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_pages(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get pages from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/pages
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.pages.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_create_pages(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a page in the site pages list of a site.
        SharePoint operation: POST /sites/{site-id}/pages
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_pages(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List baseSitePages.
        SharePoint operation: GET /sites/{site-id}/pages
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_pages_as_site_page(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get SitePage.
        SharePoint operation: GET /sites/{site-id}/pages/graph.sitePage
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.graph_site_page.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_pages(
        self,
        site_id: str,
        baseSitePage_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete baseSitePage.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_pages(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get baseSitePage.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_pages(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property pages in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_created_by_user_update_mailbox_settings(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/createdByUser/mailboxSettings
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).created_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_created_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/createdByUser/serviceProvisioningErrors
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).created_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_pages_as_site_page(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get SitePage.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_delete_canvas_layout(
        self,
        site_id: str,
        baseSitePage_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property canvasLayout for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_get_canvas_layout(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get canvasLayout from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_update_canvas_layout(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property canvasLayout in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_create_horizontal_sections(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to horizontalSections for sites.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_list_horizontal_sections(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get horizontalSections from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_delete_horizontal_sections(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property horizontalSections for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_get_horizontal_sections(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get horizontalSections from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_update_horizontal_sections(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property horizontalSections in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_create_columns(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to columns for sites.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_list_columns(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_delete_columns(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columns for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_get_columns(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_update_columns(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columns in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_columns_create_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to webparts for sites.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_columns_list_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webparts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_columns_delete_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        webPart_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property webparts for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.by_web_part_id(webPart_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_columns_get_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        webPart_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webparts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.by_web_part_id(webPart_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_horizontal_sections_columns_update_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property webparts in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.by_web_part_id(webPart_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_pages_base_site_page_microsoft_graph_site_page_canvas_layout_horizontal_sections_horizontal_section_columns_horizontal_section_column_webparts_web_part_get_position_of_web_part(
        self,
        site_id: str,
        baseSitePage_id: str,
        horizontalSection_id: str,
        horizontalSectionColumn_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action getPositionOfWebPart.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/horizontalSections/{horizontalSection-id}/columns/{horizontalSectionColumn-id}/webparts/{webPart-id}/getPositionOfWebPart
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            horizontalSection_id (str, required): SharePoint horizontalSection id identifier
            horizontalSectionColumn_id (str, required): SharePoint horizontalSectionColumn id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.horizontal_sections.by_horizontal_section_id(horizontalSection_id).columns.by_column_definition_id(horizontalSectionColumn_id).webparts.by_web_part_id(webPart_id).get_position_of_web_part.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_delete_vertical_section(
        self,
        site_id: str,
        baseSitePage_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property verticalSection for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_get_vertical_section(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get verticalSection from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_update_vertical_section(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property verticalSection in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_vertical_section_create_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to webparts for sites.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_vertical_section_list_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webparts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_vertical_section_delete_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property webparts for sites.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.by_web_part_id(webPart_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_vertical_section_get_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webparts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.by_web_part_id(webPart_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_canvas_layout_vertical_section_update_webparts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property webparts in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.by_web_part_id(webPart_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_pages_base_site_page_microsoft_graph_site_page_canvas_layout_vertical_section_webparts_web_part_get_position_of_web_part(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action getPositionOfWebPart.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/canvasLayout/verticalSection/webparts/{webPart-id}/getPositionOfWebPart
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.canvas_layout.vertical_section.webparts.by_web_part_id(webPart_id).get_position_of_web_part.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_created_by_user_update_mailbox_settings(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/createdByUser/mailboxSettings
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.created_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_created_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/createdByUser/serviceProvisioningErrors
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.created_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_last_modified_by_user_update_mailbox_settings(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/lastModifiedByUser/mailboxSettings
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.last_modified_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_last_modified_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/lastModifiedByUser/serviceProvisioningErrors
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.last_modified_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_create_web_parts(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to webParts for sites.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_list_web_parts(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webParts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_delete_web_parts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete webPart.
        SharePoint operation: DELETE /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.by_webPart_id(webPart_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_get_web_parts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get webParts from sites.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.by_webPart_id(webPart_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_as_site_page_update_web_parts(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property webParts in sites.
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts/{webPart-id}
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.by_webPart_id(webPart_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_pages_base_site_page_microsoft_graph_site_page_web_parts_web_part_get_position_of_web_part(
        self,
        site_id: str,
        baseSitePage_id: str,
        webPart_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action getPositionOfWebPart.
        SharePoint operation: POST /sites/{site-id}/pages/{baseSitePage-id}/graph.sitePage/webParts/{webPart-id}/getPositionOfWebPart
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            webPart_id (str, required): SharePoint webPart id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).graph_site_page.web_parts.by_webPart_id(webPart_id).get_position_of_web_part.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_last_modified_by_user_update_mailbox_settings(
        self,
        site_id: str,
        baseSitePage_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update property mailboxSettings value..
        SharePoint operation: PATCH /sites/{site-id}/pages/{baseSitePage-id}/lastModifiedByUser/mailboxSettings
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).last_modified_by_user.mailbox_settings.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_pages_last_modified_by_user_list_service_provisioning_errors(
        self,
        site_id: str,
        baseSitePage_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get serviceProvisioningErrors property value.
        SharePoint operation: GET /sites/{site-id}/pages/{baseSitePage-id}/lastModifiedByUser/serviceProvisioningErrors
        Operation type: pages
        Args:
            site_id (str, required): SharePoint site id identifier
            baseSitePage_id (str, required): SharePoint baseSitePage id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = PagesRequestBuilder.PagesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = PagesRequestBuilder.PagesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).pages.by_base_site_page_id(baseSitePage_id).last_modified_by_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== CONTENTTYPES OPERATIONS (31 methods) ==========

    async def sites_create_content_types(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a content type.
        SharePoint operation: POST /sites/{site-id}/contentTypes
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_content_types(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List contentTypes in a site.
        SharePoint operation: GET /sites/{site-id}/contentTypes
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_add_copy(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action addCopy.
        SharePoint operation: POST /sites/{site-id}/contentTypes/addCopy
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.add_copy.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_add_copy_from_content_type_hub(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action addCopyFromContentTypeHub.
        SharePoint operation: POST /sites/{site-id}/contentTypes/addCopyFromContentTypeHub
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.add_copy_from_content_type_hub.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_get_compatible_hub_content_types(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function getCompatibleHubContentTypes.
        SharePoint operation: GET /sites/{site-id}/contentTypes/getCompatibleHubContentTypes()
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.get_compatible_hub_content_types().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_content_types(
        self,
        site_id: str,
        contentType_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete contentType.
        SharePoint operation: DELETE /sites/{site-id}/contentTypes/{contentType-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_content_types(
        self,
        site_id: str,
        contentType_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get contentType.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_content_types(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update contentType.
        SharePoint operation: PATCH /sites/{site-id}/contentTypes/{contentType-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_content_type_associate_with_hub_sites(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action associateWithHubSites.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/associateWithHubSites
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).associate_with_hub_sites.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_get_base(
        self,
        site_id: str,
        contentType_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get base from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/base
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).base.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_list_base_types(
        self,
        site_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get baseTypes from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/baseTypes
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).base_types.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_get_base_types(
        self,
        site_id: str,
        contentType_id: str,
        contentType_id1: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get baseTypes from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/baseTypes/{contentType-id1}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            contentType_id1 (str, required): SharePoint contentType id1 identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).base_types.by_baseType_id(contentType_id1).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_create_column_links(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to columnLinks for sites.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/columnLinks
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_links.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_list_column_links(
        self,
        site_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnLinks from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columnLinks
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_links.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_delete_column_links(
        self,
        site_id: str,
        contentType_id: str,
        columnLink_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columnLinks for sites.
        SharePoint operation: DELETE /sites/{site-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_get_column_links(
        self,
        site_id: str,
        contentType_id: str,
        columnLink_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnLinks from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_update_column_links(
        self,
        site_id: str,
        contentType_id: str,
        columnLink_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columnLinks in sites.
        SharePoint operation: PATCH /sites/{site-id}/contentTypes/{contentType-id}/columnLinks/{columnLink-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnLink_id (str, required): SharePoint columnLink id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_links.by_column_link_id(columnLink_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_list_column_positions(
        self,
        site_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnPositions from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columnPositions
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_positions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_get_column_positions(
        self,
        site_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnPositions from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columnPositions/{columnDefinition-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).column_positions.by_columnPosition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_create_columns(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a columnDefinition in a content type.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/columns
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_list_columns(
        self,
        site_id: str,
        contentType_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List columnDefinitions in a content type.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columns
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_delete_columns(
        self,
        site_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete columnDefinition.
        SharePoint operation: DELETE /sites/{site-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_get_columns(
        self,
        site_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columnDefinition.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_update_columns(
        self,
        site_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update columnDefinition.
        SharePoint operation: PATCH /sites/{site-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_content_types_columns_get_source_column(
        self,
        site_id: str,
        contentType_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sourceColumn from sites.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/columns/{columnDefinition-id}/sourceColumn
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).columns.by_column_definition_id(columnDefinition_id).source_column.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_content_type_copy_to_default_content_location(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action copyToDefaultContentLocation.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/copyToDefaultContentLocation
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).copy_to_default_content_location.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_content_type_is_published(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke function isPublished.
        SharePoint operation: GET /sites/{site-id}/contentTypes/{contentType-id}/isPublished()
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).is_published().get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_content_type_publish(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action publish.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/publish
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).publish.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_content_types_content_type_unpublish(
        self,
        site_id: str,
        contentType_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action unpublish.
        SharePoint operation: POST /sites/{site-id}/contentTypes/{contentType-id}/unpublish
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            contentType_id (str, required): SharePoint contentType id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).content_types.by_content_type_id(contentType_id).unpublish.post(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_create_content_types(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to contentTypes for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/contentTypes
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.content_types.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_content_types(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get contentTypes from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/contentTypes
        Operation type: contentTypes
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.content_types.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== COLUMNS OPERATIONS (11 methods) ==========

    async def sites_create_columns(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create a columnDefinition in a site.
        SharePoint operation: POST /sites/{site-id}/columns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_columns(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List columns in a site.
        SharePoint operation: GET /sites/{site-id}/columns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ColumnsRequestBuilder.ColumnsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ColumnsRequestBuilder.ColumnsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_columns(
        self,
        site_id: str,
        columnDefinition_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property columns for sites.
        SharePoint operation: DELETE /sites/{site-id}/columns/{columnDefinition-id}
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.by_column_definition_id(columnDefinition_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_columns(
        self,
        site_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/columns/{columnDefinition-id}
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ColumnsRequestBuilder.ColumnsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ColumnsRequestBuilder.ColumnsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.by_column_definition_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_columns(
        self,
        site_id: str,
        columnDefinition_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property columns in sites.
        SharePoint operation: PATCH /sites/{site-id}/columns/{columnDefinition-id}
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.by_column_definition_id(columnDefinition_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_columns_get_source_column(
        self,
        site_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get sourceColumn from sites.
        SharePoint operation: GET /sites/{site-id}/columns/{columnDefinition-id}/sourceColumn
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = ColumnsRequestBuilder.ColumnsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = ColumnsRequestBuilder.ColumnsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).columns.by_column_definition_id(columnDefinition_id).source_column.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_external_columns(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get externalColumns from sites.
        SharePoint operation: GET /sites/{site-id}/externalColumns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).external_columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_external_columns(
        self,
        site_id: str,
        columnDefinition_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get externalColumns from sites.
        SharePoint operation: GET /sites/{site-id}/externalColumns/{columnDefinition-id}
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            columnDefinition_id (str, required): SharePoint columnDefinition id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).external_columns.by_externalColumn_id(columnDefinition_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_create_columns(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to columns for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/columns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.columns.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_columns(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get columns from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/columns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_external_columns(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get externalColumns from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/externalColumns
        Operation type: columns
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.external_columns.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== PERMISSIONS OPERATIONS (8 methods) ==========

    async def sites_get_by_path_create_permissions(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to permissions for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/permissions
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.permissions.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_permissions(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get permissions from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/permissions
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.permissions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_create_permissions(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create permission.
        SharePoint operation: POST /sites/{site-id}/permissions
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_permissions(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List permissions.
        SharePoint operation: GET /sites/{site-id}/permissions
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_permissions(
        self,
        site_id: str,
        permission_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete permission.
        SharePoint operation: DELETE /sites/{site-id}/permissions/{permission-id}
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            permission_id (str, required): SharePoint permission id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.by_permission_id(permission_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_permissions(
        self,
        site_id: str,
        permission_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get permission.
        SharePoint operation: GET /sites/{site-id}/permissions/{permission-id}
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            permission_id (str, required): SharePoint permission id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.by_permission_id(permission_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_permissions(
        self,
        site_id: str,
        permission_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update permission.
        SharePoint operation: PATCH /sites/{site-id}/permissions/{permission-id}
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            permission_id (str, required): SharePoint permission id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.by_permission_id(permission_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_site_permissions_permission_grant(
        self,
        site_id: str,
        permission_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Invoke action grant.
        SharePoint operation: POST /sites/{site-id}/permissions/{permission-id}/grant
        Operation type: permissions
        Args:
            site_id (str, required): SharePoint site id identifier
            permission_id (str, required): SharePoint permission id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).permissions.by_permission_id(permission_id).grant.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== ANALYTICS OPERATIONS (18 methods) ==========

    async def sites_delete_analytics(
        self,
        site_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property analytics for sites.
        SharePoint operation: DELETE /sites/{site-id}/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_analytics(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get analytics from sites.
        SharePoint operation: GET /sites/{site-id}/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_analytics(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property analytics in sites.
        SharePoint operation: PATCH /sites/{site-id}/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_get_all_time(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get allTime from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/allTime
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.all_time.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_create_item_activity_stats(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to itemActivityStats for sites.
        SharePoint operation: POST /sites/{site-id}/analytics/itemActivityStats
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_list_item_activity_stats(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get itemActivityStats from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_delete_item_activity_stats(
        self,
        site_id: str,
        itemActivityStat_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property itemActivityStats for sites.
        SharePoint operation: DELETE /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_get_item_activity_stats(
        self,
        site_id: str,
        itemActivityStat_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get itemActivityStats from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_update_item_activity_stats(
        self,
        site_id: str,
        itemActivityStat_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property itemActivityStats in sites.
        SharePoint operation: PATCH /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_create_activities(
        self,
        site_id: str,
        itemActivityStat_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to activities for sites.
        SharePoint operation: POST /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_list_activities(
        self,
        site_id: str,
        itemActivityStat_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get activities from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_delete_activities(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property activities for sites.
        SharePoint operation: DELETE /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_get_activities(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get activities from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_item_activity_stats_update_activities(
        self,
        site_id: str,
        itemActivityStat_id: str,
        itemActivity_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property activities in sites.
        SharePoint operation: PATCH /sites/{site-id}/analytics/itemActivityStats/{itemActivityStat-id}/activities/{itemActivity-id}
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            itemActivityStat_id (str, required): SharePoint itemActivityStat id identifier
            itemActivity_id (str, required): SharePoint itemActivity id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.item_activity_stats.by_itemActivityStat_id(itemActivityStat_id).activities.by_activitie_id(itemActivity_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_analytics_get_last_seven_days(
        self,
        site_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get lastSevenDays from sites.
        SharePoint operation: GET /sites/{site-id}/analytics/lastSevenDays
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).analytics.last_seven_days.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_delete_analytics(
        self,
        site_id: str,
        path: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property analytics for sites.
        SharePoint operation: DELETE /sites/{site-id}/getByPath(path='{path}')/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.analytics.delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_get_analytics(
        self,
        site_id: str,
        path: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get analytics from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.analytics.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_update_analytics(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property analytics in sites.
        SharePoint operation: PATCH /sites/{site-id}/getByPath(path='{path}')/analytics
        Operation type: analytics
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.analytics.patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    # ========== OPERATIONS OPERATIONS (7 methods) ==========

    async def sites_get_by_path_create_operations(
        self,
        site_id: str,
        path: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to operations for sites.
        SharePoint operation: POST /sites/{site-id}/getByPath(path='{path}')/operations
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.operations.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_by_path_list_operations(
        self,
        site_id: str,
        path: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get operations from sites.
        SharePoint operation: GET /sites/{site-id}/getByPath(path='{path}')/operations
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            path (str, required): SharePoint path: path
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).get_by_path.operations.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_create_operations(
        self,
        site_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Create new navigation property to operations for sites.
        SharePoint operation: POST /sites/{site-id}/operations
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).operations.post(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_list_operations(
        self,
        site_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """List operations on a site.
        SharePoint operation: GET /sites/{site-id}/operations
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).operations.get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_delete_operations(
        self,
        site_id: str,
        richLongRunningOperation_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Delete navigation property operations for sites.
        SharePoint operation: DELETE /sites/{site-id}/operations/{richLongRunningOperation-id}
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).delete(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_get_operations(
        self,
        site_id: str,
        richLongRunningOperation_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Get richLongRunningOperation.
        SharePoint operation: GET /sites/{site-id}/operations/{richLongRunningOperation-id}
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = SitesRequestBuilder.SitesRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = SitesRequestBuilder.SitesRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).get(request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

    async def sites_update_operations(
        self,
        site_id: str,
        richLongRunningOperation_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> SharePointResponse:
        """Update the navigation property operations in sites.
        SharePoint operation: PATCH /sites/{site-id}/operations/{richLongRunningOperation-id}
        Operation type: operations
        Args:
            site_id (str, required): SharePoint site id identifier
            richLongRunningOperation_id (str, required): SharePoint richLongRunningOperation id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., fields, contentType, createdBy)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for sites, lists, or items by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for SharePoint operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            SharePointResponse: SharePoint response wrapper with success/data/error
        """
        # Build query parameters including OData for SharePoint
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in SharePoint
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.sites.by_site_id(site_id).operations.by_rich_long_running_operation_id(richLongRunningOperation_id).patch(body=request_body, request_configuration=config)
            return self._handle_sharepoint_response(response)
        except Exception as e:
            return SharePointResponse(
                success=False,
                error=f"SharePoint API call failed: {str(e)}",
            )

