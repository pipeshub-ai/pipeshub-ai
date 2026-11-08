"""Fixtures for the connector response-validation ITs.

Record content: uploads a small text file carrying a known sentinel string into
a throwaway KB and waits for indexing to reach COMPLETED:
``_fetch_multiple_records_impl`` only serves content for records whose
``indexingStatus`` is COMPLETED, so a fixture that merely waits for the record
to become visible (as the knowledgebase suite's ``six_kb_records`` does) would
race the parser and get a 404 back from the content endpoint.

Connector setup: one connector instance per auth style (none, basic auth, OAuth
app) plus scope-limited tokens, for the registry, create, configure and enable
calls an SDK user makes to connect a source and start its sync.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Iterator, Sequence, TypedDict
from uuid import uuid4

import pytest
import pytest_asyncio
import requests
from app.config.constants.arangodb import CollectionNames

from connector_lifecycle import source_unavailable
from helper.clients.connectors_client import ConnectorOAuthConfigsClient, ConnectorsClient
from helper.clients.kb_client import KBClient
from helper.http.session_client import SessionClient
from helper.mcp_oauth import authorization_code_token, create_oauth_app, delete_oauth_app
from helper.web_fixtures import WebFixtures
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
def token_without_connector_read(user_session_client: SessionClient) -> Iterator[str]:
    """Access token from an OAuth app that deliberately omits `connector:read`.

    Minting a scope-restricted token for the *same* user isolates the scope check
    itself, and keeps this off the second-user path — that one seeds a password
    straight into MongoDB and needs TEST_MONGO_URI/TEST_MONGO_DB_NAME to match the
    running stack, which the config defaults do not. Registering the app and
    consenting need the user's login session: OAuth client routes refuse OAuth
    tokens (#3626). Authorization code rather than client_credentials, because a
    client_credentials app acts as a service account, not as this user.
    """
    with _user_scoped_tokens(user_session_client) as mint:
        yield mint(["kb:read"])


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


# ---------------------------------------------------------------------------
# Connector setup ITs: one instance per connector auth style
# ---------------------------------------------------------------------------

# Creating a connector never contacts its source, so the instances below need no
# external service. Only enabling sync does: it tests the connection first.
RSS_FEED_PATH = "feeds/news.xml"
OAUTH_REDIRECT_BASE_URL = "http://localhost:3001"
JIRA_OAUTH_CLIENT_ID = "connector-spec-it-client-id"


@dataclass
class CreatedConnector:
    connector_id: str
    connector_type: str
    request_body: dict[str, Any]
    response: requests.Response
    oauth_config_id: str | None = None


def _create_connector(connectors: ConnectorsClient, body: dict[str, Any]) -> CreatedConnector:
    resp = connectors.create_instance(body)
    assert resp.status_code < 300, (
        f"creating a {body['connectorType']} connector failed: {resp.status_code} {resp.text}"
    )
    connector_id = (resp.json().get("connector") or {}).get("connectorId")
    assert connector_id, f"create response carried no connector id: {resp.text}"
    logger.info("Created a test %s connector", body["connectorType"])
    return CreatedConnector(
        connector_id=connector_id,
        connector_type=body["connectorType"],
        request_body=body,
        response=resp,
    )


def _delete_connector(connectors: ConnectorsClient, connector_id: str) -> None:
    # Logs carry no IDs, bodies or exception text: CodeQL traces them back to the
    # fixture credentials the same client sent.
    try:
        resp = connectors.delete_instance(connector_id)
        if resp.status_code >= 300:
            logger.warning("Deleting a test connector answered %s", resp.status_code)
    except Exception as e:  # noqa: BLE001
        logger.warning("Deleting a test connector failed: %s", type(e).__name__)


@pytest.fixture(scope="module")
def connectors_api(pipeshub_client: PipeshubClient) -> ConnectorsClient:
    return ConnectorsClient(pipeshub_client)


@pytest.fixture(scope="module")
def oauth_configs_api(pipeshub_client: PipeshubClient) -> ConnectorOAuthConfigsClient:
    return ConnectorOAuthConfigsClient(pipeshub_client)


@pytest.fixture(scope="session")
def rss_feed_url() -> str:
    """The web-fixtures news feed, as the connectors service reaches it."""
    return WebFixtures().url_for_connector(RSS_FEED_PATH)


@pytest.fixture(scope="session")
def rss_feed_reachable() -> None:
    """Enabling sync tests the feed first, so toggling RSS on needs web-fixtures up."""
    fixtures = WebFixtures()
    try:
        fixtures.check_available()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        source_unavailable(f"web-fixtures service not reachable at {fixtures.test_url}: {exc}")


@pytest.fixture(scope="module")
def rss_connector(
    connectors_api: ConnectorsClient, rss_feed_url: str
) -> Iterator[CreatedConnector]:
    """Team RSS connector: auth type NONE, so creating it needs no credentials."""
    created = _create_connector(
        connectors_api,
        {
            "connectorType": "RSS",
            "instanceName": f"connector-spec-it-rss-{uuid4().hex[:8]}",
            "scope": "team",
            "config": {"sync": {"feed_urls": rss_feed_url, "max_articles_per_feed": 1}},
        },
    )
    try:
        yield created
    finally:
        _delete_connector(connectors_api, created.connector_id)


SYNC_IDLE_TIMEOUT_SEC = 90


def _wait_until_idle(connectors: ConnectorsClient, connector_id: str) -> None:
    """Wait out the sync a toggle or resync started: a running sync locks the connector."""
    deadline = time.monotonic() + SYNC_IDLE_TIMEOUT_SEC
    while True:
        connector = connectors.get_instance(connector_id).json()["connector"]
        if connector.get("status") == "IDLE" and not connector.get("isLocked"):
            return
        assert time.monotonic() < deadline, f"sync still running: {connector.get('status')}"
        time.sleep(1)


@pytest.fixture
def wait_until_idle(connectors_api: ConnectorsClient) -> Callable[[str], None]:
    return lambda connector_id: _wait_until_idle(connectors_api, connector_id)


@pytest_asyncio.fixture(loop_scope="session")
async def lock_connector(graph_provider: Any) -> AsyncIterator[Callable[[str], Awaitable[None]]]:
    """Lock a connector the way a full sync's prep phase does, and unlock it after the test.

    A real sync can finish before the next request reads the status, so a 409
    test that relies on one is a race. Writing the lock to the app node is not.
    """
    locked: list[str] = []

    async def lock(connector_id: str) -> None:
        await graph_provider.update_node(
            connector_id,
            CollectionNames.APPS.value,
            {"status": "FULL_SYNCING", "isLocked": True},
        )
        locked.append(connector_id)

    try:
        yield lock
    finally:
        # One failed unlock must not leave the rest locked for the module's later tests.
        for connector_id in locked:
            try:
                await graph_provider.update_node(
                    connector_id, CollectionNames.APPS.value, {"status": "IDLE", "isLocked": False}
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Unlocking a test connector failed: %s", type(e).__name__)


@pytest.fixture(scope="module")
def active_rss_connector(
    connectors_api: ConnectorsClient, rss_feed_url: str, rss_feed_reachable: None
) -> Iterator[CreatedConnector]:
    """Team RSS connector with sync on and its first sync finished, ready to resync."""
    created = _create_connector(
        connectors_api,
        {
            "connectorType": "RSS",
            "instanceName": f"connector-spec-it-rss-active-{uuid4().hex[:8]}",
            "scope": "team",
            "config": {"sync": {"feed_urls": rss_feed_url, "max_articles_per_feed": 1}},
        },
    )
    try:
        resp = connectors_api.toggle(created.connector_id)
        assert resp.status_code == 200, f"enabling the RSS connector failed: {resp.status_code}"
        _wait_until_idle(connectors_api, created.connector_id)
        yield created
    finally:
        _delete_connector(connectors_api, created.connector_id)


@pytest.fixture(scope="module")
def postgres_connector(connectors_api: ConnectorsClient) -> Iterator[CreatedConnector]:
    """Team PostgreSQL connector with basic-auth credentials for a port nothing listens on."""
    created = _create_connector(
        connectors_api,
        {
            "connectorType": "PostgreSQL",
            "instanceName": f"connector-spec-it-pg-{uuid4().hex[:8]}",
            "scope": "team",
            "authType": "BASIC_AUTH",
            "config": {
                "auth": {
                    "host": "127.0.0.1",
                    "port": 1,
                    "database": "connector_spec_it",
                    "username": "connector_spec_it",
                    "password": f"pw-{uuid4().hex}",
                }
            },
        },
    )
    try:
        yield created
    finally:
        _delete_connector(connectors_api, created.connector_id)


def _linked_oauth_config_id(connectors: ConnectorsClient, connector_id: str) -> str | None:
    resp = connectors.get_config(connector_id)
    assert resp.status_code == 200, resp.text
    return (((resp.json().get("config") or {}).get("config") or {}).get("auth") or {}).get(
        "oauthConfigId"
    )


@pytest.fixture(scope="module")
def linked_oauth_config_id(connectors_api: ConnectorsClient) -> Callable[[str], str | None]:
    """The ID of the shared OAuth app a connector instance signs in with."""
    return lambda connector_id: _linked_oauth_config_id(connectors_api, connector_id)


@pytest.fixture(scope="module")
def jira_oauth_connector(
    connectors_api: ConnectorsClient, oauth_configs_api: ConnectorOAuthConfigsClient
) -> Iterator[CreatedConnector]:
    """Team Jira connector created by an admin with OAuth app credentials inline.

    The server saves those credentials as a shared OAuth app and links it, so no
    separate POST /api/v1/oauth/{connectorType} is needed. The app is deleted too.
    """
    created = _create_connector(
        connectors_api,
        {
            "connectorType": "Jira",
            "instanceName": f"connector-spec-it-jira-{uuid4().hex[:8]}",
            "scope": "team",
            "authType": "OAUTH",
            "baseUrl": OAUTH_REDIRECT_BASE_URL,
            "config": {
                "auth": {
                    "clientId": JIRA_OAUTH_CLIENT_ID,
                    "clientSecret": f"secret-{uuid4().hex}",
                    "oauthInstanceName": f"connector-spec-it-jira-app-{uuid4().hex[:8]}",
                }
            },
        },
    )
    oauth_config_id = None
    try:
        oauth_config_id = _linked_oauth_config_id(connectors_api, created.connector_id)
        assert oauth_config_id, "creating the connector linked no OAuth app"
        created.oauth_config_id = oauth_config_id
        yield created
    finally:
        _delete_connector(connectors_api, created.connector_id)
        if oauth_config_id:
            _delete_oauth_app(oauth_configs_api, "Jira", oauth_config_id)


def _delete_oauth_app(
    oauth_configs_api: ConnectorOAuthConfigsClient, connector_type: str, oauth_config_id: str
) -> None:
    # Logs carry no IDs, bodies or exception text: CodeQL traces them back to the
    # fixture credentials the same client sent.
    try:
        resp = oauth_configs_api.delete_config(connector_type, oauth_config_id)
        if resp.status_code >= 300:
            logger.warning(
                "Deleting a test %s OAuth app answered %s", connector_type, resp.status_code
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Deleting a test %s OAuth app failed: %s", connector_type, type(e).__name__
        )


@pytest.fixture(scope="module")
def scoped_token(user_session_client: SessionClient) -> Iterator[Callable[[Sequence[str]], str]]:
    """Mint a token for the test user that carries exactly the given scopes.

    Lets a test call a route with only the scopes the spec declares for it. The
    OAuth apps are registered with the user's login session (#3626) and deleted
    on teardown.
    """
    with _user_scoped_tokens(user_session_client) as mint:
        yield mint


@contextmanager
def _user_scoped_tokens(
    user_session_client: SessionClient,
) -> Iterator[Callable[[Sequence[str]], str]]:
    """Authorization-code tokens for the test user, one OAuth app per scope set."""
    base_url = user_session_client.base_url
    user_jwt = user_session_client.token
    tokens: dict[tuple[str, ...], str] = {}
    app_ids: list[str] = []

    def mint(scopes: Sequence[str]) -> str:
        key = tuple(sorted(set(scopes)))
        if key in tokens:
            return tokens[key]
        app = create_oauth_app(
            base_url,
            user_jwt,
            name=f"connector-spec-it-{uuid4().hex[:8]}",
            scopes=["openid", "profile", *key],
            grant_types=["authorization_code"],
        )
        app_ids.append(app.id)
        tokens[key] = str(authorization_code_token(base_url, user_jwt, app)["access_token"])
        return tokens[key]

    try:
        yield mint
    finally:
        for app_id in app_ids:
            try:
                delete_oauth_app(base_url, user_jwt, app_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to delete OAuth app %s: %s", app_id, e)


@pytest.fixture(scope="session")
def minio_reachable() -> None:
    """MinIO is the dynamic-filter source the integration stack runs itself."""
    endpoint = os.getenv("MINIO_TEST_ENDPOINT", "http://localhost:9000").rstrip("/")
    try:
        requests.get(f"{endpoint}/minio/health/live", timeout=5).raise_for_status()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        source_unavailable(f"MinIO not reachable at {endpoint}: {exc}")


@pytest.fixture(scope="module")
def minio_connector(
    connectors_api: ConnectorsClient, minio_reachable: None
) -> Iterator[CreatedConnector]:
    """Team MinIO connector on the integration stack's MinIO; ``buckets`` is a dynamic filter."""
    del minio_reachable
    created = _create_connector(
        connectors_api,
        {
            "connectorType": "MinIO",
            "instanceName": f"connector-spec-it-minio-{uuid4().hex[:8]}",
            "scope": "team",
            "config": {
                "auth": {
                    # The connector reaches MinIO inside the compose network.
                    "endpointUrl": os.getenv("MINIO_CONNECTOR_ENDPOINT", "http://minio:9000"),
                    "accessKey": os.getenv("MINIO_ROOT_USER", "pipeshubtest"),
                    "secretKey": os.getenv("MINIO_ROOT_PASSWORD", "pipeshubtest123"),
                    "useSsl": False,
                    "verifySsl": False,
                }
            },
        },
    )
    try:
        yield created
    finally:
        _delete_connector(connectors_api, created.connector_id)
