import base64
import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

import httpx   # <-- NEW: raw client for OAuth token

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

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return json.dumps(self.to_dict())


# ======================================================================
# SERVER-TO-SERVER OAUTH CLIENT
# ======================================================================

class ZoomRESTClientViaServerToServer(HTTPClient):
    """
    Zoom REST client using Server-to-Server OAuth.
    Handles all token requests and token rotation.
    """

    def __init__(
        self,
        account_id: str,
        client_id: str,
        client_secret: str,
        base_url: str = "https://api.zoom.us/v2",
    ) -> None:

        # Call parent with empty token — will be filled after OAuth
        super().__init__(token="", token_type="Bearer")

        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token: Optional[str] = None

        self.headers.update({
            "Content-Type": "application/json"
        })

    # -------------------------------------------------------------

    def get_base_url(self) -> str:
        return self.base_url

    # -------------------------------------------------------------
    # FIXED TOKEN REQUEST (uses httpx correctly)
    # -------------------------------------------------------------

    async def _get_access_token(self) -> str:
        """
        Fetch access token using Zoom Server-to-Server OAuth.
        This is the corrected working version.
        """

        # reuse if already fetched
        if self.access_token:
            return self.access_token

        # Prepare Basic Auth header
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

        # ---- FIX: Use httpx.AsyncClient, NOT your internal HTTPClient ----
        async with httpx.AsyncClient() as client:
            resp = await client.post(token_url, headers=headers)
            data = resp.json()

        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"Failed to fetch Zoom access token: {data}")

        # store token
        self.access_token = token
        self.headers["Authorization"] = f"Bearer {token}"

        return token

    # -------------------------------------------------------------

    async def ensure_authenticated(self) -> None:
        """Ensure OAuth token is present before request()."""
        if not self.access_token:
            await self._get_access_token()

    # -------------------------------------------------------------
    # REQUIRED BY DATASOURCE → Unified request()
    # -------------------------------------------------------------

    async def request(self, method: str, url: str, params=None, body=None, timeout=None):
        """
        The datasource always calls this method.
        It must convert high-level args into HTTPRequest and call execute().
        """

        await self.ensure_authenticated()

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
# STATIC TOKEN OAUTH CLIENT
# ======================================================================

class ZoomRESTClientViaToken(HTTPClient):
    """
    Zoom REST client using a pre-generated OAuth token.
    """

    def __init__(self, base_url: str, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.base_url = base_url.rstrip("/")

        self.headers.update({
            "Content-Type": "application/json"
        })

    def get_base_url(self) -> str:
        return self.base_url

    # For token auth, request() simply wraps execute()
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
    ssl: bool = True

    def create_client(self):
        return ZoomRESTClientViaServerToServer(
            self.account_id,
            self.client_id,
            self.client_secret,
            self.base_url,
        )

    def to_dict(self):
        return asdict(self)


@dataclass
class ZoomTokenConfig:
    base_url: str
    token: str
    ssl: bool = True

    def create_client(self):
        return ZoomRESTClientViaToken(self.base_url, self.token)

    def to_dict(self):
        return asdict(self)


# ======================================================================
# TOP-LEVEL CLIENT WRAPPER
# ======================================================================

class ZoomClient(IClient):
    """Unified builder wrapper used by PipesHub."""

    def __init__(self, client: ZoomRESTClientViaServerToServer | ZoomRESTClientViaToken) -> None:
        self.client = client

    def get_client(self):
        return self.client

    @classmethod
    def build_with_config(cls, config: ZoomServerToServerConfig | ZoomTokenConfig) -> "ZoomClient":
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
    ) -> "ZoomClient":

        try:
            conf = await cls._get_connector_config(logger, config_service)
            if not conf:
                raise ValueError("Failed to load Zoom connector configuration")

            auth = conf.get("auth", {})

            auth_type = auth.get("authType", "TOKEN")
            base_url = auth.get("baseUrl", "https://api.zoom.us/v2")

            if auth_type == "SERVER_TO_SERVER":
                account_id = auth.get("accountId")
                client_id = auth.get("clientId")
                client_secret = auth.get("clientSecret")
                if not (account_id and client_id and client_secret):
                    raise ValueError("Missing S2S credentials")

                c = ZoomRESTClientViaServerToServer(
                    account_id=account_id,
                    client_id=client_id,
                    client_secret=client_secret,
                    base_url=base_url,
                )

            else:  # TOKEN
                token = auth.get("token")
                if not token:
                    raise ValueError("Missing Zoom OAuth token")
                c = ZoomRESTClientViaToken(base_url, token)

            return cls(c)

        except Exception as e:
            logger.error(f"Failed to build Zoom client from services: {e}")
            raise

    @staticmethod
    async def _get_connector_config(logger, config_service):
        try:
            return await config_service.get_config("/services/connectors/zoom/config") or {}
        except Exception as e:
            logger.error(f"Failed to get Zoom connector config: {e}")
            return {}
