# ruff: noqa
"""
IAP SSO (Google Cloud Identity-Aware Proxy) DataSource - Auto-generated API wrapper

Generated from Google Cloud IAP API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.iapsso.iapsso import IAPSSOClient, IAPSSOResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class IAPSSODataSource:
    """IAP SSO (Google Cloud Identity-Aware Proxy) DataSource

    Provides async wrapper methods for Google Cloud IAP API operations:
    - IAM Policy management (get, set, test permissions)
    - Tunnel Destination Groups management
    - OAuth Brands management
    - Identity-Aware Proxy Clients management

    All methods return IAPSSOResponse objects.
    """

    def __init__(self, client: IAPSSOClient) -> None:
        """Initialize with IAPSSOClient.

        Args:
            client: IAPSSOClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'IAPSSODataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> IAPSSOClient:
        """Return the underlying IAPSSOClient."""
        return self._client

    # -----------------------------------------------------------------------
    # IAM Policy
    # -----------------------------------------------------------------------

    async def get_iam_policy(
        self,
        resource: str
    ) -> IAPSSOResponse:
        """Get the IAM policy for an IAP-protected resource

        HTTP POST /{resource}:getIamPolicy

        Args:
            resource: The resource

        Returns:
            IAPSSOResponse with operation result
        """

        url = self.base_url + "/{resource}:getIamPolicy".format(resource=resource)


        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IAPSSOResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_iam_policy" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IAPSSOResponse(success=False, error=str(e), message="Failed to execute get_iam_policy")


    async def set_iam_policy(
        self,
        resource: str,
        policy: dict[str, Any]
    ) -> IAPSSOResponse:
        """Set the IAM policy for an IAP-protected resource

        HTTP POST /{resource}:setIamPolicy

        Args:
            resource: The resource
            policy: The IAM policy to set

        Returns:
            IAPSSOResponse with operation result
        """

        url = self.base_url + "/{resource}:setIamPolicy".format(resource=resource)

        body: dict[str, Any] = {}
        if policy is not None:
            body["policy"] = policy

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IAPSSOResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed set_iam_policy" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IAPSSOResponse(success=False, error=str(e), message="Failed to execute set_iam_policy")


    async def test_iam_permissions(
        self,
        resource: str,
        permissions: list[str]
    ) -> IAPSSOResponse:
        """Test IAM permissions for an IAP-protected resource

        HTTP POST /{resource}:testIamPermissions

        Args:
            resource: The resource
            permissions: List of permissions to test

        Returns:
            IAPSSOResponse with operation result
        """

        url = self.base_url + "/{resource}:testIamPermissions".format(resource=resource)

        body: dict[str, Any] = {}
        if permissions is not None:
            body["permissions"] = permissions

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IAPSSOResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed test_iam_permissions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IAPSSOResponse(success=False, error=str(e), message="Failed to execute test_iam_permissions")


    # -----------------------------------------------------------------------
    # Tunnel Dest Groups
    # -----------------------------------------------------------------------

    async def list_tunnel_dest_groups(
        self,
        project: str,
        location: str,
        *,
        pageSize: int | None = None,
        pageToken: str | None = None
    ) -> IAPSSOResponse:
        """List tunnel destination groups

        HTTP GET /projects/{project}/iap_tunnel/locations/{location}/destGroups

        Args:
            project: The project
            location: The location
            pageSize: Maximum number of results per page
            pageToken: Token for pagination

        Returns:
            IAPSSOResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if pageSize is not None:
            query_params['pageSize'] = str(pageSize)
        if pageToken is not None:
            query_params['pageToken'] = str(pageToken)

        url = self.base_url + "/projects/{project}/iap_tunnel/locations/{location}/destGroups".format(project=project, location=location)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IAPSSOResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tunnel_dest_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IAPSSOResponse(success=False, error=str(e), message="Failed to execute list_tunnel_dest_groups")


    async def get_tunnel_dest_group(
        self,
        project: str,
        location: str,
        dest_group_id: str
    ) -> IAPSSOResponse:
        """Get a specific tunnel destination group

        HTTP GET /projects/{project}/iap_tunnel/locations/{location}/destGroups/{dest_group_id}

        Args:
            project: The project
            location: The location
            dest_group_id: The dest group id

        Returns:
            IAPSSOResponse with operation result
        """

        url = self.base_url + "/projects/{project}/iap_tunnel/locations/{location}/destGroups/{dest_group_id}".format(project=project, location=location, dest_group_id=dest_group_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IAPSSOResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_tunnel_dest_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IAPSSOResponse(success=False, error=str(e), message="Failed to execute get_tunnel_dest_group")


    # -----------------------------------------------------------------------
    # Brands
    # -----------------------------------------------------------------------

    async def list_brands(
        self,
        project: str
    ) -> IAPSSOResponse:
        """List OAuth brands for a project

        HTTP GET /projects/{project}/brands

        Args:
            project: The project

        Returns:
            IAPSSOResponse with operation result
        """

        url = self.base_url + "/projects/{project}/brands".format(project=project)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IAPSSOResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_brands" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IAPSSOResponse(success=False, error=str(e), message="Failed to execute list_brands")


    # -----------------------------------------------------------------------
    # IAP Clients
    # -----------------------------------------------------------------------

    async def list_iap_clients(
        self,
        project: str,
        brand_id: str,
        *,
        pageSize: int | None = None,
        pageToken: str | None = None
    ) -> IAPSSOResponse:
        """List Identity-Aware Proxy clients for a brand

        HTTP GET /projects/{project}/brands/{brand_id}/identityAwareProxyClients

        Args:
            project: The project
            brand_id: The brand id
            pageSize: Maximum number of results per page
            pageToken: Token for pagination

        Returns:
            IAPSSOResponse with operation result
        """

        query_params: dict[str, Any] = {}
        if pageSize is not None:
            query_params['pageSize'] = str(pageSize)
        if pageToken is not None:
            query_params['pageToken'] = str(pageToken)

        url = self.base_url + "/projects/{project}/brands/{brand_id}/identityAwareProxyClients".format(project=project, brand_id=brand_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IAPSSOResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_iap_clients" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IAPSSOResponse(success=False, error=str(e), message="Failed to execute list_iap_clients")


