# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportMissingParameterType=false, reportUnnecessaryIsInstance=false

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, Optional

from kiota_abstractions.base_request_configuration import (  # type: ignore
    RequestConfiguration,
)
from msgraph.generated.groups.groups_request_builder import (  # type: ignore
    GroupsRequestBuilder,
)
from msgraph.generated.organization.organization_request_builder import (  # type: ignore
    OrganizationRequestBuilder,
)

from app.sources.client.microsoft.microsoft import MSGraphClient


# Entra ID-specific response wrapper
class EntraIDResponse:
    """Standardized Entra ID API response wrapper."""
    success: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def __init__(self, success: bool, data: Optional[dict[str, Any]] = None, error: Optional[str] = None, message: Optional[str] = None) -> None:
        self.success = success
        self.data = data
        self.error = error
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

# Set up logger
logger = logging.getLogger(__name__)

class EntraIDDataSource:
    """
    Microsoft Entra ID (Azure Active Directory) API client for SSO, security,
    and identity management operations.

    This datasource covers Entra ID-specific concerns that go beyond basic
    user/group CRUD:

    Moved from UsersGroupsDataSource:
    - Organization Branding: logo, favicon, background, custom CSS, localizations
    - Group Lifecycle Policies: creation, renewal, expiration management
    - Domains: domain management, verification, DNS records, federation config
    - Subscriptions: webhook subscription management
    - Tenant Relationships: multi-tenant organization management
    - Cross-Tenant Access Policies: partner configuration templates
    - Certificate-Based Auth Configuration: cert auth setup for organizations

    New Entra ID APIs:
    - Service Principals: application identity objects in the directory
    - Applications (App Registrations): registered application management
    - Directory Roles: built-in and custom role management
    - Audit Logs: sign-in logs and directory audit logs
    - Conditional Access: policies governing access control
    - Identity Providers: federated identity provider configuration
    - Administrative Units: scoped admin management boundaries
    - Authentication Methods: user MFA and auth method management

    All methods use the Microsoft Graph SDK via the shared MSGraphClient.
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize with Microsoft Graph SDK client for Entra ID operations."""
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "users"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Entra ID client initialized")

    def _handle_entra_id_response(self, response: object) -> EntraIDResponse:
        """Handle Entra ID API response with comprehensive error handling."""
        try:
            if response is None:
                return EntraIDResponse(success=False, error="Empty response from Entra ID API")

            success = True
            error_msg = None

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

            return EntraIDResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling Entra ID response: {e}")
            return EntraIDResponse(success=False, error=str(e))

    def get_data_source(self) -> 'EntraIDDataSource':
        """Get the underlying Entra ID client."""
        return self

    # ==========================================================================
    # ORGANIZATION BRANDING OPERATIONS (moved from UsersGroupsDataSource)
    # ==========================================================================

    async def organization_delete_branding(
        self,
        organization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete organizationalBranding.
        Entra ID operation: DELETE /organization/{organization-id}/branding
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_get_branding(
        self,
        organization_id: str,
        dollar_select: Optional[list[str]] = None,
        dollar_expand: Optional[list[str]] = None,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get organizationalBranding.
        Entra ID operation: GET /organization/{organization-id}/branding
        """
        try:
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter

            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_update_branding(
        self,
        organization_id: str,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update organizationalBranding.
        Entra ID operation: PATCH /organization/{organization-id}/branding
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_delete_branding_background_image(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete branding background image.
        Entra ID operation: DELETE /organization/{organization-id}/branding/backgroundImage
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.background_image.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_get_branding_background_image(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get branding background image.
        Entra ID operation: GET /organization/{organization-id}/branding/backgroundImage
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.background_image.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_update_branding_background_image(
        self,
        organization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update branding background image.
        Entra ID operation: PUT /organization/{organization-id}/branding/backgroundImage
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.background_image.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_delete_branding_banner_logo(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete branding banner logo.
        Entra ID operation: DELETE /organization/{organization-id}/branding/bannerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.banner_logo.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_get_branding_banner_logo(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get branding banner logo.
        Entra ID operation: GET /organization/{organization-id}/branding/bannerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.banner_logo.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_update_branding_banner_logo(
        self,
        organization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update branding banner logo.
        Entra ID operation: PUT /organization/{organization-id}/branding/bannerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.banner_logo.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_delete_branding_custom_css(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete branding custom CSS.
        Entra ID operation: DELETE /organization/{organization-id}/branding/customCSS
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.custom_c_s_s.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_get_branding_custom_css(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get branding custom CSS.
        Entra ID operation: GET /organization/{organization-id}/branding/customCSS
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.custom_c_s_s.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_update_branding_custom_css(
        self,
        organization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update branding custom CSS.
        Entra ID operation: PUT /organization/{organization-id}/branding/customCSS
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.custom_c_s_s.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_delete_branding_favicon(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete branding favicon.
        Entra ID operation: DELETE /organization/{organization-id}/branding/favicon
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.favicon.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_get_branding_favicon(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get branding favicon.
        Entra ID operation: GET /organization/{organization-id}/branding/favicon
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.favicon.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_update_branding_favicon(
        self,
        organization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update branding favicon.
        Entra ID operation: PUT /organization/{organization-id}/branding/favicon
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.favicon.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_delete_branding_header_logo(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete branding header logo.
        Entra ID operation: DELETE /organization/{organization-id}/branding/headerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.header_logo.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_get_branding_header_logo(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get branding header logo.
        Entra ID operation: GET /organization/{organization-id}/branding/headerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.header_logo.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_update_branding_header_logo(
        self,
        organization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update branding header logo.
        Entra ID operation: PUT /organization/{organization-id}/branding/headerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.header_logo.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_create_localizations(
        self,
        organization_id: str,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Create branding localization.
        Entra ID operation: POST /organization/{organization-id}/branding/localizations
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_list_localizations(
        self,
        organization_id: str,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """List branding localizations.
        Entra ID operation: GET /organization/{organization-id}/branding/localizations
        """
        try:
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter

            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params
            if headers:
                config.headers = headers
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete branding localization.
        Entra ID operation: DELETE /organization/{organization-id}/branding/localizations/{localization-id}
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get branding localization.
        Entra ID operation: GET /organization/{organization-id}/branding/localizations/{localization-id}
        """
        try:
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]

            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update branding localization.
        Entra ID operation: PATCH /organization/{organization-id}/branding/localizations/{localization-id}
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_background_image(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete localization background image.
        Entra ID operation: DELETE /organization/{organization-id}/branding/localizations/{localization-id}/backgroundImage
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).background_image.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_background_image(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get localization background image.
        Entra ID operation: GET /organization/{organization-id}/branding/localizations/{localization-id}/backgroundImage
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).background_image.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_background_image(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update localization background image.
        Entra ID operation: PUT /organization/{organization-id}/branding/localizations/{localization-id}/backgroundImage
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).background_image.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_banner_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete localization banner logo.
        Entra ID operation: DELETE /organization/{organization-id}/branding/localizations/{localization-id}/bannerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).banner_logo.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_banner_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get localization banner logo.
        Entra ID operation: GET /organization/{organization-id}/branding/localizations/{localization-id}/bannerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).banner_logo.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_banner_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update localization banner logo.
        Entra ID operation: PUT /organization/{organization-id}/branding/localizations/{localization-id}/bannerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).banner_logo.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_custom_css(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete localization custom CSS.
        Entra ID operation: DELETE /organization/{organization-id}/branding/localizations/{localization-id}/customCSS
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).custom_c_s_s.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_custom_css(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get localization custom CSS.
        Entra ID operation: GET /organization/{organization-id}/branding/localizations/{localization-id}/customCSS
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).custom_c_s_s.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_custom_css(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update localization custom CSS.
        Entra ID operation: PUT /organization/{organization-id}/branding/localizations/{localization-id}/customCSS
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).custom_c_s_s.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_favicon(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete localization favicon.
        Entra ID operation: DELETE /organization/{organization-id}/branding/localizations/{localization-id}/favicon
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).favicon.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_favicon(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get localization favicon.
        Entra ID operation: GET /organization/{organization-id}/branding/localizations/{localization-id}/favicon
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).favicon.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_favicon(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update localization favicon.
        Entra ID operation: PUT /organization/{organization-id}/branding/localizations/{localization-id}/favicon
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).favicon.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_header_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete localization header logo.
        Entra ID operation: DELETE /organization/{organization-id}/branding/localizations/{localization-id}/headerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).header_logo.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_header_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get localization header logo.
        Entra ID operation: GET /organization/{organization-id}/branding/localizations/{localization-id}/headerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).header_logo.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_header_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update localization header logo.
        Entra ID operation: PUT /organization/{organization-id}/branding/localizations/{localization-id}/headerLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).header_logo.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_square_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete localization square logo.
        Entra ID operation: DELETE /organization/{organization-id}/branding/localizations/{localization-id}/squareLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_square_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get localization square logo.
        Entra ID operation: GET /organization/{organization-id}/branding/localizations/{localization-id}/squareLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_square_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update localization square logo.
        Entra ID operation: PUT /organization/{organization-id}/branding/localizations/{localization-id}/squareLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_square_logo_dark(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete localization square logo dark.
        Entra ID operation: DELETE /organization/{organization-id}/branding/localizations/{localization-id}/squareLogoDark
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo_dark.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_square_logo_dark(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get localization square logo dark.
        Entra ID operation: GET /organization/{organization-id}/branding/localizations/{localization-id}/squareLogoDark
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo_dark.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_square_logo_dark(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update localization square logo dark.
        Entra ID operation: PUT /organization/{organization-id}/branding/localizations/{localization-id}/squareLogoDark
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo_dark.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_delete_branding_square_logo(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete branding square logo.
        Entra ID operation: DELETE /organization/{organization-id}/branding/squareLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_get_branding_square_logo(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get branding square logo.
        Entra ID operation: GET /organization/{organization-id}/branding/squareLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_update_branding_square_logo(
        self,
        organization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update branding square logo.
        Entra ID operation: PUT /organization/{organization-id}/branding/squareLogo
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_delete_branding_square_logo_dark(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete branding square logo dark.
        Entra ID operation: DELETE /organization/{organization-id}/branding/squareLogoDark
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo_dark.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_get_branding_square_logo_dark(
        self,
        organization_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get branding square logo dark.
        Entra ID operation: GET /organization/{organization-id}/branding/squareLogoDark
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo_dark.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_update_branding_square_logo_dark(
        self,
        organization_id: str,
        request_body: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update branding square logo dark.
        Entra ID operation: PUT /organization/{organization-id}/branding/squareLogoDark
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo_dark.put(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    # ==========================================================================
    # CERTIFICATE-BASED AUTH CONFIGURATION (moved from UsersGroupsDataSource)
    # ==========================================================================

    async def organization_create_certificate_based_auth_configuration(
        self,
        organization_id: str,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Create certificateBasedAuthConfiguration.
        Entra ID operation: POST /organization/{organization-id}/certificateBasedAuthConfiguration
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).certificate_based_auth_configuration.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_list_certificate_based_auth_configuration(
        self,
        organization_id: str,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """List certificateBasedAuthConfigurations.
        Entra ID operation: GET /organization/{organization-id}/certificateBasedAuthConfiguration
        """
        try:
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter

            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params
            if headers:
                config.headers = headers
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).certificate_based_auth_configuration.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_delete_certificate_based_auth_configuration(
        self,
        organization_id: str,
        certificateBasedAuthConfiguration_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete certificateBasedAuthConfiguration.
        Entra ID operation: DELETE /organization/{organization-id}/certificateBasedAuthConfiguration/{id}
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).certificate_based_auth_configuration.by_certificate_based_auth_configuration_id(certificateBasedAuthConfiguration_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def organization_get_certificate_based_auth_configuration(
        self,
        organization_id: str,
        certificateBasedAuthConfiguration_id: str,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get certificateBasedAuthConfiguration.
        Entra ID operation: GET /organization/{organization-id}/certificateBasedAuthConfiguration/{id}
        """
        try:
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]

            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params
            if headers:
                config.headers = headers

            response = await self.client.organization.by_organization_id(organization_id).certificate_based_auth_configuration.by_certificate_based_auth_configuration_id(certificateBasedAuthConfiguration_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    # ==========================================================================
    # CROSS-TENANT / MULTI-TENANT OPERATIONS (moved from UsersGroupsDataSource)
    # ==========================================================================

    async def policies_cross_tenant_access_policy_templates_delete_multi_tenant_organization_partner_configuration(
        self,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete multiTenantOrganizationPartnerConfiguration for policies.
        Entra ID operation: DELETE /policies/crossTenantAccessPolicy/templates/multiTenantOrganizationPartnerConfiguration
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.policies.cross_tenant_access_policy.templates.multi_tenant_organization_partner_configuration.delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def tenant_relationships_get_multi_tenant_organization(
        self,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get multiTenantOrganization.
        Entra ID operation: GET /tenantRelationships/multiTenantOrganization
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.tenant_relationships.multi_tenant_organization.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def tenant_relationships_update_multi_tenant_organization(
        self,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update multiTenantOrganization.
        Entra ID operation: PATCH /tenantRelationships/multiTenantOrganization
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.tenant_relationships.multi_tenant_organization.patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def tenant_relationships_multi_tenant_organization_get_join_request(
        self,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get multiTenantOrganizationJoinRequestRecord.
        Entra ID operation: GET /tenantRelationships/multiTenantOrganization/joinRequest
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.tenant_relationships.multi_tenant_organization.join_request.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def tenant_relationships_multi_tenant_organization_list_tenants(
        self,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """List multiTenantOrganizationMembers.
        Entra ID operation: GET /tenantRelationships/multiTenantOrganization/tenants
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def tenant_relationships_multi_tenant_organization_delete_tenants(
        self,
        multiTenantOrganizationMember_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Remove multiTenantOrganizationMember.
        Entra ID operation: DELETE /tenantRelationships/multiTenantOrganization/tenants/{id}
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.by_tenant_id(multiTenantOrganizationMember_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def tenant_relationships_multi_tenant_organization_get_tenants(
        self,
        multiTenantOrganizationMember_id: str,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get multiTenantOrganizationMember.
        Entra ID operation: GET /tenantRelationships/multiTenantOrganization/tenants/{id}
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.by_tenant_id(multiTenantOrganizationMember_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def tenant_relationships_multi_tenant_organization_update_tenants(
        self,
        multiTenantOrganizationMember_id: str,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update tenant in tenantRelationships.
        Entra ID operation: PATCH /tenantRelationships/multiTenantOrganization/tenants/{id}
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.by_tenant_id(multiTenantOrganizationMember_id).patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    # ==========================================================================
    # GROUP LIFECYCLE POLICIES (moved from UsersGroupsDataSource)
    # ==========================================================================

    async def groups_create_group_lifecycle_policies(
        self,
        group_id: str,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Create groupLifecyclePolicy for a group.
        Entra ID operation: POST /groups/{group-id}/groupLifecyclePolicies
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def groups_list_group_lifecycle_policies(
        self,
        group_id: str,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """List groupLifecyclePolicies for a group.
        Entra ID operation: GET /groups/{group-id}/groupLifecyclePolicies
        """
        try:
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter

            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params
            if headers:
                config.headers = headers
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def groups_delete_group_lifecycle_policies(
        self,
        group_id: str,
        groupLifecyclePolicy_id: str,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Delete groupLifecyclePolicy.
        Entra ID operation: DELETE /groups/{group-id}/groupLifecyclePolicies/{id}
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def groups_get_group_lifecycle_policies(
        self,
        group_id: str,
        groupLifecyclePolicy_id: str,
        select: Optional[list[str]] = None,
        expand: Optional[list[str]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Get groupLifecyclePolicy.
        Entra ID operation: GET /groups/{group-id}/groupLifecyclePolicies/{id}
        """
        try:
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]

            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params
            if headers:
                config.headers = headers

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def groups_update_group_lifecycle_policies(
        self,
        group_id: str,
        groupLifecyclePolicy_id: str,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Update groupLifecyclePolicy.
        Entra ID operation: PATCH /groups/{group-id}/groupLifecyclePolicies/{id}
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def groups_group_group_lifecycle_policies_group_lifecycle_policy_add_group(
        self,
        group_id: str,
        groupLifecyclePolicy_id: str,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Invoke action addGroup on a lifecycle policy.
        Entra ID operation: POST /groups/{group-id}/groupLifecyclePolicies/{id}/addGroup
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).add_group.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    async def groups_group_group_lifecycle_policies_group_lifecycle_policy_remove_group(
        self,
        group_id: str,
        groupLifecyclePolicy_id: str,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs
    ) -> EntraIDResponse:
        """Invoke action removeGroup on a lifecycle policy.
        Entra ID operation: POST /groups/{group-id}/groupLifecyclePolicies/{id}/removeGroup
        """
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).remove_group.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(
                success=False,
                error=f"Entra ID API call failed: {str(e)}",
            )

    # ==========================================================================
    # DOMAINS OPERATIONS (moved from UsersGroupsDataSource)
    # ==========================================================================

    async def domain_dns_records_create(self, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Add new entity to domainDnsRecords. Entra ID operation: POST /domainDnsRecords"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domain_dns_records.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domain_dns_records_list(self, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get entities from domainDnsRecords. Entra ID operation: GET /domainDnsRecords"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domain_dns_records.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domain_dns_records_delete(self, domainDnsRecord_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Delete entity from domainDnsRecords. Entra ID operation: DELETE /domainDnsRecords/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domain_dns_records.by_domainDnsRecord_id(domainDnsRecord_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domain_dns_records_get(self, domainDnsRecord_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get entity from domainDnsRecords by key. Entra ID operation: GET /domainDnsRecords/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domain_dns_records.by_domainDnsRecord_id(domainDnsRecord_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domain_dns_records_update(self, domainDnsRecord_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Update entity in domainDnsRecords. Entra ID operation: PATCH /domainDnsRecords/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domain_dns_records.by_domainDnsRecord_id(domainDnsRecord_id).patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_create(self, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Create domain. Entra ID operation: POST /domains"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_list(self, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List domains. Entra ID operation: GET /domains"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_delete(self, domain_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Delete domain. Entra ID operation: DELETE /domains/{domain-id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_get(self, domain_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get domain. Entra ID operation: GET /domains/{domain-id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_update(self, domain_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Update domain. Entra ID operation: PATCH /domains/{domain-id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_create_federation_configuration(self, domain_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Create federation configuration. Entra ID operation: POST /domains/{domain-id}/federationConfiguration"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).federation_configuration.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_list_federation_configuration(self, domain_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List federation configurations. Entra ID operation: GET /domains/{domain-id}/federationConfiguration"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).federation_configuration.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_delete_federation_configuration(self, domain_id: str, internalDomainFederation_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Delete federation configuration. Entra ID operation: DELETE /domains/{domain-id}/federationConfiguration/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).federation_configuration.by_internal_domain_federation_id(internalDomainFederation_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_get_federation_configuration(self, domain_id: str, internalDomainFederation_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get federation configuration. Entra ID operation: GET /domains/{domain-id}/federationConfiguration/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).federation_configuration.by_internal_domain_federation_id(internalDomainFederation_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_update_federation_configuration(self, domain_id: str, internalDomainFederation_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Update federation configuration. Entra ID operation: PATCH /domains/{domain-id}/federationConfiguration/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).federation_configuration.by_internal_domain_federation_id(internalDomainFederation_id).patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_force_delete(self, domain_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Force delete domain. Entra ID operation: POST /domains/{domain-id}/forceDelete"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).force_delete.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_promote(self, domain_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Promote domain. Entra ID operation: POST /domains/{domain-id}/promote"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).promote.post(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_get_root_domain(self, domain_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get root domain. Entra ID operation: GET /domains/{domain-id}/rootDomain"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).root_domain.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_create_service_configuration_records(self, domain_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Create service configuration record. Entra ID operation: POST /domains/{domain-id}/serviceConfigurationRecords"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).service_configuration_records.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_list_service_configuration_records(self, domain_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List service configuration records. Entra ID operation: GET /domains/{domain-id}/serviceConfigurationRecords"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).service_configuration_records.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_delete_service_configuration_records(self, domain_id: str, domainDnsRecord_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Delete service configuration record. Entra ID operation: DELETE /domains/{domain-id}/serviceConfigurationRecords/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).service_configuration_records.by_domain_dns_record_id(domainDnsRecord_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_get_service_configuration_records(self, domain_id: str, domainDnsRecord_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get service configuration record. Entra ID operation: GET /domains/{domain-id}/serviceConfigurationRecords/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).service_configuration_records.by_domain_dns_record_id(domainDnsRecord_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_update_service_configuration_records(self, domain_id: str, domainDnsRecord_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Update service configuration record. Entra ID operation: PATCH /domains/{domain-id}/serviceConfigurationRecords/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).service_configuration_records.by_domain_dns_record_id(domainDnsRecord_id).patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_create_verification_dns_records(self, domain_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Create verification DNS record. Entra ID operation: POST /domains/{domain-id}/verificationDnsRecords"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).verification_dns_records.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_list_verification_dns_records(self, domain_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List verification DNS records. Entra ID operation: GET /domains/{domain-id}/verificationDnsRecords"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).verification_dns_records.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_delete_verification_dns_records(self, domain_id: str, domainDnsRecord_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Delete verification DNS record. Entra ID operation: DELETE /domains/{domain-id}/verificationDnsRecords/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).verification_dns_records.by_domain_dns_record_id(domainDnsRecord_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_get_verification_dns_records(self, domain_id: str, domainDnsRecord_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get verification DNS record. Entra ID operation: GET /domains/{domain-id}/verificationDnsRecords/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).verification_dns_records.by_domain_dns_record_id(domainDnsRecord_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_update_verification_dns_records(self, domain_id: str, domainDnsRecord_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Update verification DNS record. Entra ID operation: PATCH /domains/{domain-id}/verificationDnsRecords/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).verification_dns_records.by_domain_dns_record_id(domainDnsRecord_id).patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def domains_verify(self, domain_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Verify domain. Entra ID operation: POST /domains/{domain-id}/verify"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.domains.by_domain_id(domain_id).verify.post(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    # ==========================================================================
    # SUBSCRIPTIONS OPERATIONS (moved from UsersGroupsDataSource)
    # ==========================================================================

    async def subscriptions_create(self, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Create subscription. Entra ID operation: POST /subscriptions"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.subscriptions.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def subscriptions_list(self, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List subscriptions. Entra ID operation: GET /subscriptions"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.subscriptions.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def subscriptions_delete(self, subscription_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Delete subscription. Entra ID operation: DELETE /subscriptions/{subscription-id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.subscriptions.by_subscription_id(subscription_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def subscriptions_get(self, subscription_id: str, select: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get subscription. Entra ID operation: GET /subscriptions/{subscription-id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.subscriptions.by_subscription_id(subscription_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def subscriptions_update(self, subscription_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Update subscription. Entra ID operation: PATCH /subscriptions/{subscription-id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.subscriptions.by_subscription_id(subscription_id).patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def subscriptions_reauthorize(self, subscription_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Reauthorize subscription. Entra ID operation: POST /subscriptions/{subscription-id}/reauthorize"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.subscriptions.by_subscription_id(subscription_id).reauthorize.post(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    # ==========================================================================
    # NEW: SERVICE PRINCIPALS
    # ==========================================================================

    async def list_service_principals(self, select: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, search: Optional[str] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List service principals. Entra ID operation: GET /servicePrincipals"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'
            response = await self.client.service_principals.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def get_service_principal(self, service_principal_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get service principal. Entra ID operation: GET /servicePrincipals/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.service_principals.by_service_principal_id(service_principal_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def create_service_principal(self, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Create service principal. Entra ID operation: POST /servicePrincipals"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.service_principals.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def delete_service_principal(self, service_principal_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Delete service principal. Entra ID operation: DELETE /servicePrincipals/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.service_principals.by_service_principal_id(service_principal_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    # ==========================================================================
    # NEW: APPLICATIONS (App Registrations)
    # ==========================================================================

    async def list_applications(self, select: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, search: Optional[str] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List applications. Entra ID operation: GET /applications"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'
            response = await self.client.applications.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def get_application(self, application_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get application. Entra ID operation: GET /applications/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.applications.by_application_id(application_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def create_application(self, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Create application. Entra ID operation: POST /applications"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.applications.post(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def update_application(self, application_id: str, request_body: Optional[Mapping[str, Any]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Update application. Entra ID operation: PATCH /applications/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.applications.by_application_id(application_id).patch(body=request_body, request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def delete_application(self, application_id: str, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Delete application. Entra ID operation: DELETE /applications/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.applications.by_application_id(application_id).delete(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    # ==========================================================================
    # NEW: DIRECTORY ROLES
    # ==========================================================================

    async def list_directory_roles(self, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List directory roles. Entra ID operation: GET /directoryRoles"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.directory_roles.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def get_directory_role(self, directory_role_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get directory role. Entra ID operation: GET /directoryRoles/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.directory_roles.by_directory_role_id(directory_role_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def list_directory_role_members(self, directory_role_id: str, select: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List directory role members. Entra ID operation: GET /directoryRoles/{id}/members"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.directory_roles.by_directory_role_id(directory_role_id).members.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    # ==========================================================================
    # NEW: AUDIT LOGS
    # ==========================================================================

    async def list_sign_in_logs(self, select: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, orderby: Optional[str] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List sign-in logs. Entra ID operation: GET /auditLogs/signIns"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.audit_logs.sign_ins.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def list_directory_audit_logs(self, select: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, orderby: Optional[str] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List directory audit logs. Entra ID operation: GET /auditLogs/directoryAudits"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.audit_logs.directory_audits.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    # ==========================================================================
    # NEW: CONDITIONAL ACCESS POLICIES
    # ==========================================================================

    async def list_conditional_access_policies(self, select: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List conditional access policies. Entra ID operation: GET /identity/conditionalAccess/policies"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.identity.conditional_access.policies.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def get_conditional_access_policy(self, policy_id: str, select: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get conditional access policy. Entra ID operation: GET /identity/conditionalAccess/policies/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.identity.conditional_access.policies.by_conditional_access_policy_id(policy_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    # ==========================================================================
    # NEW: IDENTITY PROVIDERS
    # ==========================================================================

    async def list_identity_providers(self, select: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List identity providers. Entra ID operation: GET /identity/identityProviders"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.identity.identity_providers.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def get_identity_provider(self, identity_provider_id: str, select: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get identity provider. Entra ID operation: GET /identity/identityProviders/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.identity.identity_providers.by_identity_provider_base_id(identity_provider_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    # ==========================================================================
    # NEW: ADMINISTRATIVE UNITS
    # ==========================================================================

    async def list_administrative_units(self, select: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, search: Optional[str] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List administrative units. Entra ID operation: GET /directory/administrativeUnits"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'
            response = await self.client.directory.administrative_units.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def get_administrative_unit(self, administrative_unit_id: str, select: Optional[list[str]] = None, expand: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """Get administrative unit. Entra ID operation: GET /directory/administrativeUnits/{id}"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.directory.administrative_units.by_administrative_unit_id(administrative_unit_id).get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    async def list_administrative_unit_members(self, administrative_unit_id: str, select: Optional[list[str]] = None, filter: Optional[str] = None, top: Optional[int] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List administrative unit members. Entra ID operation: GET /directory/administrativeUnits/{id}/members"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.directory.administrative_units.by_administrative_unit_id(administrative_unit_id).members.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")

    # ==========================================================================
    # NEW: USER AUTHENTICATION METHODS
    # ==========================================================================

    async def list_user_authentication_methods(self, user_id: str, select: Optional[list[str]] = None, headers: Optional[dict[str, str]] = None, **kwargs) -> EntraIDResponse:
        """List user authentication methods. Entra ID operation: GET /users/{user-id}/authentication/methods"""
        try:
            config = RequestConfiguration()
            if headers:
                config.headers = headers
            response = await self.client.users.by_user_id(user_id).authentication.methods.get(request_configuration=config)
            return self._handle_entra_id_response(response)
        except Exception as e:
            return EntraIDResponse(success=False, error=f"Entra ID API call failed: {str(e)}")
