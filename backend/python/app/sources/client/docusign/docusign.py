"""DocuSign API Client using official Python SDK.

This module provides a comprehensive wrapper around the official DocuSign
eSignature Python SDK for integration with the PipesHub platform.
"""

import base64
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode

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
from pydantic import BaseModel

from app.config.configuration_service import ConfigurationService
from app.config.constants.http_status_code import HttpStatusCode
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient


class DocuSignClientError(Exception):
    """Base exception for DocuSign client errors."""


@dataclass
class DocuSignResponse:
    """Standardized response object for DocuSign operations."""

    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    message: Optional[str] = None


class DocuSignBaseClient:
    """Base class ensuring HTTP execution capabilities for all auth types."""

    def __init__(self, base_path: str) -> None:
        self.base_path = base_path
        # Initialize internal HTTP client for raw async calls (used by DataSource)
        self._http_client = HTTPClient(token="")

    def get_base_path(self) -> str:
        return self.base_path

    @property
    def headers(self) -> Dict[str, str]:
        """Must be implemented by subclasses to return Auth headers."""
        raise NotImplementedError()

    async def execute(self, request: HTTPRequest) -> Any:
        """Executes a raw HTTP request using the internal HTTPClient.

        This is required for the manual async methods in DocuSignDataSource.
        """
        if not request.headers:
            request.headers = {}

        # Inject the authorization headers from the specific auth strategy
        request.headers.update(self.headers)

        return await self._http_client.execute(request)

    def get_api_client(self) -> ApiClient:
        raise NotImplementedError()


class DocuSignRESTClientViaJWT(DocuSignBaseClient):
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
        super().__init__(base_path)
        if not private_key_data and not private_key_file:
            raise ValueError(
                "Either private_key_data or private_key_file must be provided"
            )

        if private_key_data == "":
            raise ValueError("private_key_data cannot be an empty string")

        if "demo" in base_path:
            logging.warning(
                "Using DocuSign demo environment. Switch to production before go-live."
            )

        self.client_id = client_id
        self.user_id = user_id
        self.oauth_base_url = oauth_base_url
        self.expires_in = expires_in
        self.private_key_data = private_key_data
        self.private_key_file = private_key_file
        self.api_client: Optional[ApiClient] = None

    def create_client(self) -> ApiClient:
        """Create DocuSign API client using JWT authentication."""
        try:
            # Initialize with no args to avoid compatibility issues
            self.api_client = ApiClient()
            
            # Set host/base_path manually
            if hasattr(self.api_client, "host"):
                self.api_client.host = self.base_path
            else:
                self.api_client.set_base_path(self.base_path)

            if self.private_key_file:
                self.api_client.configure_jwt_authorization_flow(
                    private_key_file=self.private_key_file,
                    oauth_base_url=self.oauth_base_url,
                    client_id=self.client_id,
                    user_id=self.user_id,
                    expires_in=self.expires_in,
                )
            else:
                self.api_client.configure_jwt_authorization_flow_bytes(
                    private_key_bytes=self.private_key_data.encode(),
                    oauth_base_url=self.oauth_base_url,
                    client_id=self.client_id,
                    user_id=self.user_id,
                    expires_in=self.expires_in,
                )
            return self.api_client
        except Exception as e:
            raise RuntimeError("Failed to create DocuSign JWT client") from e

    @property
    def headers(self) -> Dict[str, str]:
        # For JWT, token retrieval from SDK structure varies by version.
        # Best effort attempt to get token for raw HTTP calls:
        if self.api_client:
            # Try accessing via configuration object if it exists
            if hasattr(self.api_client, "configuration") and self.api_client.configuration:
                token = getattr(self.api_client.configuration, "access_token", None)
                if token:
                    return {"Authorization": f"Bearer {token}"}
            # Some versions might store it elsewhere, but this covers most.
        return {}

    def get_api_client(self) -> ApiClient:
        if self.api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.api_client


