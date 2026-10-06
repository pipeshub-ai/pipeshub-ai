"""Shared fixtures for the strict OpenAPI audit of /api/v1/crawlingManager."""

from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest
import requests

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import SecondUser, second_user  # noqa: E402, F401 - fixture

from crawling_manager_audit_support import (  # noqa: E402
    SEED_CONNECTOR_TYPE,
    CrawlingManagerClient,
    SeedConnector,
    SeededConnector,
    once_schedule_body,
    request_as,
)

logger = logging.getLogger("crawling-manager-audit")


@pytest.fixture(scope="session")
def crawling_manager_client(pipeshub_client: PipeshubClient) -> CrawlingManagerClient:
    return CrawlingManagerClient(pipeshub_client)


@pytest.fixture
def seed_connector(
    pipeshub_client: PipeshubClient, crawling_manager_client: CrawlingManagerClient
) -> Iterator[SeedConnector]:
    """Factory: create one never-enabled team connector; teardown removes its schedule, then it."""
    created: list[SeededConnector] = []

    def _seed() -> SeededConnector:
        instance = pipeshub_client.create_connector(
            SEED_CONNECTOR_TYPE,
            f"spec-audit-crawl-{uuid.uuid4().hex[:8]}",
            scope="team",
        )
        connector_id = str(instance.connector_id or "")
        if not connector_id:
            pytest.fail("connector create returned no id")
        # Registered before the read below so a failure there still cleans up.
        seeded = SeededConnector(connector_id, instance.connector_type)
        created.append(seeded)
        stored_type = pipeshub_client.get_connector(connector_id).get("type")
        if stored_type and stored_type != seeded.connector_type:
            created[-1] = seeded = SeededConnector(connector_id, str(stored_type))
        return seeded

    try:
        yield _seed
    finally:
        for seeded in created:
            # The schedule lives in BullMQ, which deleting the connector does not clear.
            try:
                crawling_manager_client.remove(seeded.connector_type, seeded.connector_id)
            except Exception as exc:  # noqa: BLE001 - still delete the connector
                logger.warning("Could not remove schedule of %s: %s", seeded.connector_id, exc)
            try:
                pipeshub_client.delete_connector(seeded.connector_id)
            except Exception as exc:  # noqa: BLE001 - teardown must not mask the test result
                logger.warning("Could not delete connector %s: %s", seeded.connector_id, exc)


@pytest.fixture
def scheduled_connector(
    seed_connector: SeedConnector, crawling_manager_client: CrawlingManagerClient
) -> SeededConnector:
    """A seeded connector holding a one-time schedule a year out, as the admin."""
    seeded = seed_connector()
    resp = crawling_manager_client.schedule(
        seeded.connector_type, seeded.connector_id, once_schedule_body()
    )
    if resp.status_code != 201:
        pytest.fail(f"could not schedule the seeded connector: {resp.status_code} {resp.text[:300]}")
    return seeded


@pytest.fixture
def member_personal_connector(second_user: SecondUser) -> Iterator[SeededConnector]:  # noqa: F811
    """A never-enabled personal connector the non-admin member created and owns.

    The admin cannot see it, so the member's own token also clears its schedule and deletes it.
    """
    connectors_url = f"{second_user.base_url}/api/v1/connectors"
    resp = requests.post(
        f"{connectors_url}/",
        headers=second_user.headers,
        json={
            "connectorType": SEED_CONNECTOR_TYPE,
            "instanceName": f"spec-audit-crawl-member-{uuid.uuid4().hex[:8]}",
            "scope": "personal",
        },
        timeout=second_user.timeout,
    )
    connector = (resp.json().get("connector") or {}) if resp.status_code < 300 else {}
    connector_id = str(connector.get("connectorId") or connector.get("_key") or "")
    if not connector_id:
        pytest.fail(f"member could not create a personal connector: {resp.status_code} {resp.text[:300]}")
    seeded = SeededConnector(connector_id, str(connector.get("connectorType") or SEED_CONNECTOR_TYPE))
    try:
        yield seeded
    finally:
        removed = request_as(
            second_user, "DELETE", f"/{seeded.connector_type}/{seeded.connector_id}/remove"
        )
        if removed.status_code != 200:
            logger.warning("Could not remove schedule of %s: %s", connector_id, removed.text[:200])
        deleted = requests.delete(
            f"{connectors_url}/{connector_id}", headers=second_user.headers, timeout=second_user.timeout
        )
        if deleted.status_code >= 300:
            logger.warning("Could not delete member connector %s: %s", connector_id, deleted.text[:200])


@pytest.fixture
def token_without_crawl_scopes(token_without_connector_read: str) -> str:
    """The parent folder's OAuth token (openid, profile, kb:read): it carries no crawl:* scope either."""
    return token_without_connector_read
