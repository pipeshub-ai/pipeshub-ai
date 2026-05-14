"""
Minimal HTTP client for talking to the Pipeshub connectors API on test.pipeshub.com.

Supports:
  - OAuth2 client-credentials authentication
  - Connector CRUD (create, list, get, toggle, delete)
  - Polling helpers for sync completion
"""

import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("pipeshub-client")

# Refresh this many seconds before the token expires (client_credentials + typical 1h JWT).
_TOKEN_REFRESH_SKEW_SEC = 120


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
        self._token_claims: Optional[Dict[str, Any]] = None
        # Unix time (seconds): refresh before this moment (from expires_in / JWT exp / default).
        self._token_expires_at: float = 0.0

        if not self.base_url:
            raise PipeshubClientError(
                "PIPESHUB_BASE_URL must be set in integration-tests/.env to talk to test.pipeshub.com"
            )

    def _use_connector_direct_api(self) -> bool:
        """True when ``base_url`` targets the Python connector app (8088), not the Node gateway."""
        explicit = os.getenv("PIPESHUB_USE_CONNECTOR_API", "").strip().lower()
        if explicit in ("1", "true", "yes"):
            return True
        if explicit in ("0", "false", "no"):
            return False
        try:
            if urlparse(self.base_url).port == 8088:
                return True
        except Exception:
            pass
        return False

    def _reindex_record_path(self, record_id: str) -> str:
        if self._use_connector_direct_api():
            return f"/api/v1/records/{record_id}/reindex"
        return f"/api/v1/knowledgeBase/reindex/record/{record_id}"

    def _reindex_record_group_path(self, record_group_id: str) -> str:
        if self._use_connector_direct_api():
            return f"/api/v1/record-groups/{record_group_id}/reindex"
        return f"/api/v1/knowledgeBase/reindex/record-group/{record_group_id}"

    def _stream_record_path(self, record_id: str) -> str:
        if self._use_connector_direct_api():
            return f"/api/v1/stream/record/{record_id}"
        return f"/api/v1/knowledgeBase/stream/record/{record_id}"

    # --------------------------------------------------------------------- #
    # JWT claim helpers — mirrors the backend's extractOrgId/extractUserId
    # which read orgId/userId straight off the request's token payload.
    # --------------------------------------------------------------------- #
    def _decode_jwt_claims(self, token: str) -> Dict[str, Any]:
        """Decode the payload
        """
        try:
            _, payload_b64, _ = token.split(".", 2)
        except ValueError as exc:
            raise PipeshubClientError("Access token is not a valid JWT") from exc
        # base64url with missing padding is legal in JWTs.
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        try:
            payload_bytes = base64.urlsafe_b64decode(padded)
            return json.loads(payload_bytes)
        except (ValueError, json.JSONDecodeError) as exc:
            raise PipeshubClientError("Failed to decode JWT payload") from exc

    def _claims(self) -> Dict[str, Any]:
        """Return (and memoise) the decoded claims of the current access token."""
        self._ensure_access_token()
        if self._token_claims is None:
            assert self._access_token is not None  # for type-checkers
            self._token_claims = self._decode_jwt_claims(self._access_token)
        return self._token_claims

    @property
    def auth_headers(self) -> Dict[str, str]:
        """Authorization + Content-Type headers using a fresh, valid access token.

        Refreshes the token if it has expired so callers making raw ``requests``
        calls (instead of going through ``_request_json``) still get a working
        bearer header.
        """
        return self._headers()

    @property
    def org_id(self) -> str:
        """orgId claim from the authenticated access token.

        Matches the backend's ``extractOrgId`` which reads ``orgId`` off the
        request's decoded token payload.
        """
        claims = self._claims()
        org_id = claims.get("orgId")
        if not org_id:
            raise PipeshubClientError(
                "orgId claim not found in access token payload"
            )
        return str(org_id)

    @property
    def user_id(self) -> Optional[str]:
        """userId claim from the authenticated access token, if present."""
        claims = self._claims()
        user_id = claims.get("userId")
        return str(user_id) if user_id else None

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    def _invalidate_access_token(self) -> None:
        self._access_token = None
        self._token_claims = None
        self._token_expires_at = 0.0

    def _set_token_expiry_from_token_response(self, data: Dict[str, Any]) -> None:
        """Set _token_expires_at from OAuth expires_in, else JWT exp, else a safe default."""
        now = time.time()
        candidate = now + 3300.0
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)) and float(expires_in) > _TOKEN_REFRESH_SKEW_SEC:
            candidate = now + float(expires_in) - _TOKEN_REFRESH_SKEW_SEC
        elif self._access_token:
            try:
                claims = self._decode_jwt_claims(self._access_token)
                exp = claims.get("exp")
                if isinstance(exp, (int, float)):
                    candidate = float(exp) - _TOKEN_REFRESH_SKEW_SEC
            except Exception:
                logger.warning(
                    "Could not decode JWT exp for token expiry; using default TTL",
                    exc_info=True,
                )
        self._token_expires_at = max(now + 60.0, candidate)

    def _fetch_access_token(self) -> None:
        """Always request a new token (caller clears old state first)."""
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
        if resp.status_code >= 400:
            msg = f"HTTP {resp.status_code} {token_url}"
            try:
                msg += f" - {resp.json()}"
            except Exception:
                if resp.text:
                    msg += f" - {resp.text[:200]}"
            raise PipeshubAuthError(msg)
        try:
            data = resp.json()
        except ValueError as exc:
            raise PipeshubAuthError("Token endpoint did not return JSON") from exc

        access_token = data.get("access_token")
        if not access_token:
            raise PipeshubAuthError(
                "OAuth token response did not include access_token"
            )

        self._access_token = str(access_token)
        self._token_claims = None
        self._set_token_expiry_from_token_response(data)

    def _ensure_access_token(self) -> None:
        """
        Ensure a valid access token: reuse until near expiry, then client_credentials again.
        """
        if self._access_token and time.time() < self._token_expires_at:
            return
        self._invalidate_access_token()
        self._fetch_access_token()

    def _headers(self, is_admin: bool = True) -> Dict[str, str]:
        self._ensure_access_token()
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        return headers

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        is_admin: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Authorized JSON API request; refresh on expiry, retry once on 401."""
        url = self._url(path)
        kwargs.setdefault("timeout", self.timeout_seconds)
        last_resp: Optional[requests.Response] = None
        for attempt in range(2):
            self._ensure_access_token()
            headers = self._headers(is_admin=is_admin)
            kwargs_with_headers = {**kwargs, "headers": headers}
            last_resp = requests.request(method, url, **kwargs_with_headers)
            if last_resp.status_code == 401 and attempt == 0:
                logger.warning("Pipeshub API returned 401; invalidating token and retrying once")
                self._invalidate_access_token()
                continue
            return self._handle_response(last_resp)
        assert last_resp is not None
        return self._handle_response(last_resp)

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
            
            # Try to add response body details for debugging
            try:
                error_data = resp.json()
                if error_data:
                    msg += f" - {error_data}"
            except Exception:
                if resp.text:
                    msg += f" - {resp.text[:200]}"
            
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
        auth_type: str | None = None,
    ) -> ConnectorInstance:
        """Create a connector instance via /api/v1/connectors/."""
        payload: Dict[str, Any] = {
            "connectorType": connector_type,
            "instanceName": instance_name,
            "scope": scope,
        }
        if config:
            payload["config"] = config
        if auth_type:
            payload["authType"] = auth_type

        data = self._request_json(
            "POST",
            "/api/v1/connectors/",
            is_admin=is_admin,
            json=payload,
        )
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

        return self._request_json(
            "GET",
            "/api/v1/connectors/",
            params=params,
        )

    def get_connector(self, connector_id: str) -> Dict[str, Any]:
        """Fetch a connector instance document."""
        data = self._request_json(
            "GET",
            f"/api/v1/connectors/{connector_id}",
        )
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
        return self._request_json(
            "POST",
            f"/api/v1/connectors/{connector_id}/toggle",
            json={"type": "sync"},
        )

    def delete_connector(self, connector_id: str) -> Dict[str, Any]:
        """Delete a connector instance and all associated data."""
        return self._request_json(
            "DELETE",
            f"/api/v1/connectors/{connector_id}",
        )

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
    
    # --------------------------------------------------------------------- #
    # Public API - Reindex
    # --------------------------------------------------------------------- #
    def reindex_record(self, record_id: str) -> Dict[str, Any]:
        """
        Trigger reindex for a specific record.
        
        Args:
            record_id: Internal record ID (_key)
            
        Returns:
            API response with success status
        """
        return self._request_json(
            "POST",
            self._reindex_record_path(record_id),
        )
    
    def reindex_record_group(self, record_group_id: str, depth: int = 0) -> Dict[str, Any]:
        """
        Trigger reindex for a record group and its children.
        
        Args:
            record_group_id: Internal record group ID (_key)
            depth: How many levels of children to reindex (0 = group only)
            
        Returns:
            API response with success status
        """
        return self._request_json(
            "POST",
            self._reindex_record_group_path(record_group_id),
            json={"depth": depth},
        )
    
    # --------------------------------------------------------------------- #
    # Public API - Config Management
    # --------------------------------------------------------------------- #
    def update_connector_config(
        self, connector_id: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update connector configuration including filters.
        
        Args:
            connector_id: Connector ID
            config: Configuration object with filters, auth, etc.
            
        Returns:
            Updated connector document
        """
        return self._request_json(
            "PUT",
            f"/api/v1/connectors/{connector_id}/config",
            json=config,
        )
    
    def update_connector_filters_sync_config(
        self, connector_id: str, filters: Optional[Dict[str, Any]] = None, 
        sync: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Update connector filters and sync configuration using the filters-sync endpoint.
        
        Note: Backend requires connector to be disabled before updating filters/sync.
        Use update_connector_filters_sync_safe() for automatic disable/enable handling.
        
        Args:
            connector_id: Connector ID
            filters: Filter configuration (optional)
            sync: Sync configuration (optional)
            
        Returns:
            API response with syncFiltersChanged flag
        """
        if not filters and not sync:
            raise PipeshubClientError("Either filters or sync configuration must be provided")
        
        payload: Dict[str, Any] = {}
        if filters:
            payload["filters"] = filters
        if sync:
            payload["sync"] = sync
        
        return self._request_json(
            "PUT",
            f"/api/v1/connectors/{connector_id}/config/filters-sync",
            json=payload,
        )
    
    def update_connector_filters_sync_safe(
        self, connector_id: str, filters: Optional[Dict[str, Any]] = None,
        sync: Optional[Dict[str, Any]] = None, wait_before_enable: int = 3
    ) -> Dict[str, Any]:
        """
        Safely update connector filters and sync configuration.
        
        This method handles the backend requirement that connectors must be disabled
        before updating filters/sync configuration. It:
        1. Checks if connector is currently active
        2. Disables connector if active
        3. Updates filters and/or sync configuration
        4. Re-enables connector if it was originally active
        
        This pattern mirrors the frontend's approach and ensures integration tests
        can update filters/sync without violating backend constraints.
        
        Args:
            connector_id: Connector ID
            filters: Filter configuration (optional)
            sync: Sync configuration (optional)
            wait_before_enable: Seconds to wait before re-enabling (default 3)
            
        Returns:
            API response from the filters-sync update call
            
        Example:
            # Update space filter for Confluence connector
            client.update_connector_filters_sync_safe(
                connector_id,
                filters={
                    "space_keys": {
                        "operator": "IN",
                        "values": ["MYSPACE"]
                    }
                }
            )
        """
        # Get current connector status
        connector = self.get_connector(connector_id)
        was_active = bool(connector.get("isActive"))
        
        # Disable if active
        if was_active:
            logger.info(f"Connector {connector_id} is active, disabling before config update")
            self.toggle_sync(connector_id, enable=False)
            self.wait(wait_before_enable)
        
        # Update filters and sync configuration
        logger.info(f"Updating filters/sync config for connector {connector_id}")
        response = self.update_connector_filters_sync_config(
            connector_id, filters=filters, sync=sync
        )
        
        # Re-enable if it was originally active
        if was_active:
            logger.info(f"Re-enabling connector {connector_id}")
            self.wait(wait_before_enable)
            self.toggle_sync(connector_id, enable=True)
        
        return response
    
    # --------------------------------------------------------------------- #
    # Public API - Stream Content
    # --------------------------------------------------------------------- #
    def stream_record(self, record_id: str) -> requests.Response:
        """
        Stream record content (for testing streaming responses).
        
        Args:
            record_id: Internal record ID (_key)
            
        Returns:
            Response object (caller can iterate over response.iter_content())
        """
        url = self._url(self._stream_record_path(record_id))
        for attempt in range(2):
            self._ensure_access_token()
            resp = requests.get(
                url,
                headers=self._headers(),
                timeout=self.timeout_seconds,
                stream=True,
            )
            if resp.status_code == 401 and attempt == 0:
                logger.warning("Pipeshub stream returned 401; invalidating token and retrying once")
                self._invalidate_access_token()
                continue
            resp.raise_for_status()
            return resp
        raise PipeshubAuthError("stream_record failed after token retry")
