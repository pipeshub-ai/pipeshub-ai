"""Shared fixtures for the strict OpenAPI audit of /api/v1/connectors."""

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

from connectors_audit_support import (  # noqa: E402
    ConnectorsAuditClient,
    SeedConnector,
    create_seed_connector,
)

logger = logging.getLogger("spec-audit-connectors")


def _remove(client: ConnectorsAuditClient, connector_ids: list[str]) -> None:
    for connector_id in connector_ids:
        resp = client.delete_instance(connector_id)
        if resp.status_code >= 400 and resp.status_code != 404:
            logger.warning(
                "could not delete seeded connector %s: %s %s",
                connector_id, resp.status_code, resp.text[:200],
            )


@pytest.fixture(scope="session")
def connectors_client(pipeshub_client: PipeshubClient) -> ConnectorsAuditClient:
    return ConnectorsAuditClient(pipeshub_client)


@pytest.fixture(scope="module")
def connector_id(connectors_client: ConnectorsAuditClient) -> Iterator[str]:
    """One admin-owned, team-scoped Demo instance per test file, deleted afterwards."""
    seeded = create_seed_connector(connectors_client)
    try:
        yield seeded
    finally:
        _remove(connectors_client, [seeded])


@pytest.fixture
def seed_connector(connectors_client: ConnectorsAuditClient) -> Iterator[SeedConnector]:
    """Factory for extra instances: ``seed_connector(**create_body_overrides)`` returns a connectorId."""
    created: list[str] = []

    def _seed(**overrides: Any) -> str:
        seeded = create_seed_connector(connectors_client, **overrides)
        created.append(seeded)
        return seeded

    try:
        yield _seed
    finally:
        _remove(connectors_client, created)
