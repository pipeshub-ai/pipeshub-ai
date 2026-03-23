"""
Minimal HTTP client for talking to the Pipeshub connectors API on test.pipeshub.com.

Supports:
  - OAuth2 client-credentials authentication
  - Connector CRUD (create, list, get, toggle, delete)
  - Polling helpers for sync completion
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("pipeshub-client")


class PipeshubClientError(Exception):
    """Base error for Pipeshub client issues."""


class PipeshubAuthError(PipeshubClientError):
    """Authentication/authorization failure when calling Pipeshub APIs."""


@dataclass
class ConnectorInstance:
    connector_id: str
    connector_type: str
    instance_name: str
    scope: str


class PipeshubClient:
    """Minimal HTTP client for talking to the Pipeshub connectors API on test.pipeshub.com."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.base_url = (base_url or os.getenv("PIPESHUB_BASE_URL") or "").rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._access_token: Optional[str] = None

        if not self.base_url:
            raise PipeshubClientError(
                "PIPESHUB_BASE_URL must be set in integration-tests/.env to talk to test.pipeshub.com"
            )

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    def _ensure_access_token(self) -> None:
        """
        Fetch an access token using client_credentials if we don't already have one.

        Uses CLIENT_ID and CLIENT_SECRET from integration-tests/.env against:
            POST /api/v1/oauth2/token
        """
        if self._access_token:
            return

        client_id = os.getenv("CLIENT_ID")
        client_secret = os.getenv("CLIENT_SECRET")
        if not client_id or not client_secret:
            raise PipeshubClientError(
                "CLIENT_ID and CLIENT_SECRET must be set in integration-tests/.env "
                "to fetch an access token for test.pipeshub.com"
            )

        token_url = f"{self.base_url}/api/v1/oauth2/token"
        resp = requests.post(
            token_url,
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=self.timeout_seconds,
        )
        data = self._handle_response(resp)
        access_token = data.get("access_token")
        if not access_token:
            raise PipeshubAuthError(
                "OAuth token response did not include access_token"
            )

        self._access_token = str(access_token)

    def _headers(self, is_admin: bool = True) -> Dict[str, str]:
        self._ensure_access_token()
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        if is_admin:
            headers["X-Is-Admin"] = "true"
        return headers

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    def _handle_response(self, resp: requests.Response) -> Any:
        if resp.status_code >= 400:
            p = urlparse(resp.url or "")
            loc = (
                f"{p.scheme}://{p.netloc}{p.path or '/'}"
                if p.netloc
                else (resp.url or "")
            )
            msg = f"HTTP {resp.status_code} {loc}"
            if resp.status_code == 401:
                raise PipeshubAuthError(msg)
            raise PipeshubClientError(msg)
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # --------------------------------------------------------------------- #
    # Public API - Connector CRUD
    # --------------------------------------------------------------------- #
    def create_connector(
        self,
        connector_type: str,
        instance_name: str,
        *,
        scope: str = "personal",
        config: Optional[Dict[str, Any]] = None,
        is_admin: bool = True,
    ) -> ConnectorInstance:
        """Create a connector instance via /api/v1/connectors/."""
        payload: Dict[str, Any] = {
            "connectorType": connector_type,
            "instanceName": instance_name,
            "scope": scope,
        }
        if config:
            payload["config"] = config

        resp = requests.post(
            self._url("/api/v1/connectors/"),
            headers=self._headers(is_admin=is_admin),
            json=payload,
            timeout=self.timeout_seconds,
        )
        data = self._handle_response(resp)
        if not data.get("success"):
            raise PipeshubClientError("Failed to create connector (API returned success=false)")

        connector = data.get("connector") or {}
        return ConnectorInstance(
            connector_id=connector.get("connectorId") or connector.get("_key"),
            connector_type=connector.get("connectorType") or connector_type,
            instance_name=connector.get("instanceName") or instance_name,
            scope=connector.get("scope") or scope,
        )

    def list_connectors(
        self,
        *,
        scope: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """List configured connector instances via GET /api/v1/connectors/."""
        params: Dict[str, Any] = {"page": page, "limit": limit}
        if scope:
            params["scope"] = scope
        if search:
            params["search"] = search

        resp = requests.get(
            self._url("/api/v1/connectors/"),
            headers=self._headers(),
            params=params,
            timeout=self.timeout_seconds,
        )
        return self._handle_response(resp)

    def get_connector(self, connector_id: str) -> Dict[str, Any]:
        """Fetch a connector instance document."""
        resp = requests.get(
            self._url(f"/api/v1/connectors/{connector_id}"),
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        data = self._handle_response(resp)
        if not data.get("success"):
            raise PipeshubClientError(
                f"Failed to fetch connector {connector_id} (API returned success=false)"
            )
        return data.get("connector") or {}

    def toggle_sync(self, connector_id: str, enable: bool = True) -> Dict[str, Any]:
        """
        Toggle sync on or off for the connector.

        When enabling, the server internally calls init() + test_connection_and_access()
        before publishing a sync event.
        """
        connector = self.get_connector(connector_id)
        current = bool(connector.get("isActive"))
        if current == enable:
            # Force toggle: disable then re-enable
            if enable:
                logger.info("Connector already active, toggling off then on to force re-sync")
                self._do_toggle(connector_id)
                time.sleep(2)
                return self._do_toggle(connector_id)
            else:
                return {"success": True, "message": "Already in desired state"}

        return self._do_toggle(connector_id)

    def _do_toggle(self, connector_id: str) -> Dict[str, Any]:
        resp = requests.post(
            self._url(f"/api/v1/connectors/{connector_id}/toggle"),
            headers=self._headers(),
            json={"type": "sync"},
            timeout=self.timeout_seconds,
        )
        return self._handle_response(resp)

    def delete_connector(self, connector_id: str) -> Dict[str, Any]:
        """Delete a connector instance and all associated data."""
        resp = requests.delete(
            self._url(f"/api/v1/connectors/{connector_id}"),
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        return self._handle_response(resp)

    # --------------------------------------------------------------------- #
    # Public API - Sync helpers
    # --------------------------------------------------------------------- #
    def wait(self, seconds: float) -> None:
        """Simple blocking wait helper for tests."""
        time.sleep(seconds)

    def wait_for_sync(
        self,
        connector_id: str,
        check_fn: Callable[[], bool],
        timeout: int = 120,
        poll_interval: int = 5,
        description: str = "sync",
    ) -> None:
        """
        Poll until *check_fn* returns True, or timeout.

        Args:
            connector_id: For logging purposes.
            check_fn: Callable that returns True when sync is complete.
            timeout: Maximum seconds to wait.
            poll_interval: Seconds between polls.
            description: Label for log messages.
        """
        deadline = time.time() + timeout
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            if check_fn():
                logger.info(
                    "✅ %s complete for connector %s (attempt %d)",
                    description, connector_id, attempt,
                )
                return
            logger.info(
                "⏳ Waiting for %s on connector %s (attempt %d, %.0fs remaining)...",
                description, connector_id, attempt, deadline - time.time(),
            )
            time.sleep(poll_interval)

        raise TimeoutError(
            f"Timed out waiting for {description} on connector {connector_id} "
            f"after {timeout}s"
        )

    def get_connector_status(self, connector_id: str) -> Dict[str, Any]:
        """Get connector status including isActive and sync timestamps."""
        return self.get_connector(connector_id)