class DocuSignRESTClientViaOAuth(DocuSignBaseClient):
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
        super().__init__(base_path)
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.oauth_base_url = oauth_base_url
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.api_client: Optional[ApiClient] = None

    def get_authorization_url(
        self, scopes: Optional[list[str]] = None, state: Optional[str] = None
    ) -> str:
        if scopes is None:
            scopes = ["signature"]
        params = {
            "response_type": "code",
            "scope": " ".join(scopes),
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
        }
        if state:
            params["state"] = state
        return f"{self.oauth_base_url}/oauth/auth?{urlencode(params)}"

    def create_client(self) -> ApiClient:
        if not self.access_token:
            raise RuntimeError("Access token not available. Complete OAuth flow first.")
        try:
            # --- FIX: Vanilla initialization + Manual setters ---
            self.api_client = ApiClient()
            
            # Handle base path (some SDKs use 'host', some use 'set_base_path')
            if hasattr(self.api_client, "host"):
                self.api_client.host = self.base_path
            else:
                self.api_client.set_base_path(self.base_path)

            # Set header
            self.api_client.set_default_header(
                "Authorization", f"Bearer {self.access_token}"
            )
            # ----------------------------------------------------
            
            return self.api_client
        except Exception as e:
            raise RuntimeError("Failed to create DocuSign OAuth client") from e

    @property
    def headers(self) -> Dict[str, str]:
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    def get_api_client(self) -> ApiClient:
        if self.api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.api_client

    async def _exchange_token(self, data: dict) -> DocuSignResponse:
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        request = HTTPRequest(
            method="POST",
            url=f"{self.oauth_base_url}/oauth/token",
            headers=headers,
            body=data,
        )
        response = await self._http_client.execute(request)

        if response.status >= HttpStatusCode.BAD_REQUEST.value:
            return DocuSignResponse(
                success=False, error=f"{response.status}", message=await response.text()
            )

        token_data = await response.json()
        self.access_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token", self.refresh_token)
        return DocuSignResponse(success=True, data=token_data)

    async def exchange_code_for_token(
        self, authorization_code: str
    ) -> DocuSignResponse:
        data = {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": self.redirect_uri,
        }
        return await self._exchange_token(data)

    async def refresh_access_token(self) -> DocuSignResponse:
        if not self.refresh_token:
            return DocuSignResponse(
                success=False,
                error="missing_refresh_token",
                message="Refresh token not available",
            )
        data = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        return await self._exchange_token(data)

    async def check_for_access_token(self) -> None:
        if not self.access_token:
            raise RuntimeError(
                "No access token available; call exchange_code_for_token first."
            )


class DocuSignRESTClientViaPAT(DocuSignBaseClient):
    """DocuSign client via Personal Access Token."""

    def __init__(
        self,
        access_token: str,
        base_path: str = "https://demo.docusign.net/restapi",
    ) -> None:
        super().__init__(base_path)
        self.access_token = access_token
        self.api_client: Optional[ApiClient] = None

    def create_client(self) -> ApiClient:
        try:
            # --- FIX: Vanilla initialization + Manual setters ---
            self.api_client = ApiClient()
            
            if hasattr(self.api_client, "host"):
                self.api_client.host = self.base_path
            else:
                self.api_client.set_base_path(self.base_path)
                
            self.api_client.set_default_header(
                "Authorization", f"Bearer {self.access_token}"
            )
            # ----------------------------------------------------
            
            return self.api_client
        except Exception as e:
            raise RuntimeError("Failed to create DocuSign PAT client") from e

    @property
    def headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def get_api_client(self) -> ApiClient:
        if self.api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.api_client


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

    def model_post_init(self, __context: Any) -> None:
        if not self.private_key_data and not self.private_key_file:
            raise ValueError(
                "Either private_key_data or private_key_file must be provided"
            )

    def create_client(self) -> DocuSignRESTClientViaJWT:
        client = DocuSignRESTClientViaJWT(
            client_id=self.client_id,
            user_id=self.user_id,
            oauth_base_url=self.oauth_base_url,
            base_path=self.base_path,
            expires_in=self.expires_in,
            private_key_data=self.private_key_data,
            private_key_file=self.private_key_file,
        )
        client.create_client()
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
            refresh_token=self.refresh_token,
        )
        if client.access_token:
            client.create_client()
        return client


class DocuSignPATConfig(BaseModel):
    access_token: str
    base_path: str = "https://demo.docusign.net/restapi"

    def create_client(self) -> DocuSignRESTClientViaPAT:
        client = DocuSignRESTClientViaPAT(
            access_token=self.access_token, base_path=self.base_path
        )
        client.create_client()
        return client


# ============================================================
# Unified Builder / Client
# ============================================================


