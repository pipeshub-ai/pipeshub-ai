# backend/python/app/sources/client/zoom/zoom.py
# ruff: noqa

import logging
import base64
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Union

from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.iclient import IClient
from app.config.configuration_service import ConfigurationService

LOG = logging.getLogger(__name__)


# =====================================================================
# Zoom Response Wrapper
# =====================================================================
@dataclass
class ZoomResponse:
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# =====================================================================
# Config DataClasses
# =====================================================================
@dataclass
class ZoomTokenConfig:
    base_url: str
    token: str

    def to_dict(self):
        return asdict(self)


@dataclass
class ZoomAppKeySecretConfig:
    client_id: str
    client_secret: str
    account_id: str
    base_url: str = "https://api.zoom.us/v2"
    ssl: bool = True
    timeout: Optional[float] = None

    async def create_client(self, is_team: bool = False):
        token, expires_in = await self._fetch_token()
        return ZoomRESTClientViaToken(
            base_url=self.base_url,
            token=token,
            token_expires_in=expires_in,
            timeout=self.timeout
        )

    async def _fetch_token(self) -> (str, int):
        creds = f"{self.client_id}:{self.client_secret}"
        encoded = base64.b64encode(creds.encode()).decode()

        url = "https://zoom.us/oauth/token"
        form_body = {
            "grant_type": "account_credentials",
            "account_id": self.account_id,
        }

        req = HTTPRequest(
            method="POST",
            url=url,
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=form_body,
            query_params={},
            path_params={},
        )

        http_client = HTTPClient(token="")
        resp: HTTPResponse = await http_client.execute(req)

        data = resp.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))

        if not token:
            LOG.error("Failed to fetch Zoom token: %s", data)
            raise RuntimeError("Zoom token fetch failed")

        return token, expires_in

    def to_dict(self):
        return asdict(self)


# =====================================================================
# REST Client with Auto Refresh
# =====================================================================
class ZoomRESTClientViaToken:
    def __init__(
        self,
        base_url: str,
        token: str,
        token_expires_in: Optional[int] = None,
        timeout: Optional[float] = None,
        logger: Optional[logging.Logger] = None
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.token_expires_in = token_expires_in or 3600
        self.timeout = timeout
        self.logger = logger or LOG

        self.http_client = HTTPClient(
            token=self.token,
            token_type="Bearer",
            timeout=self.timeout
        )

        self._client_id = None
        self._client_secret = None
        self._account_id = None

    def inject_credentials(self, client_id, client_secret, account_id):
        self._client_id = client_id
        self._client_secret = client_secret
        self._account_id = account_id

    async def _fetch_new_token(self):
        if not all([self._client_id, self._client_secret, self._account_id]):
            raise RuntimeError("Client credentials not injected; cannot refresh token")

        creds = f"{self._client_id}:{self._client_secret}"
        encoded = base64.b64encode(creds.encode()).decode()

        url = "https://zoom.us/oauth/token"
        form_body = {
            "grant_type": "account_credentials",
            "account_id": self._account_id,
        }

        req = HTTPRequest(
            method="POST",
            url=url,
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            body=form_body,
            query_params={},
            path_params={},
        )

        http_client = HTTPClient(token="")
        resp: HTTPResponse = await http_client.execute(req)

        data = resp.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))

        if not token:
            raise RuntimeError(f"Failed to refresh Zoom token: {data}")

        self.token = token
        self.token_expires_in = expires_in
        self.http_client = HTTPClient(
            token=self.token,
            token_type="Bearer",
            timeout=self.timeout
        )

        self.logger.info("Zoom token refreshed successfully (expires_in=%s)", expires_in)

    async def request(self, method, endpoint, params=None, body=None) -> ZoomResponse:
        url = f"{self.base_url}{endpoint}"

        req = HTTPRequest(
            method=method,
            url=url,
            headers={},
            query_params=params or {},
            body=body or {},
            path_params={},
        )

        try:
            resp: HTTPResponse = await self.http_client.execute(req)

            status = getattr(resp.response, "status_code", None) or getattr(resp, "status_code", None)

            if status == 204 or (hasattr(resp, "content") and not getattr(resp, "content")):
                return ZoomResponse(success=True, data={"status": "no_content"})

            if status == 401:
                self.logger.info("401 received; refreshing Zoom token...")
                try:
                    await self._fetch_new_token()
                except Exception as e:
                    self.logger.exception("Token refresh failed: %s", e)
                    return ZoomResponse(success=False, error=str(e))

                resp = await self.http_client.execute(req)

            return ZoomResponse(success=True, data=resp.json())

        except Exception as e:
            self.logger.exception("Zoom request failed: %s %s", method, endpoint)
            return ZoomResponse(success=False, error=str(e))


# =====================================================================
# Zoom Client (Builder)
# =====================================================================
class ZoomClient(IClient):
    def __init__(self, client):
        self.client = client

    def get_client(self):
        return self.client

    @classmethod
    async def build_with_config(cls, config):
        if isinstance(config, ZoomAppKeySecretConfig):
            rest_client = await config.create_client()
            rest_client.inject_credentials(config.client_id, config.client_secret, config.account_id)
            return cls(client=rest_client)

        elif isinstance(config, ZoomTokenConfig):
            rest_client = ZoomRESTClientViaToken(base_url=config.base_url, token=config.token)
            return cls(client=rest_client)

        raise ValueError("Unsupported config type for ZoomClient")

    @classmethod
    async def build_from_services(cls, logger, config_service):
        cfg = await config_service.get_config("/services/connectors/zoom/config")
        if not cfg:
            raise ValueError("Zoom configuration not found in ConfigurationService")

        auth_type = cfg.get("authType", "APP_KEY_SECRET")
        auth = cfg.get("auth", {})

        if auth_type == "APP_KEY_SECRET":
            client_id = auth.get("clientId") or auth.get("client_id")
            client_secret = auth.get("clientSecret") or auth.get("client_secret")
            account_id = auth.get("accountId") or auth.get("account_id")
            base_url = auth.get("baseUrl", "https://api.zoom.us/v2")
            timeout = auth.get("timeout")

            if not all([client_id, client_secret, account_id]):
                raise ValueError("clientId, clientSecret, accountId are required")

            cfg_obj = ZoomAppKeySecretConfig(
                client_id=client_id,
                client_secret=client_secret,
                account_id=account_id,
                base_url=base_url,
                timeout=timeout,
            )

            rest_client = await cfg_obj.create_client()
            rest_client.inject_credentials(client_id, client_secret, account_id)

            return cls(client=rest_client)

        elif auth_type == "TOKEN":
            token = auth.get("token") or auth.get("access_token")
            base_url = auth.get("baseUrl", "https://api.zoom.us/v2")

            if not token:
                raise ValueError("token required for TOKEN authType")

            return cls(client=ZoomRESTClientViaToken(base_url=base_url, token=token))

        raise ValueError(f"Unsupported authType: {auth_type}")
