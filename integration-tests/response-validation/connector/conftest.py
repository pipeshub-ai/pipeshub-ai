"""Fixtures for the connector record-content ITs.

Uploads a small text file carrying a known sentinel string into a throwaway KB
and waits for indexing to reach COMPLETED: ``_fetch_multiple_records_impl``
only serves content for records whose ``indexingStatus`` is COMPLETED, so a
fixture that merely waits for the record to become visible (as the
knowledgebase suite's ``six_kb_records`` does) would race the parser and get a
404 back from the content endpoint.
"""

from __future__ import annotations

import logging
from typing import Iterator, TypedDict
from uuid import uuid4

import pytest

from helper.clients.kb_client import KBClient
from helper.clients.oauth_client import OAuthAppsClient, OAuthProviderClient
from messaging.test_e2e_record_pipeline import (
    TERMINAL_STATUSES,
    _extract_kb_id,
    _extract_record_id,
    _get_record_status,
    poll_until,
)
from pipeshub_client import PipeshubClient

logger = logging.getLogger("connector-conftest")

INDEX_TIMEOUT_SEC = 180
INDEX_POLL_INTERVAL_SEC = 3

_RECORD_BODY_TEMPLATE = """{sentinel}

Asana Disaster Recovery Summary Report (2023-08)

The recovery point objective for the primary datastore is fifteen minutes and
the recovery time objective is four hours. Failover is rehearsed quarterly by
the platform team, and the most recent rehearsal completed without data loss.

Backups are replicated to a secondary region every hour. Restore drills are
signed off by the on-call lead before the runbook is marked current.
"""


@pytest.fixture(scope="module")
def token_without_connector_read(pipeshub_client: PipeshubClient) -> Iterator[str]:
    """Access token from an OAuth app that deliberately omits `connector:read`.

    Minting a scope-restricted token for the *same* user isolates the scope check
    itself, and keeps this off the second-user path — that one seeds a password
    straight into MongoDB and needs TEST_MONGO_URI/TEST_MONGO_DB_NAME to match the
    running stack, which the config defaults do not.
    """
    apps = OAuthAppsClient(pipeshub_client)
    resp = apps.create_app(
        name=f"connector-content-it-noscope-{uuid4().hex[:8]}",
        allowedGrantTypes=["client_credentials"],
        allowedScopes=["openid", "profile", "kb:read"],
    )
    assert resp.status_code < 300, f"OAuth app create failed: {resp.status_code} {resp.text}"
    app = resp.json()["app"]

    try:
        token_resp = OAuthProviderClient(pipeshub_client).token(
            grant_type="client_credentials",
            client_id=app["clientId"],
            client_secret=app["clientSecret"],
        )
        assert token_resp.status_code < 300, (
            f"token fetch failed: {token_resp.status_code} {token_resp.text}"
        )
        access_token = token_resp.json().get("access_token")
        assert access_token, f"token response had no access_token: {token_resp.text}"

        yield str(access_token)
    finally:
        try:
            apps.delete_app(str(app["id"]))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to delete OAuth app %s: %s", app.get("id"), e)


class IndexedTextRecord(TypedDict):
    kb_id: str
    record_id: str
    record_name: str
    record_name_stem: str
    sentinel: str


@pytest.fixture(scope="module")
def indexed_text_record(
    pipeshub_client: PipeshubClient,
    ai_models_configured,
) -> Iterator[IndexedTextRecord]:
    """KB record whose content is parsed and retrievable, plus its sentinel text.

    Yields ``{"kb_id", "record_id", "record_name", "record_name_stem", "sentinel"}``
    and deletes the KB on teardown.
    """
    del ai_models_configured  # ordering only: indexing needs an LLM + embedding configured

    kb_client = KBClient(pipeshub_client)
    kb_id = _extract_kb_id(
        kb_client.create_kb(name=f"connector-content-it-kb-{uuid4().hex[:8]}")
    )
    assert kb_id, "KB create returned no id"
    logger.info("Created KB %s for connector record-content IT", kb_id)

    sentinel = f"pipeshub-content-sentinel-{uuid4().hex[:12]}"
    record_name_stem = f"connector-content-it-{uuid4().hex[:8]}"
    record_name = f"{record_name_stem}.txt"
    body = _RECORD_BODY_TEMPLATE.format(sentinel=sentinel).encode()

    try:
        record_id = _extract_record_id(
            kb_client.upload_file(kb_id, record_name, body, mimetype="text/plain")
        )
        assert record_id, "Upload returned no record id"
        logger.info("Uploaded %s to KB %s, record %s", record_name, kb_id, record_id)

        poll_until(
            lambda: _get_record_status(kb_client.get_record(record_id))
            in TERMINAL_STATUSES,
            timeout=INDEX_TIMEOUT_SEC,
            interval=INDEX_POLL_INTERVAL_SEC,
            description=f"record {record_id} to finish indexing",
        )

        status = _get_record_status(kb_client.get_record(record_id))
        assert status == "COMPLETED", (
            f"record {record_id} reached terminal status {status!r}, expected "
            "COMPLETED — the content endpoint 404s for anything else"
        )

        yield IndexedTextRecord(
            kb_id=kb_id,
            record_id=record_id,
            record_name=record_name,
            record_name_stem=record_name_stem,
            sentinel=sentinel,
        )
    finally:
        try:
            kb_client.delete_kb(kb_id)
            logger.info("Deleted KB %s", kb_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to delete KB %s: %s", kb_id, e)
