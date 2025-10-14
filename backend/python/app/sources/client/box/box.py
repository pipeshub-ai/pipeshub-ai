import base64
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union

from app.sources.client.http.http_request import HTTPRequest

try:
    from box_sdk_gen import BoxClient as BoxSDKClient  # type: ignore
    from box_sdk_gen import BoxDeveloperTokenAuth, BoxJWTAuth, BoxOAuth  # type: ignore
except ImportError:
    raise ImportError("box_sdk_gen is not installed. Please install it with `pip install box-sdk-gen`")

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


@dataclass
class BoxResponse:
    """Standardized Box API response wrapper."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class BoxRESTClientViaToken:
    """Box client via Developer Token or OAuth2 access token."""
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.box_client = None

    async def create_client(self) -> BoxSDKClient:  # type: ignore[valid-type]
        """Create Box client using Developer Token or OAuth2 token."""
        auth = BoxDeveloperTokenAuth(token=self.access_token)
        self.box_client = BoxSDKClient(auth=auth)
        return self.box_client

    def get_box_client(self) -> BoxSDKClient:  # type: ignore[valid-type]
        if self.box_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.box_client


class BoxRESTClientWithJWT:
    """
    Box client via JWT authentication (recommended for enterprise apps).

    Args:
        client_id: Box app client ID
        client_secret: Box app client secret
        enterprise_id: Box enterprise ID (or user_id for user apps)
        jwt_key_id: JWT key ID
        rsa_private_key_data: RSA private key data
        rsa_private_key_passphrase: Optional passphrase for private key
    """
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        enterprise_id: str,
        jwt_key_id: str,
        rsa_private_key_data: str,
        rsa_private_key_passphrase: Optional[str] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.enterprise_id = enterprise_id
        self.jwt_key_id = jwt_key_id
        self.rsa_private_key_data = rsa_private_key_data
        self.rsa_private_key_passphrase = rsa_private_key_passphrase
        self.box_client = None

    async def create_client(self) -> BoxSDKClient:  # type: ignore[valid-type]
        """Create Box client using JWT authentication."""
        auth = BoxJWTAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            enterprise_id=self.enterprise_id,
            jwt_key_id=self.jwt_key_id,
            rsa_private_key_data=self.rsa_private_key_data,
            rsa_private_key_passphrase=self.rsa_private_key_passphrase,
        )
        self.box_client = BoxSDKClient(auth=auth)
        return self.box_client

    def get_box_client(self) -> BoxSDKClient:  # type: ignore[valid-type]
        if self.box_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.box_client


class BoxRESTClientWithOAuth2:
    """
    Box client via OAuth2 (for user apps).

    Args:
        client_id: Box app client ID
        client_secret: Box app client secret
        access_token: OAuth2 access token
        refresh_token: Optional refresh token
    """
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: Optional[str] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.box_client = None

    async def create_client(self) -> BoxSDKClient:  # type: ignore[valid-type]
        """Create Box client using OAuth2."""
        auth = BoxOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            access_token=self.access_token,
            refresh_token=self.refresh_token,
        )
        self.box_client = BoxSDKClient(auth=auth)
        return self.box_client

    def get_box_client(self) -> BoxSDKClient:  # type: ignore[valid-type]
        if self.box_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.box_client


class BoxRESTClientWithOAuthCode:
    """
    Box client via OAuth2 authorization code flow.
    This client exchanges an authorization code for an access token and then
    creates a Box SDK client using that token.

    Args:
        client_id: Box app client ID
        client_secret: Box app client secret
        code: OAuth2 authorization code
        redirect_uri: Optional redirect URI used in authorization flow
    """
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: Optional[str] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.code = code
        self.redirect_uri = redirect_uri
        self.access_token = None
        self.refresh_token = None
        self.box_client = None

    async def create_client(self) -> BoxSDKClient:  # type: ignore[valid-type]
        """Create Box client by exchanging authorization code for access token."""
        if self.access_token is None:
            await self._fetch_token()

        auth = BoxOAuth(
            client_id=self.client_id,
            client_secret=self.client_secret,
            access_token=self.access_token,
            refresh_token=self.refresh_token,
        )
        self.box_client = BoxSDKClient(auth=auth)
        return self.box_client

    def get_box_client(self) -> BoxSDKClient:  # type: ignore[valid-type]
        if self.box_client is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self.box_client

    async def _fetch_token(self) -> None:
        """Exchange authorization code for access token."""
        credentials = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()

        body = {
            "grant_type": "authorization_code",
            "code": self.code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        if self.redirect_uri:
            body["redirect_uri"] = self.redirect_uri

        request = HTTPRequest(
            method="POST",
            url="https://api.box.com/oauth2/token",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {credentials}"
            },
            body=body,
        )

        http_client = HTTPClient(token="")
        response = await http_client.execute(request)
        token_data = response.json()
        self.access_token = token_data["access_token"]
        self.refresh_token = token_data.get("refresh_token")


@dataclass
class BoxTokenConfig:
    """
    Configuration for Box client via Developer Token or OAuth2 access token.

    Args:
        token: Developer token or OAuth2 access token
        base_url: Present for API parity; not used by Box SDK
        ssl: Unused; kept for interface parity
    """
    token: str
    base_url: str = "https://api.box.com"   # not used by SDK, for parity only
    ssl: bool = True

    async def create_client(self) -> BoxRESTClientViaToken:
        """Create a Box client."""
        return BoxRESTClientViaToken(self.token)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BoxJWTConfig:
    """
    Configuration for Box client via JWT authentication.

    Args:
        client_id: Box app client ID
        client_secret: Box app client secret
        enterprise_id: Box enterprise ID (or user_id for user apps)
        jwt_key_id: JWT key ID
        rsa_private_key_data: RSA private key data
        rsa_private_key_passphrase: Optional passphrase for private key
        base_url: Present for parity; not used by Box SDK
        ssl: Unused; kept for interface parity
    """
    client_id: str
    client_secret: str
    enterprise_id: str
    jwt_key_id: str
    rsa_private_key_data: str
    rsa_private_key_passphrase: Optional[str] = None
    base_url: str = "https://api.box.com"   # not used by SDK
    ssl: bool = True

    async def create_client(self) -> BoxRESTClientWithJWT:
        """Create a Box client."""
        return BoxRESTClientWithJWT(
            client_id=self.client_id,
            client_secret=self.client_secret,
            enterprise_id=self.enterprise_id,
            jwt_key_id=self.jwt_key_id,
            rsa_private_key_data=self.rsa_private_key_data,
            rsa_private_key_passphrase=self.rsa_private_key_passphrase,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BoxOAuth2Config:
    """
    Configuration for Box client via OAuth2.

    Args:
        client_id: Box app client ID
        client_secret: Box app client secret
        access_token: OAuth2 access token
        refresh_token: Optional refresh token
        base_url: Present for parity; not used by Box SDK
        ssl: Unused; kept for interface parity
    """
    client_id: str
    client_secret: str
    access_token: str
    refresh_token: Optional[str] = None
    base_url: str = "https://api.box.com"   # not used by SDK
    ssl: bool = True

    async def create_client(self) -> BoxRESTClientWithOAuth2:
        """Create a Box client."""
        return BoxRESTClientWithOAuth2(
            client_id=self.client_id,
            client_secret=self.client_secret,
            access_token=self.access_token,
            refresh_token=self.refresh_token,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BoxOAuthCodeConfig:
    """
    Configuration for Box client via OAuth2 authorization code flow.

    Args:
        client_id: Box app client ID
        client_secret: Box app client secret
        code: OAuth2 authorization code
        redirect_uri: Optional redirect URI used in authorization flow
        base_url: Present for parity; not used by Box SDK
        ssl: Unused; kept for interface parity
    """
    client_id: str
    client_secret: str
    code: str
    redirect_uri: Optional[str] = None
    base_url: str = "https://api.box.com"   # not used by SDK
    ssl: bool = True

    async def create_client(self) -> BoxRESTClientWithOAuthCode:
        """Create a Box client."""
        return BoxRESTClientWithOAuthCode(
            client_id=self.client_id,
            client_secret=self.client_secret,
            code=self.code,
            redirect_uri=self.redirect_uri,
        )

    def to_dict(self) -> dict:
        return asdict(self)


class BoxClient(IClient):
    """
    Builder class for Box clients with multiple construction methods.

    Mirrors your SlackClient/DropboxClient shape so it can be swapped in existing wiring.
    """

    def __init__(
        self,
        client: Union[BoxRESTClientViaToken, BoxRESTClientWithJWT, BoxRESTClientWithOAuth2, BoxRESTClientWithOAuthCode],
        ) -> None:
        self.client = client

    def get_client(self) -> Union[BoxRESTClientViaToken, BoxRESTClientWithJWT, BoxRESTClientWithOAuth2, BoxRESTClientWithOAuthCode]:
        """Return the underlying auth-holder client object (call `.create_client()` to get SDK)."""
        return self.client

    @classmethod
    async def build_with_config(
        cls,
        config: Union[BoxTokenConfig, BoxJWTConfig, BoxOAuth2Config, BoxOAuthCodeConfig],
    ) -> "BoxClient":
        """Build BoxClient using one of the config dataclasses."""
        client = await config.create_client()
        return cls(client=client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        arango_service: IGraphService,
    ) -> "BoxClient":
        """
        Build BoxClient using your configuration service & org/user context.
        """
        try:
            # Get Box configuration from config service
            config_data = await cls._get_connector_config(config_service, "box")

            # Extract configuration parameters
            auth_type = config_data.get("authType", "OAUTH")
            auth_config = config_data.get("auth", {})

            if auth_type == "API_TOKEN":
                access_token = auth_config.get("access_token")
                if not access_token:
                    raise ValueError("access_token is required for token auth_type")

                config = BoxTokenConfig(token=access_token)
                return await cls.build_with_config(config)

            elif auth_type == "JWT":
                client_id = auth_config.get("clientId")
                client_secret = auth_config.get("clientSecret")
                enterprise_id = auth_config.get("enterpriseId")
                jwt_key_id = auth_config.get("jwtKeyId")
                rsa_private_key_data = auth_config.get("rsaPrivateKeyData")
                rsa_private_key_passphrase = auth_config.get("rsaPrivateKeyPassphrase")

                if not all([client_id, client_secret, enterprise_id, jwt_key_id, rsa_private_key_data]):
                    raise ValueError("client_id, client_secret, enterprise_id, jwt_key_id, and rsa_private_key_data are required for jwt auth_type")

                config = BoxJWTConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                    enterprise_id=enterprise_id,
                    jwt_key_id=jwt_key_id,
                    rsa_private_key_data=rsa_private_key_data,
                    rsa_private_key_passphrase=rsa_private_key_passphrase
                )
                return await cls.build_with_config(config)

            elif auth_type == "OAUTH":
                credentials_config = auth_config.get("credentials", {})
                client_id = auth_config.get("clientId")
                client_secret = auth_config.get("clientSecret")
                access_token = credentials_config.get("access_token")
                refresh_token = credentials_config.get("refresh_token")

                if not all([client_id, client_secret, access_token]):
                    raise ValueError("client_id, client_secret, and access_token are required for oauth2 auth_type")

                config = BoxOAuth2Config(
                    client_id=client_id,
                    client_secret=client_secret,
                    access_token=access_token,
                    refresh_token=refresh_token
                )
                return await cls.build_with_config(config)

            elif auth_type == "OAUTH_CODE":
                client_id = auth_config.get("clientId")
                client_secret = auth_config.get("clientSecret")
                code = credentials_config.get("code")
                redirect_uri = auth_config.get("redirectUri")

                if not all([client_id, client_secret, code]):
                    raise ValueError("client_id, client_secret, and code are required for oauth_code auth_type")

                config = BoxOAuthCodeConfig(
                    client_id=client_id,
                    client_secret=client_secret,
                    code=code,
                    redirect_uri=redirect_uri
                )
                return await cls.build_with_config(config)

            else:
                raise ValueError(f"Unsupported auth_type: {auth_type}")

        except Exception as e:
            logger.error(f"Failed to build Box client from services: {e}")
            raise ValueError(f"Failed to build Box client: {str(e)}")

    @staticmethod
    async def _get_connector_config(config_service: ConfigurationService, connector_name: str) -> Dict[str, Any]:
        """Get connector configuration from config service"""
        try:
            config_path = f"/services/connectors/{connector_name}/config"
            config_data = await config_service.get_config(config_path)
            return config_data
        except Exception as e:
            raise ValueError(f"Failed to get {connector_name} configuration: {str(e)}")
