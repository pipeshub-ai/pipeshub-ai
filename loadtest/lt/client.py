"""Thin wrapper over the integration-tests PipeshubClient.

Reused rather than reimplemented: the OAuth2 client-credentials flow, token
refresh and connector CRUD already live in
integration-tests/helper/pipeshub_client.py, and that module only needs
`requests` — none of the heavy per-connector SDK dependencies of the wider
integration-tests package come with it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
_HELPER_DIR = REPO_ROOT / "integration-tests" / "helper"


def _refuse_refresh() -> None:
    raise RuntimeError(
        "LT_PIPESHUB_TOKEN expired mid-run. A supplied session JWT cannot be "
        "refreshed; re-export a fresh one and rerun."
    )


def _import_client():
    if not _HELPER_DIR.exists():
        raise RuntimeError(f"expected integration-tests helpers at {_HELPER_DIR}")
    if str(_HELPER_DIR) not in sys.path:
        sys.path.insert(0, str(_HELPER_DIR))
    from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

    return PipeshubClient


class Client:
    """Only the calls the harness makes, with load-test-specific behaviour."""

    #: The point of a load test is to measure the service, not the client's
    #: patience. PipeshubClient defaults to a 60s read timeout, which at 120
    #: concurrent connectors is shorter than the API takes to answer a
    #: sync toggle — so the run died with ReadTimeout and produced no number at
    #: exactly the load worth measuring. Raised, and configurable.
    DEFAULT_TIMEOUT_SEC = 300

    def __init__(self, base_url: str, timeout_seconds: int | None = None) -> None:
        if timeout_seconds is None:
            timeout_seconds = int(
                os.getenv("LT_CLIENT_TIMEOUT_SEC", str(self.DEFAULT_TIMEOUT_SEC))
            )
        self._impl = _import_client()(
            base_url=base_url, timeout_seconds=timeout_seconds
        )
        self.base_url = self._impl.base_url

        token = os.getenv("LT_PIPESHUB_TOKEN")
        if token:
            self._adopt_session_token(token.strip())

    def _adopt_session_token(self, token: str) -> None:
        """Use a user session JWT instead of the client-credentials flow.

        A client-credentials token cannot be an org admin: its ``userId`` claim
        is the OAuth client id, and Node's ``adminCheck`` rejects that as an
        invalid user id, so the connector service resolves the caller to
        ``member`` and refuses TEAM-scope connectors. Confluence is TEAM-only,
        so the harness needs a session JWT — which carries a ``role`` claim —
        to create anything at all.

        The token is injected into the wrapped client rather than layered on
        top, because every request it makes reads its own cached token.
        """
        impl = self._impl
        impl._access_token = token
        impl._token_claims = impl._decode_jwt_claims(token)
        exp = impl._token_claims.get("exp")
        # Expire exactly when the JWT does. Refreshing is impossible for a token
        # we were handed, so the run must fail loudly rather than silently fall
        # back to client-credentials and lose admin halfway through.
        impl._token_expires_at = float(exp) if exp else 0.0
        impl._fetch_access_token = _refuse_refresh  # type: ignore[method-assign]

    # -- auth ---------------------------------------------------------------

    def auth_headers(self) -> dict[str, str]:
        # A property on PipeshubClient, not a method — reading it refreshes an
        # expired token, which is why the probe resolves headers per request
        # rather than capturing them once.
        return self._impl.auth_headers

    @property
    def org_id(self) -> str:
        return self._impl.org_id

    # -- connectors ---------------------------------------------------------

    def create(self, connector_type: str, name: str, auth: dict, scope: str, auth_type: str | None) -> str:
        instance = self._impl.create_connector(
            connector_type=connector_type,
            instance_name=name,
            scope=scope,
            config={"auth": auth} if auth else None,
            auth_type=auth_type,
        )
        return instance.connector_id

    def set_filters(self, connector_id: str, filters: dict, sync: dict | None = None) -> dict:
        # The safe variant handles the backend rule that a connector must be
        # disabled while its filters change.
        return self._impl.update_connector_filters_sync_safe(
            connector_id, filters=filters, sync=sync or None
        )

    def stored_filters(self, connector_id: str) -> dict[str, Any]:
        """Filters as actually persisted.

        Worth reading back: the filters-sync endpoint answers
        "saved successfully" even when it stored nothing, because it copies only
        the keys it recognises (router.py:3900) and drops the rest without
        complaint.
        """
        data = self._impl._request_json("GET", f"/api/v1/connectors/{connector_id}/config")
        return ((data or {}).get("config") or {}).get("config", {}).get("filters", {}) or {}

    def start_sync(self, connector_id: str) -> dict:
        return self._impl.toggle_sync(connector_id, enable=True)

    def stop_sync(self, connector_id: str) -> dict:
        return self._impl.toggle_sync(connector_id, enable=False)

    def delete(self, connector_id: str) -> dict:
        return self._impl.delete_connector(connector_id)

    def list_instances(self) -> list[dict[str, Any]]:
        """Every connector instance in the org, active or not.

        Deliberately not `GET /api/v1/connectors/` — that answers 200 with an
        empty page, so anything listing from it silently sees no connectors.
        """
        found: dict[str, dict[str, Any]] = {}
        for path in ("active", "inactive"):
            data = self._impl._request_json("GET", f"/api/v1/connectors/{path}")
            items = (data or {}).get("connectors") or []
            for item in items:
                key = item.get("_key") or item.get("id") or item.get("_id")
                if key:
                    found[key] = item
        return list(found.values())

    def status(self, connector_id: str) -> dict:
        return self._impl.get_connector(connector_id)

    def stats(self, connector_id: str) -> dict[str, Any]:
        # _request_json rather than the public `request`, which hands back a raw
        # Response; this path is polled thousands of times a run and wants the
        # shared token-refresh/401-retry behaviour.
        data = self._impl._request_json("GET", f"/api/v1/connectors/{connector_id}/stats")
        return (data or {}).get("data") or {}