class DocuSignClient(IClient):
    """Comprehensive client for interacting with DocuSign eSignature API.

    Acts as both a Builder for Auth strategies and a Wrapper for SDK operations.
    """

    def __init__(
        self,
        client: Union[
            DocuSignRESTClientViaJWT,
            DocuSignRESTClientViaOAuth,
            DocuSignRESTClientViaPAT,
        ],
    ) -> None:
        self.client = client
        self.account_id: Optional[str] = None

        # Initialize all API instances if client is ready
        if self.client.api_client:
            self._initialize_apis(self.client.api_client)

    def _initialize_apis(self, api_client: ApiClient) -> None:
        self.accounts = AccountsApi(api_client)
        self.envelopes = EnvelopesApi(api_client)
        self.templates = TemplatesApi(api_client)
        self.users = UsersApi(api_client)
        self.folders = FoldersApi(api_client)
        self.groups = GroupsApi(api_client)
        self.bulk_envelopes = BulkEnvelopesApi(api_client)
        self.workspaces = WorkspacesApi(api_client)

    def set_account_id(self, account_id: str) -> None:
        self.account_id = account_id

    def get_client(
        self,
    ) -> Union[
        DocuSignRESTClientViaJWT, DocuSignRESTClientViaOAuth, DocuSignRESTClientViaPAT
    ]:
        return self.client

    def get_api_client(self) -> ApiClient:
        return self.client.get_api_client()

    def get_base_path(self) -> str:
        return self.client.get_base_path()

    @classmethod
    def build_with_config(
        cls, config: Union[DocuSignJWTConfig, DocuSignOAuthConfig, DocuSignPATConfig]
    ) -> "DocuSignClient":
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

    # ========================================================================
    # SDK Wrapper Methods (Synchronous)
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
        if not self.account_id:
            raise ValueError("Account ID not set")
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
            response = self.envelopes.list_status_changes(self.account_id, **kwargs)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to list envelopes: {e}") from e

    def get_envelope(self, envelope_id: str) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.envelopes.get_envelope(self.account_id, envelope_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(
                f"Failed to get envelope {envelope_id}: {e}"
            ) from e

    def get_envelope_documents(self, envelope_id: str) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.envelopes.list_documents(self.account_id, envelope_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(
                f"Failed to get documents for envelope {envelope_id}: {e}"
            ) from e

    def download_document(self, envelope_id: str, document_id: str) -> bytes:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            return self.envelopes.get_document(
                self.account_id, document_id, envelope_id
            )
        except ApiException as e:
            raise DocuSignClientError(
                f"Failed to download document {document_id}: {e}"
            ) from e

    def list_recipients(self, envelope_id: str) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.envelopes.list_recipients(self.account_id, envelope_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to list recipients: {e}") from e

    def get_envelope_audit_events(self, envelope_id: str) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.envelopes.list_audit_events(self.account_id, envelope_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to get audit events: {e}") from e

    def list_templates(
        self,
        count: str = "100",
        folder_ids: str | None = None,
        search_text: str | None = None,
        start_position: str | None = None,
    ) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            kwargs: dict[str, Any] = {"count": count}
            if folder_ids:
                kwargs["folder_ids"] = folder_ids
            if search_text:
                kwargs["search_text"] = search_text
            response = self.templates.list_templates(self.account_id, **kwargs)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to list templates: {e}") from e

    def get_template(self, template_id: str) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.templates.get(self.account_id, template_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to get template: {e}") from e

    def get_account_information(self) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.accounts.get_account_information(self.account_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to get account information: {e}") from e

    def list_brands(self) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.accounts.list_brands(self.account_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to list brands: {e}") from e

    def get_account_settings(self) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.accounts.list_settings(self.account_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to get account settings: {e}") from e

    def list_users(
        self, count: str = "100", start_position: str = "0"
    ) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.users.list(
                self.account_id, count=count, start_position=start_position
            )
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to list users: {e}") from e

    def get_user(self, user_id: str) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.users.get_information(self.account_id, user_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to get user: {e}") from e

    def list_groups(
        self, count: str = "100", start_position: str = "0"
    ) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.groups.list_groups(
                self.account_id, count=count, start_position=start_position
            )
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to list groups: {e}") from e

    def get_group(self, group_id: str) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            path = f"/v2.1/accounts/{self.account_id}/groups/{group_id}"
            result = (
                self.client.get_api_client().call_api(
                    path, "GET", response_type="object"
                )
            )
            data = result[0] if isinstance(result, (list, tuple)) else result
            if hasattr(data, "to_dict"):
                return data.to_dict()
            return dict(data)
        except ApiException as e:
            raise DocuSignClientError(f"Failed to get group: {e}") from e

    def list_folders(self) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.folders.list(self.account_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to list folders: {e}") from e

    def list_folder_items(
        self, folder_id: str, start_position: str = "0"
    ) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.folders.list_items(
                self.account_id, folder_id, start_position=start_position
            )
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to list folder items: {e}") from e

    def list_workspaces(self) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.workspaces.list_workspaces(self.account_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to list workspaces: {e}") from e

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.workspaces.get_workspace(self.account_id, workspace_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(f"Failed to get workspace: {e}") from e

    def get_bulk_envelope_status(self, batch_id: str) -> dict[str, Any]:
        if not self.account_id:
            raise ValueError("Account ID not set")
        try:
            response = self.bulk_envelopes.get(self.account_id, batch_id)
            return response.to_dict()
        except ApiException as e:
            raise DocuSignClientError(
                f"Failed to get bulk envelope status: {e}"
            ) from e