"""DocuSign API Client using official Python SDK.

This module provides a comprehensive wrapper around the official DocuSign
eSignature Python SDK for integration with the PipesHub platform.
"""

import base64
import logging
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode

from pydantic import BaseModel  # type: ignore

from typing import Any

from docusign_esign import (
    AccountsApi,
    ApiClient,
    ApiException,
    BulkEnvelopesApi,
    EnvelopesApi,
    FoldersApi,
    GroupsApi,
    TemplatesApi,
    UsersApi,
    WorkspacesApi,
)

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient


class DocuSignClientError(Exception):
    """Base exception for DocuSign client errors."""


class DocuSignClient:
    """Comprehensive client for interacting with DocuSign eSignature API.

    This client wraps the official DocuSign Python SDK and provides
    simplified methods for all major API operations.

    Attributes:
        account_id: DocuSign account identifier
        client: DocuSign ApiClient instance
        accounts: AccountsApi instance for account operations
        envelopes: EnvelopesApi instance for envelope operations
        templates: TemplatesApi instance for template operations
        users: UsersApi instance for user operations
        folders: FoldersApi instance for folder operations
        groups: GroupsApi instance for group operations
        bulk_envelopes: BulkEnvelopesApi instance for bulk operations
        workspaces: WorkspacesApi instance for workspace operations
    """

    def __init__(
        self,
        access_token: str,
        base_uri: str,
        account_id: str,
    ) -> None:
        """Initialize the DocuSign client.

        Args:
            access_token: OAuth access token for authentication
            base_uri: Base URI for the DocuSign API
            account_id: DocuSign account ID

        Raises:
            DocuSignClientError: If initialization fails
        """
        try:
            self.account_id = account_id
            self.client = ApiClient()
            self.client.host = f"{base_uri}/restapi"
            self.client.set_default_header(
                "Authorization", f"Bearer {access_token}"
            )

            # Initialize all API instances
            self.accounts = AccountsApi(self.client)
            self.envelopes = EnvelopesApi(self.client)
            self.templates = TemplatesApi(self.client)
            self.users = UsersApi(self.client)
            self.folders = FoldersApi(self.client)
            self.groups = GroupsApi(self.client)
            self.bulk_envelopes = BulkEnvelopesApi(self.client)
            self.workspaces = WorkspacesApi(self.client)
        except Exception as e:
            msg = f"Failed to initialize DocuSign client: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # ENVELOPE OPERATIONS
    # ========================================================================

    def list_envelopes(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        status: str | None = None,
        folder_ids: str | None = None,
        count: str = "100",
        start_position: str | None = None,
    ) -> dict[str, Any]:
        """List envelopes with optional filters.

        Args:
            from_date: Start date for filtering (ISO 8601 format)
            to_date: End date for filtering (ISO 8601 format)
            status: Filter by envelope status
            folder_ids: Comma-separated list of folder IDs
            count: Maximum number of results (default: 100)

        Returns:
            Dictionary containing envelope list and metadata

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            kwargs: dict[str, Any] = {"count": count}
            if from_date:
                kwargs["from_date"] = from_date
            if to_date:
                kwargs["to_date"] = to_date
            if status:
                kwargs["status"] = status
            if folder_ids:
                kwargs["folder_ids"] = folder_ids

            response = self.envelopes.list_status_changes(
                self.account_id, **kwargs
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list envelopes: {e}"
            raise DocuSignClientError(msg) from e

    def get_envelope(self, envelope_id: str) -> dict[str, Any]:
        """Get details of a specific envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing envelope details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.envelopes.get_envelope(
                self.account_id, envelope_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get envelope {envelope_id}: {e}"
            raise DocuSignClientError(msg) from e

    def get_envelope_documents(self, envelope_id: str) -> dict[str, Any]:
        """List all documents in an envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing document list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.envelopes.list_documents(
                self.account_id, envelope_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get documents for envelope {envelope_id}: {e}"
            raise DocuSignClientError(msg) from e

    def download_document(
        self,
        envelope_id: str,
        document_id: str,
    ) -> bytes:
        """Download a specific document from an envelope.

        Args:
            envelope_id: The envelope ID
            document_id: The document ID

        Returns:
            Document content as bytes

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            return self.envelopes.get_document(
                self.account_id,
                document_id,
                envelope_id,
            )
        except ApiException as e:
            msg = (
                f"Failed to download document {document_id} "
                f"from envelope {envelope_id}: {e}"
            )
            raise DocuSignClientError(msg) from e

    def list_recipients(self, envelope_id: str) -> dict[str, Any]:
        """List all recipients of an envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing recipient list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.envelopes.list_recipients(
                self.account_id, envelope_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list recipients for envelope {envelope_id}: {e}"
            raise DocuSignClientError(msg) from e

    def get_envelope_audit_events(self, envelope_id: str) -> dict[str, Any]:
        """Get audit events for an envelope.

        Args:
            envelope_id: The envelope ID

        Returns:
            Dictionary containing audit event list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.envelopes.list_audit_events(
                self.account_id, envelope_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get audit events for envelope {envelope_id}: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # TEMPLATE OPERATIONS
    # ========================================================================

    def list_templates(
        self,
        count: str = "100",
        start_position: str | None = None,   
        folder_ids: str | None = None,
        search_text: str | None = None,
    ) -> dict[str, Any]:
        """List templates in the account.

        Args:
            count: Maximum number of results (default: 100)
            folder_ids: Comma-separated list of folder IDs
            search_text: Search text to filter templates

        Returns:
            Dictionary containing template list and metadata

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            kwargs: dict[str, Any] = {"count": count}
            if folder_ids:
                kwargs["folder_ids"] = folder_ids
            if search_text:
                kwargs["search_text"] = search_text

            response = self.templates.list_templates(
                self.account_id, **kwargs
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list templates: {e}"
            raise DocuSignClientError(msg) from e

    def get_template(self, template_id: str) -> dict[str, Any]:
        """Get details of a specific template.

        Args:
            template_id: The template ID

        Returns:
            Dictionary containing template details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.templates.get(self.account_id, template_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get template {template_id}: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # ACCOUNT OPERATIONS
    # ========================================================================

    def get_account_information(self) -> dict[str, Any]:
        """Get account information and settings.

        Returns:
            Dictionary containing account details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.accounts.get_account_information(self.account_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get account information: {e}"
            raise DocuSignClientError(msg) from e

    def list_brands(self) -> dict[str, Any]:
        """List all brands in the account.

        Returns:
            Dictionary containing brand list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.accounts.list_brands(self.account_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list brands: {e}"
            raise DocuSignClientError(msg) from e

    def get_account_settings(self) -> dict[str, Any]:
        """Get account settings.

        Returns:
            Dictionary containing account settings

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.accounts.list_settings(self.account_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get account settings: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # USER OPERATIONS
    # ========================================================================

    def list_users(
        self,
        count: str = "100",
        start_position: str = "0",
    ) -> dict[str, Any]:
        """List users in the account.

        Args:
            count: Maximum number of results (default: 100)
            start_position: Starting position for pagination (default: 0)

        Returns:
            Dictionary containing user list and metadata

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.users.list(
                self.account_id,
                count=count,
                start_position=start_position,
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list users: {e}"
            raise DocuSignClientError(msg) from e

    def get_user(self, user_id: str) -> dict[str, Any]:
        """Get details of a specific user.

        Args:
            user_id: The user ID

        Returns:
            Dictionary containing user details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.users.get_information(self.account_id, user_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get user {user_id}: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # GROUP OPERATIONS
    # ========================================================================

    def list_groups(
        self,
        count: str = "100",
        start_position: str = "0",
    ) -> dict[str, Any]:
        """List groups in the account.

        Args:
            count: Maximum number of results (default: 100)
            start_position: Starting position for pagination

        Returns:
            Dictionary containing group list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.groups.list_groups(
                self.account_id,
                count=count,
                start_position=start_position,
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list groups: {e}"
            raise DocuSignClientError(msg) from e

    def get_group(self, group_id: str) -> dict[str, Any]:
        """Get details of a specific group using the direct REST endpoint."""

        try:
            # Direct REST endpoint
            path = f"/v2.1/accounts/{self.account_id}/groups/{group_id}"

            # Perform the call using ApiClient
            result = self.client.call_api(
                resource_path=path,
                method="GET",
                response_type="object",
            )

            # Normalize result: some SDK versions return a tuple, others return just data
            data = result[0] if isinstance(result, (list, tuple)) else result

            # Convert SDK model → dict if needed
            if hasattr(data, "to_dict"):
                return data.to_dict()
            if isinstance(data, dict):
                return data

            # Fallback conversion
            return dict(data)

        except ApiException as e:
            msg = f"Failed to get group {group_id}: {e}"
            raise DocuSignClientError(msg) from e


    # ========================================================================
    # FOLDER OPERATIONS
    # ========================================================================

    def list_folders(self) -> dict[str, Any]:
        """List all folders in the account.

        Returns:
            Dictionary containing folder list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.folders.list(self.account_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list folders: {e}"
            raise DocuSignClientError(msg) from e

    def list_folder_items(
        self,
        folder_id: str,
        start_position: str = "0",
    ) -> dict[str, Any]:
        """List items in a specific folder.

        Args:
            folder_id: The folder ID
            start_position: Starting position for pagination

        Returns:
            Dictionary containing folder items

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.folders.list_items(
                self.account_id,
                folder_id,
                start_position=start_position,
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list items in folder {folder_id}: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # WORKSPACE OPERATIONS
    # ========================================================================

    def list_workspaces(self) -> dict[str, Any]:
        """List all workspaces in the account.

        Returns:
            Dictionary containing workspace list

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.workspaces.list_workspaces(self.account_id)
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to list workspaces: {e}"
            raise DocuSignClientError(msg) from e

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        """Get details of a specific workspace.

        Args:
            workspace_id: The workspace ID

        Returns:
            Dictionary containing workspace details

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.workspaces.get_workspace(
                self.account_id, workspace_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get workspace {workspace_id}: {e}"
            raise DocuSignClientError(msg) from e

    # ========================================================================
    # BULK ENVELOPE OPERATIONS
    # ========================================================================

    def get_bulk_envelope_status(
        self,
        batch_id: str,
    ) -> dict[str, Any]:
        """Get status of a bulk envelope batch.

        Args:
            batch_id: The batch ID

        Returns:
            Dictionary containing batch status

        Raises:
            DocuSignClientError: If the API call fails
        """
        try:
            response = self.bulk_envelopes.get(
                self.account_id, batch_id
            )
            return response.to_dict()
        except ApiException as e:
            msg = f"Failed to get bulk envelope status for batch {batch_id}: {e}"
            raise DocuSignClientError(msg) from e


# ============================================================
# JWT Client
# ============================================================

class DocuSignRESTClientViaJWT:
    """DocuSign client via JWT authentication (server-to-server)."""

    def __init__(
        self,
        client_id: str,
        user_id: str,
        oauth_base_url: str = "https://account-d.docusign.com",
        base_path: str = "https://demo.docusign.net/restapi",
        expires_in: int = 3600,
        private_key_data: Optional[str] = None,
        private_key_file: Optional[str] = None,
    ) -> None:
        if not private_key_data and not private_key_file:
            raise ValueError("Either private_key_data or private_key_file must be provided")

        if private_key_data == "":
            raise ValueError("private_key_data cannot be an empty string")

        if "demo" in base_path:
            logging.warning("Using DocuSign demo environment. Switch to production before go-live.")

        self.client_id = client_id
        self.user_id = user_id
        self.oauth_base_url = oauth_base_url
        self.base_path = base_path
        self.expires_in = expires_in
        self.private_key_data = private_key_data
        self.private_key_file = private_key_file
        self.api_client: Optional[ApiClient] = None
        # Specialized SDK API instances
        self.accounts_api: Optional[AccountsApi] = None
        self.envelopes_api: Optional[EnvelopesApi] = None
        self.templates_api: Optional[TemplatesApi] = None
        self.users_api: Optional[UsersApi] = None
        self.groups_api: Optional[GroupsApi] = None
        self.bulk_envelopes_api: Optional[BulkEnvelopesApi] = None
        self.workspaces_api: Optional[WorkspacesApi] = None

    def create_client(self) -> ApiClient:  # type: ignore[valid-type]
        """Create DocuSign API client using JWT authentication."""
        try:
            self.api_client = ApiClient()
            self.api_client.set_base_path(self.base_path)

            if self.private_key_file:
                self.api_client.configure_jwt_authorization_flow(
                    private_key_file=self.private_key_file,
                    oauth_base_url=self.oauth_base_url,
                    client_id=self.client_id,
                    user_id=self.user_id,
                    expires_in=self.expires_in
                )
            else:
                self.api_client.configure_jwt_authorization_flow_bytes(
                    private_key_bytes=self.private_key_data.encode(),
                    oauth_base_url=self.oauth_base_url,
                    client_id=self.client_id,
                    user_id=self.user_id,
                    expires_in=self.expires_in
                )
            self._initialize_api_instances()
            return self.api_client
        except Exception as e:
            raise RuntimeError("Failed to create DocuSign JWT client") from e

    def _initialize_api_instances(self) -> None:
        """Initialize all specialized DocuSign SDK API instances."""
        if self.api_client is None:
            raise RuntimeError("API client must be created first")

        self.accounts_api = AccountsApi(self.api_client)
        self.envelopes_api = EnvelopesApi(self.api_client)
        self.templates_api = TemplatesApi(self.api_client)
        self.users_api = UsersApi(self.api_client)
        self.groups_api = GroupsApi(self.api_client)
        self.bulk_envelopes_api = BulkEnvelopesApi(self.api_client)
        self.workspaces_api = WorkspacesApi(self.api_client)

    def get_api_client(self) -> ApiClient:  # type: ignore[valid-type]
        if self.api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.api_client

    def get_base_path(self) -> str:
        return self.base_path

# ============================================================
# OAuth Client
# ============================================================

class DocuSignRESTClientViaOAuth:
    """DocuSign client via OAuth 2.0 (user-based applications)."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        oauth_base_url: str = "https://account-d.docusign.com",
        base_path: str = "https://demo.docusign.net/restapi",
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.oauth_base_url = oauth_base_url
        self.base_path = base_path
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.api_client: Optional[ApiClient] = None

    def get_authorization_url(self, scopes: Optional[list[str]] = None, state: Optional[str] = None) -> str:
        """Generate OAuth authorization URL."""
        if scopes is None:
            scopes = ["signature"]

        params = {
            "response_type": "code",
            "scope": " ".join(scopes),
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri
        }
        if state:
            params["state"] = state
        return f"{self.oauth_base_url}/oauth/auth?{urlencode(params)}"

    def create_client(self) -> ApiClient:  # type: ignore[valid-type]
        """Create DocuSign API client using OAuth authentication."""
        if not self.access_token:
            raise RuntimeError("Access token not available. Complete OAuth flow first.")

        try:
            self.api_client = ApiClient()
            self.api_client.set_base_path(self.base_path)
            self.api_client.set_oauth_token(self.access_token)
            self._initialize_api_instances()
            return self.api_client
        except Exception as e:
            raise RuntimeError("Failed to create DocuSign OAuth client") from e

    def _initialize_api_instances(self) -> None:
        """Initialize all specialized DocuSign SDK API instances."""
        if self.api_client is None:
            raise RuntimeError("API client must be created first")

        self.accounts_api = AccountsApi(self.api_client)
        self.envelopes_api = EnvelopesApi(self.api_client)
        self.templates_api = TemplatesApi(self.api_client)
        self.users_api = UsersApi(self.api_client)
        self.groups_api = GroupsApi(self.api_client)
        self.bulk_envelopes_api = BulkEnvelopesApi(self.api_client)
        self.workspaces_api = WorkspacesApi(self.api_client)

    def get_api_client(self) -> ApiClient:  # type: ignore[valid-type]
        if self.api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.api_client

    def get_base_path(self) -> str:
        return self.base_path

    async def _exchange_token(self, data: dict) -> DocuSignResponse:
        """Helper for exchanging tokens with DocuSign OAuth server."""
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        request = HTTPRequest(
            method="POST",
            url=f"{self.oauth_base_url}/oauth/token",
            headers=headers,
            body=data
        )
        http_client = HTTPClient(token="")
        response = await http_client.execute(request)

        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            return DocuSignResponse(success=False, error=f"{response.status}", message=await response.text())

        token_data = await response.json()
        self.access_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token", self.refresh_token)
        return DocuSignResponse(success=True, data=token_data)

    async def exchange_code_for_token(self, authorization_code: str) -> DocuSignResponse:
        """Exchange authorization code for access token."""
        data = {"grant_type": "authorization_code", "code": authorization_code, "redirect_uri": self.redirect_uri}
        return await self._exchange_token(data)

    async def refresh_access_token(self) -> DocuSignResponse:
        """Refresh OAuth access token using refresh token."""
        if not self.refresh_token:
            return DocuSignResponse(success=False, error="missing_refresh_token", message="Refresh token not available")
        data = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        return await self._exchange_token(data)

    async def check_for_access_token(self) -> None:
        """Checks if an access token is present."""
        if not self.access_token:
            raise RuntimeError("No access token available; call exchange_code_for_token first.")


# ============================================================
# Config Models
# ============================================================

class DocuSignJWTConfig(BaseModel):
    client_id: str
    user_id: str
    oauth_base_url: str = "https://account-d.docusign.com"
    base_path: str = "https://demo.docusign.net/restapi"
    expires_in: int = 3600
    private_key_data: Optional[str] = None
    private_key_file: Optional[str] = None

    def model_post_init(self, __context) -> None:
        if not self.private_key_data and not self.private_key_file:
            raise ValueError("Either private_key_data or private_key_file must be provided")

    def create_client(self) -> DocuSignRESTClientViaJWT:
        client = DocuSignRESTClientViaJWT(
            client_id=self.client_id,
            user_id=self.user_id,
            oauth_base_url=self.oauth_base_url,
            base_path=self.base_path,
            expires_in=self.expires_in,
            private_key_data=self.private_key_data,
            private_key_file=self.private_key_file
        )
        client.create_client()  # Initialize the API client and SDK APIs
        return client


class DocuSignOAuthConfig(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str
    oauth_base_url: str = "https://account-d.docusign.com"
    base_path: str = "https://demo.docusign.net/restapi"
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None

    def create_client(self) -> DocuSignRESTClientViaOAuth:
        client = DocuSignRESTClientViaOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=self.redirect_uri,
            oauth_base_url=self.oauth_base_url,
            base_path=self.base_path,
            access_token=self.access_token,
            refresh_token=self.refresh_token
        )
        if client.access_token:  # Only initialize if we have an access token
            client.create_client()
        return client

class DocuSignPATConfig(BaseModel):
    access_token: str
    base_path: str = "https://demo.docusign.net/restapi"

    def create_client(self) -> DocuSignRESTClientViaPAT:
        client = DocuSignRESTClientViaPAT(
            access_token=self.access_token,
            base_path=self.base_path
        )
        client.create_client()  # Initialize the API client and SDK APIs
        return client

# ============================================================
# Builder
# ============================================================

class DocuSignClient(IClient):
    """Builder class for DocuSign clients with multiple construction methods."""

    def __init__(self, client: Union[DocuSignRESTClientViaJWT, DocuSignRESTClientViaOAuth, DocuSignRESTClientViaPAT]) -> None:
        self.client = client

    def get_client(self) -> Union[DocuSignRESTClientViaJWT, DocuSignRESTClientViaOAuth, DocuSignRESTClientViaPAT]:
        return self.client

    def get_api_client(self) -> ApiClient:  # type: ignore[valid-type]
        return self.client.get_api_client()

    def get_base_path(self) -> str:
        return self.client.get_base_path()

    @classmethod
    def build_with_config(cls, config: Union[DocuSignJWTConfig, DocuSignOAuthConfig, DocuSignPATConfig]) -> "DocuSignClient":
        client = config.create_client()
        return cls(client=client)

    @classmethod
    def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        graph_db_service: IGraphService,
    ) -> "DocuSignClient":
        logger.warning("DocuSignClient.build_from_services not yet implemented")
        raise NotImplementedError("Implement build_from_services with actual services")