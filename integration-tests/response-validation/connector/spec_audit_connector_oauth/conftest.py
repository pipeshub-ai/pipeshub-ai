"""Shared fixtures for the strict OpenAPI audit of /api/v1/oauth."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from connector_oauth_audit_support import (  # noqa: E402
    SEED_CONNECTOR_TYPE,
    ConnectorOAuthClient,
    SeededOAuthConfig,
    SeedOAuthConfig,
    created_config_id,
    oauth_config_body,
)

logger = logging.getLogger("connector-oauth-audit")


@pytest.fixture(scope="session")
def connector_oauth_client(pipeshub_client: PipeshubClient) -> ConnectorOAuthClient:
    return ConnectorOAuthClient(pipeshub_client)


@pytest.fixture
def seed_oauth_config(
    connector_oauth_client: ConnectorOAuthClient,
) -> Iterator[SeedOAuthConfig]:
    """Factory: create one OAuth config as the admin; every one is deleted on teardown.

    ``seed_oauth_config(connector_type=SEED_CONNECTOR_TYPE, name=None, **config)`` returns
    ``{"id", "connector_type", "name", "body"}``. A config the test already deleted is fine.
    """
    created: list[tuple[str, str]] = []

    def _seed(
        connector_type: str = SEED_CONNECTOR_TYPE, name: str | None = None, **config: Any
    ) -> SeededOAuthConfig:
        body = oauth_config_body(name, **config)
        resp = connector_oauth_client.create(connector_type, body)
        config_id = created_config_id(resp)
        if resp.status_code >= 300 or not config_id:
            pytest.fail(
                f"could not seed an OAuth config for {connector_type}: "
                f"HTTP {resp.status_code} {resp.text[:300]}"
            )
        created.append((connector_type, config_id))
        return {
            "id": config_id,
            "connector_type": connector_type,
            "name": body["oauthInstanceName"],
            "body": body,
        }

    try:
        yield _seed
    finally:
        for connector_type, config_id in created:
            resp = connector_oauth_client.remove(connector_type, config_id)
            if resp.status_code >= 300 and resp.status_code != 404:
                logger.warning(
                    "could not delete seeded OAuth config %s/%s: HTTP %s %s",
                    connector_type, config_id, resp.status_code, resp.text[:200],
                )
