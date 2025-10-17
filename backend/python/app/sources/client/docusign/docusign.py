import base64
import logging
import time
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode

from pydantic import BaseModel  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient

try:
    from docusign_esign import ApiClient  # type: ignore
except ImportError:
    raise ImportError("docusign_esign is not installed. Install with `pip install docusign_esign`")


class DocuSignResponse(BaseModel):
    """Standardized DocuSign API response wrapper."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    def to_json(self) -> str:
        return self.model_dump_json()

# ============================================================
# PAT Client
# ============================================================

class DocuSignRESTClientViaPAT:
    """DocuSign client via PAT authentication (server-to-server)."""

    def __init__(self, access_token: str, base_path: str = "https://demo.docusign.net/restapi") -> None:
        self.access_token = access_token
        self.base_path = base_path
        self.api_client: Optional[ApiClient] = None

    def create_client(self) -> ApiClient:  # type: ignore[valid-type]
        """Create DocuSign API client using PAT authentication."""
        self.api_client = ApiClient(host=self.base_path)
        self.api_client.set_default_header("Authorization", f"Bearer {self.access_token}")
        return self.api_client

    def get_api_client(self) -> ApiClient:  # type: ignore[valid-type]
        if self.api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.api_client

    def get_base_path(self) -> str:
        return self.base_path

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

    def create_client(self) -> ApiClient:  # type: ignore[valid-type]
        """Create DocuSign API client using JWT authentication."""
        try:
            import time

            import jwt
            import requests

            # Read private key
            if self.private_key_file:
                with open(self.private_key_file, 'r') as f:
                    private_key = f.read()
            else:
                private_key = self.private_key_data

            # Create JWT payload
            now = int(time.time())
            payload = {
                "iss": self.client_id,  # Integration Key (Client ID)
                "sub": self.user_id,     # User ID (Impersonated user)
                "aud": self.oauth_base_url,  # OAuth server
                "iat": now,         # Issued at
                "exp": now + self.expires_in,  # Expires
                "scope": "signature impersonation"
            }

            # Generate JWT
            assertion = jwt.encode(payload, private_key, algorithm='RS256')

            # Exchange JWT for access token
            token_url = f"{self.oauth_base_url}/oauth/token"
            response = requests.post(
                token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded"
                }
            )

            HTTP_OK = 200
            if response.status_code != HTTP_OK:
                raise RuntimeError(f"JWT token exchange failed: HTTP {response.status_code} - {response.text}")

            token_data = response.json()
            access_token = token_data.get("access_token")

            # Create API client with the access token
            self.api_client = ApiClient(host=self.base_path)
            self.api_client.set_default_header("Authorization", f"Bearer {access_token}")

            return self.api_client
        except Exception as e:
            raise RuntimeError("Failed to create DocuSign JWT client") from e

    def get_api_client(self) -> ApiClient:  # type: ignore[valid-type]
        if self.api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.api_client

    def get_base_path(self) -> str:
        return self.base_path

    def get_access_token(self) -> Optional[str]:
        """Get the current access token."""
        if self.api_client is None:
            return None
        return self.api_client.get_default_header().get("Authorization", "").replace("Bearer ", "")

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
        self.token_expiry: Optional[float] = None  # Unix timestamp when token expires
        self.token_buffer_seconds: int = 300  # Refresh 5 minutes before expiry
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
            self.api_client = ApiClient(host=self.base_path)
            self.api_client.set_default_header("Authorization", f"Bearer {self.access_token}")
            return self.api_client
        except Exception as e:
            raise RuntimeError("Failed to create DocuSign OAuth client") from e

    def get_api_client(self) -> ApiClient:  # type: ignore[valid-type]
        if self.api_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        if not hasattr(self, "_http_client") or self._http_client is None:
            self._http_client = HTTPClient(token=self.access_token)
        else:
            self._http_client.headers["Authorization"] = f"Bearer {self.access_token}"
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

        token_data = response.json()
        self.access_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token", self.refresh_token)

        # Track token expiry
        expires_in = token_data.get("expires_in")
        if expires_in:
            self.token_expiry = time.time() + int(expires_in)

        return DocuSignResponse(success=True, data=token_data)
        self.access_token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token", self.refresh_token)
        return DocuSignResponse(success=True, data=token_data)

    async def exchange_code_for_token(self, authorization_code: str) -> DocuSignResponse:
        """Exchange authorization code for access token."""
        data = {"grant_type": "authorization_code", "code": authorization_code, "redirect_uri": self.redirect_uri}
        return await self._exchange_token(data)

    async def refresh_access_token(self) -> DocuSignResponse:
        """Refresh the access token using the refresh token."""
        if not self.refresh_token:
            return DocuSignResponse(success=False, error="missing_refresh_token", message="Refresh token not available")
        data = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}
        return await self._exchange_token(data)

    async def ensure_valid_token(self) -> None:
        """Ensure the client has a valid token, refresh if needed."""
        if not self.access_token:
            raise RuntimeError("No access token available; call exchange_code_for_token first.")

        # Check if token is expired or close to expiring
        if self.token_expiry is not None:
            time_until_expiry = self.token_expiry - time.time()
            # Refresh if less than 5 minutes remaining
            TOKEN_REFRESH_BUFFER_SECONDS = 300  # 5 minutes
            if time_until_expiry <= TOKEN_REFRESH_BUFFER_SECONDS:
                if not self.refresh_token:
                    raise RuntimeError("Token expired and no refresh token available")

                refresh_result = await self.refresh_access_token()
                if not refresh_result.success:
                    raise RuntimeError(f"Failed to refresh token: {refresh_result.error}")

                # Update API client with new token
                if self.api_client is not None:
                    self.api_client.set_default_header("Authorization", f"Bearer {self.access_token}")


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
    ssl: bool = True  # unused

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
        client.create_client()  # Initialize the API client
        return client


class DocuSignOAuthConfig(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str
    oauth_base_url: str = "https://account-d.docusign.com"
    base_path: str = "https://demo.docusign.net/restapi"
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    ssl: bool = True  # unused

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
        # Only initialize if access_token is available
        if self.access_token:
            client.create_client()  # Initialize the API client
        return client

class DocuSignPATConfig(BaseModel):
    access_token: str
    base_path: str = "https://demo.docusign.net/restapi"
    ssl: bool = True  # unused

    def create_client(self) -> DocuSignRESTClientViaPAT:
        client = DocuSignRESTClientViaPAT(
            access_token=self.access_token,
            base_path=self.base_path
        )
        client.create_client()  # Initialize the API client
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


# Export public API
__all__ = [
    "DocuSignClient",
    "DocuSignPATConfig",
    "DocuSignJWTConfig",
    "DocuSignOAuthConfig",
    "DocuSignRESTClientViaPAT",
    "DocuSignRESTClientViaJWT",
    "DocuSignRESTClientViaOAuth",
    "DocuSignResponse",
]
