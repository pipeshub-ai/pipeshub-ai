import base64
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Union

from backend.python.app.config.configuration_service import ConfigurationService
from backend.python.app.sources.client.http.http_client import HTTPClient
from backend.python.app.sources.client.http.http_request import HTTPRequest
from backend.python.app.sources.client.iclient import IClient

# ======================================================================
# Standard Zoom Response Wrapper
# ======================================================================

@dataclass
class ZoomResponse:
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# ======================================================================
# SERVER-TO-SERVER OAUTH CLIENT
# ======================================================================

class ZoomRESTClientViaServerToServer(HTTPClient):
    """
    Zoom REST client using Server-to-Server OAuth.
    Token must be fetched explicitly via _get_access_token().
    """

    def __init__(
        self,
        account_id: str,
        client_id: str,
        client_secret: str,
        base_url: str = "https://api.zoom.us/v2",
    ) -> None:
        super().__init__(token="", token_type="Bearer")

        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token: Optional[str] = None

        self.headers.update({"Content-Type": "application/json"})

    def get_base_url(self) -> str:
        return self.base_url

    async def _get_access_token(self) -> str:
        """
        Explicitly fetch access token using HTTPClient.execute().
        """
        if self.access_token:
            return self.access_token

        cred = f"{self.client_id}:{self.client_secret}"
        encoded = base64.b64encode(cred.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        token_url = (
            f"https://zoom.us/oauth/token"
            f"?grant_type=account_credentials&account_id={self.account_id}"
        )

        req = HTTPRequest(
            method="POST",
            url=token_url,
            headers=headers,
            query_params=None,
            body=None,
            timeout=30,
        )

        resp = await self.execute(req)

        try:
            data = resp.json() if hasattr(resp, "json") else resp.response.json()
        except Exception as e:
            raise RuntimeError(f"Failed to parse token response: {e}")

        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"Failed to fetch Zoom access token: {data}")

        self.access_token = token
        self.headers["Authorization"] = f"Bearer {token}"
        return token

    async def request(self, method: str, url: str, params=None, body=None, timeout=None):
        req = HTTPRequest(
            method=method,
            url=url,
            headers=self.headers,
            query_params=params or {},
            body=body,
            timeout=timeout,
        )
        return await self.execute(req)


# ======================================================================
# STATIC TOKEN CLIENT
# ======================================================================

class ZoomRESTClientViaToken(HTTPClient):
    """
    Zoom REST client using a pre-generated OAuth token.
    """

    def __init__(self, base_url: str, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.base_url = base_url.rstrip("/")
        self.headers.update({"Content-Type": "application/json"})

    def get_base_url(self) -> str:
        return self.base_url

    async def request(self, method: str, url: str, params=None, body=None, timeout=None):
        req = HTTPRequest(
            method=method,
            url=url,
            headers=self.headers,
            query_params=params or {},
            body=body,
            timeout=timeout,
        )
        return await self.execute(req)


# ======================================================================
# AUTHORIZATION CODE OAUTH CLIENT
# ======================================================================

class ZoomRESTClientViaAuthorizationCode(HTTPClient):
    """
    OAuth Authorization Code client.
    Token helper. Must be called explicitly by setup/example code.
    Not invoked automatically during API requests.

    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        base_url: str = "https://api.zoom.us/v2",
    ):
        super().__init__(token="", token_type="Bearer")

        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.base_url = base_url.rstrip("/")

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at: Optional[float] = None

        self.headers.update({"Content-Type": "application/json"})

    def get_base_url(self) -> str:
        return self.base_url

    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        cred = f"{self.client_id}:{self.client_secret}"
        encoded = base64.b64encode(cred.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        body = f"grant_type=authorization_code&code={code}&redirect_uri={self.redirect_uri}"

        req = HTTPRequest(
            method="POST",
            url="https://zoom.us/oauth/token",
            headers=headers,
            query_params=None,
            body=body,
            timeout=30,
        )

        resp = await self.execute(req)
        data = resp.json() if hasattr(resp, "json") else resp.response.json()

        access = data.get("access_token")
        if not access:
            raise RuntimeError(f"Failed to exchange code for token: {data}")

        self.access_token = access
        self.refresh_token = data.get("refresh_token")

        expires_in = data.get("expires_in")
        if expires_in:
            self.expires_at = time.time() + int(expires_in) - 10

        self.headers["Authorization"] = f"Bearer {access}"
        return data

    async def refresh_access_token(self) -> Dict[str, Any]:
        if not self.refresh_token:
            raise RuntimeError("No refresh token available")

        cred = f"{self.client_id}:{self.client_secret}"
        encoded = base64.b64encode(cred.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        body = f"grant_type=refresh_token&refresh_token={self.refresh_token}"

        req = HTTPRequest(
            method="POST",
            url="https://zoom.us/oauth/token",
            headers=headers,
            query_params=None,
            body=body,
            timeout=30,
        )

        resp = await self.execute(req)
        data = resp.json() if hasattr(resp, "json") else resp.response.json()

        access = data.get("access_token")
        if not access:
            raise RuntimeError(f"Failed to refresh token: {data}")

        self.access_token = access
        self.refresh_token = data.get("refresh_token", self.refresh_token)

        expires_in = data.get("expires_in")
        if expires_in:
            self.expires_at = time.time() + int(expires_in) - 10

        self.headers["Authorization"] = f"Bearer {access}"
        return data

    async def request(self, method: str, url: str, params=None, body=None, timeout=None):
        req = HTTPRequest(
            method=method,
            url=url,
            headers=self.headers,
            query_params=params or {},
            body=body,
            timeout=timeout,
        )
        return await self.execute(req)


# ======================================================================
# CONFIG CLASSES
# ======================================================================

@dataclass
class ZoomServerToServerConfig:
    account_id: str
    client_id: str
    client_secret: str
    base_url: str = "https://api.zoom.us/v2"

    def create_client(self):
        return ZoomRESTClientViaServerToServer(
            self.account_id,
            self.client_id,
            self.client_secret,
            self.base_url,
        )


@dataclass
class ZoomTokenConfig:
    base_url: str
    token: str

    def create_client(self):
        return ZoomRESTClientViaToken(self.base_url, self.token)


@dataclass
class ZoomOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str = "http://localhost:8080/callback"
    base_url: str = "https://api.zoom.us/v2"

    def create_client(self):
        return ZoomRESTClientViaAuthorizationCode(
            self.client_id,
            self.client_secret,
            self.redirect_uri,
            self.base_url,
        )


# ======================================================================
# TOP-LEVEL CLIENT WRAPPER
# ======================================================================

ClientType = Union[
    ZoomRESTClientViaServerToServer,
    ZoomRESTClientViaToken,
    ZoomRESTClientViaAuthorizationCode,
]


class ZoomClient(IClient):
    def __init__(self, client: ClientType) -> None:
        self.client = client

    def get_client(self):
        return self.client

    @classmethod
    def build_with_config(cls, config):
        return cls(config.create_client())

    @classmethod
    async def build_from_services(cls, logger: logging.Logger, config_service: ConfigurationService):
        conf = await config_service.get_config("/services/connectors/zoom/config") or {}
        auth = conf.get("auth", {})
        base_url = auth.get("baseUrl", "https://api.zoom.us/v2")

        auth_type = auth.get("authType", "TOKEN")

        if auth_type == "SERVER_TO_SERVER":
            return cls(
                ZoomRESTClientViaServerToServer(
                    auth["accountId"],
                    auth["clientId"],
                    auth["clientSecret"],
                    base_url,
                )
            )

        if auth_type in ("OAUTH", "AUTHORIZATION_CODE", "GENERAL_OAUTH"):
            return cls(
                ZoomRESTClientViaAuthorizationCode(
                    auth["clientId"],
                    auth["clientSecret"],
                    auth.get("redirectUri", "http://localhost:8080/callback"),
                    base_url,
                )
            )

        return cls(ZoomRESTClientViaToken(base_url, auth["token"]))
