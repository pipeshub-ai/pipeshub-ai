"""Shared fixtures for the strict OpenAPI audit of /api/v1/connectors."""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.clients.kb_client import KBClient  # noqa: E402
from helper.clients.oauth_client import OAuthAppsClient, OAuthProviderClient  # noqa: E402
from helper.http.session_client import SessionClient  # noqa: E402
from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import SecondUser, second_user  # noqa: E402, F401 - fixture
from helper.vector_rebuild import (  # noqa: E402
    VECTOR_STORE_REBUILD_FLAG,
    read_platform_settings,
    set_rebuild_flag,
)
from messaging.test_e2e_record_pipeline import (  # noqa: E402
    TERMINAL_STATUSES,
    _extract_kb_id,
    _extract_record_id,
    _get_record_fields,
    poll_until,
)

from app.services.redis.config import ClientOptions  # noqa: E402
from app.services.redis.connection_provider_factory import get_redis_provider  # noqa: E402
from app.services.vector_db.rebuild_state import JOB_LOCK_KEY  # noqa: E402

from connectors_audit_support import (  # noqa: E402
    MEMBER_TOKEN_AUTH,
    OAUTH_BASE_URL,
    ConnectorsAuditClient,
    KbRecords,
    SeedConnector,
    StubSource,
    create_seed_connector,
    created_connector_id,
    is_settled,
    is_syncing,
    personal_body,
    request_as,
    wait_for_state,
)

logger = logging.getLogger("spec-audit-connectors")

INDEX_TIMEOUT_SEC = 240
INDEX_POLL_INTERVAL_SEC = 3
# Long enough for the two calls made under the lock, short enough that a crashed
# run frees the real job lock by itself.
REBUILD_LOCK_TTL_SEC = 60


def _remove(client: ConnectorsAuditClient, connector_ids: list[str]) -> None:
    for connector_id in connector_ids:
        resp = client.delete_instance(connector_id)
        # 409: the test already asked for the deletion and it is still running.
        if resp.status_code >= 400 and resp.status_code not in (404, 409):
            logger.warning("could not delete a seeded connector: HTTP %s", resp.status_code)


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


@pytest.fixture
def cleanup_connectors(connectors_client: ConnectorsAuditClient) -> Iterator[list[str]]:
    """Connector ids appended by a test that creates instances itself; all deleted on teardown."""
    created: list[str] = []
    try:
        yield created
    finally:
        _remove(connectors_client, created)


@pytest.fixture
def member_connector(
    connectors_client: ConnectorsAuditClient, second_user: SecondUser  # noqa: F811 - the fixture
) -> Iterator[SeedConnector]:
    """Factory for personal instances owned by the non-admin member.

    Removed as the admin, who may delete any connector; the member's own delete
    would do as well, but this keeps teardown independent of the member's session.
    """
    created: list[str] = []

    def _seed(**overrides: Any) -> str:
        resp = request_as(second_user, "POST", "/", json=personal_body(**overrides))
        seeded = created_connector_id(resp)
        created.append(seeded)
        return seeded

    try:
        yield _seed
    finally:
        _remove(connectors_client, created)


@pytest.fixture(scope="session")
def token_without_connector_scopes(
    pipeshub_client: PipeshubClient, user_session_client: SessionClient
) -> Iterator[str]:
    """Admin's access token from an OAuth app granted no connector or kb scope.

    Every connectors route sits behind requireScopes, so this isolates the scope
    refusal from the role and ownership checks that also answer 403 or 404.
    Registering the app needs the admin's login session: OAuth client routes
    refuse OAuth tokens (#3626).
    """
    apps = OAuthAppsClient(user_session_client)
    resp = apps.create_app(
        name=f"spec-audit-connectors-noscope-{uuid.uuid4().hex[:8]}",
        allowedGrantTypes=["client_credentials"],
        allowedScopes=["openid", "profile"],
    )
    assert resp.status_code < 300, f"OAuth app create failed: {resp.status_code} {resp.text[:300]}"
    app = resp.json()["app"]
    try:
        token_resp = OAuthProviderClient(pipeshub_client).token(
            grant_type="client_credentials",
            client_id=app["clientId"],
            client_secret=app["clientSecret"],
        )
        assert token_resp.status_code < 300, (
            f"token fetch failed: {token_resp.status_code} {token_resp.text[:300]}"
        )
        yield str(token_resp.json()["access_token"])
    finally:
        deleted = apps.delete_app(str(app["id"]))
        if deleted.status_code >= 300:
            logger.warning("could not delete an OAuth app: HTTP %s", deleted.status_code)


@pytest.fixture(scope="session")
def token_with_scopes(
    pipeshub_client: PipeshubClient, user_session_client: SessionClient
) -> Iterator[Callable[..., str]]:
    """Factory: the admin's access token from an OAuth app granted exactly ``scopes``.

    Shows which single scope a route accepts, apart from the role checks.
    """
    apps = OAuthAppsClient(user_session_client)
    created: list[str] = []
    tokens: dict[tuple[str, ...], str] = {}

    def _token(*scopes: str) -> str:
        key = tuple(sorted(scopes))
        if key in tokens:
            return tokens[key]
        resp = apps.create_app(
            name=f"spec-audit-connectors-{'-'.join(key).replace(':', '_')}-{uuid.uuid4().hex[:8]}",
            allowedGrantTypes=["client_credentials"],
            allowedScopes=["openid", *key],
        )
        assert resp.status_code < 300, f"OAuth app create failed: {resp.status_code} {resp.text[:300]}"
        app = resp.json()["app"]
        created.append(str(app["id"]))
        token_resp = OAuthProviderClient(pipeshub_client).token(
            grant_type="client_credentials",
            client_id=app["clientId"],
            client_secret=app["clientSecret"],
            scope=" ".join(key),
        )
        assert token_resp.status_code < 300, (
            f"token fetch failed: {token_resp.status_code} {token_resp.text[:300]}"
        )
        tokens[key] = str(token_resp.json()["access_token"])
        return tokens[key]

    try:
        yield _token
    finally:
        for app_id in created:
            deleted = apps.delete_app(app_id)
            if deleted.status_code >= 300:
                logger.warning("could not delete an OAuth app: HTTP %s", deleted.status_code)


@pytest.fixture(scope="session")
def kb_records(pipeshub_client: PipeshubClient) -> Iterator[KbRecords]:
    """A throwaway KB holding one text record and one record of an unsupported type.

    Both are waited to a terminal indexing status. The text record's status is
    reported, not asserted, so tests that only need a record in the graph do not
    depend on the indexing pipeline; the one that reads its content asserts it.
    """
    kb = KBClient(pipeshub_client)
    token = uuid.uuid4().hex[:10]
    kb_id = _extract_kb_id(kb.create_kb(name=f"spec-audit-connectors-kb-{token}"))
    assert kb_id, "KB create returned no id"
    sentinel = f"spec-audit-sentinel-{token}"
    try:
        text_id = _extract_record_id(
            kb.upload_file(
                kb_id,
                f"spec-audit-{token}.txt",
                (
                    f"{sentinel}\n\nThe recovery point objective for the primary datastore is "
                    "fifteen minutes and the recovery time objective is four hours.\n"
                ).encode(),
                mimetype="text/plain",
            )
        )
        unsupported_id = _extract_record_id(
            kb.upload_file(
                kb_id,
                f"spec-audit-{token}.bin",
                bytes(range(256)),
                mimetype="application/octet-stream",
            )
        )

        def _status(record_id: str) -> dict[str, Any]:
            return _get_record_fields(kb.get_record(record_id))

        poll_until(
            lambda: _status(text_id).get("indexingStatus") in TERMINAL_STATUSES
            and _status(unsupported_id).get("indexingStatus") in TERMINAL_STATUSES,
            timeout=INDEX_TIMEOUT_SEC,
            interval=INDEX_POLL_INTERVAL_SEC,
            description=f"records {text_id} and {unsupported_id} to finish indexing",
        )
        text_record = _status(text_id)
        yield KbRecords(
            kb_id=kb_id,
            text_record_id=text_id,
            text_record_status=str(text_record.get("indexingStatus")),
            text_record_reason=str(text_record.get("reason") or ""),
            text_sentinel=sentinel,
            unsupported_record_id=unsupported_id,
        )
    finally:
        try:
            kb.delete_kb(kb_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("could not delete a knowledge base: %s", type(e).__name__)


async def _set_rebuild_lock(token: str) -> bool:
    client = get_redis_provider().create_client(ClientOptions(decode_responses=True))
    try:
        return bool(await client.set(JOB_LOCK_KEY, token, nx=True, ex=REBUILD_LOCK_TTL_SEC))
    finally:
        await client.aclose()


async def _release_rebuild_lock(token: str) -> None:
    client = get_redis_provider().create_client(ClientOptions(decode_responses=True))
    try:
        if await client.get(JOB_LOCK_KEY) == token:
            await client.delete(JOB_LOCK_KEY)
    finally:
        await client.aclose()


@pytest.fixture
def vector_store_rebuild_busy(pipeshub_client: PipeshubClient) -> Iterator[None]:
    """Vector-store rebuild switched on while this test holds the single-job lock.

    The only way to see what the two rebuild routes answer past their feature
    flag without starting a job: a cleanup would drop the vector collection every
    agent on this stack shares. The lock is taken first and released last, so no
    job can start in the window the flag is on.
    """
    token = f"spec-audit-{uuid.uuid4().hex}"
    if not asyncio.run(_set_rebuild_lock(token)):
        pytest.fail("a vector-store cleanup or reindex job already holds the rebuild lock")
    was_enabled = read_platform_settings(pipeshub_client).flag(VECTOR_STORE_REBUILD_FLAG)
    try:
        set_rebuild_flag(pipeshub_client, True)
        yield
    finally:
        try:
            set_rebuild_flag(pipeshub_client, was_enabled)
        finally:
            asyncio.run(_release_rebuild_lock(token))


@pytest.fixture
def vector_store_rebuild_disabled(pipeshub_client: PipeshubClient) -> Iterator[None]:
    """Vector-store rebuild switched off by the administrator for one test, then restored."""
    was_enabled = read_platform_settings(pipeshub_client).flag(VECTOR_STORE_REBUILD_FLAG)
    if was_enabled:
        set_rebuild_flag(pipeshub_client, False)
    try:
        yield
    finally:
        if was_enabled:
            set_rebuild_flag(pipeshub_client, True)


@pytest.fixture
def syncing_connector(connectors_client: ConnectorsAuditClient) -> Iterator[str]:
    """A Demo instance whose first sync was just started and is still running.

    Demo contacts nothing external. Teardown stops the sync early, so few demo
    records are written, waits for it to settle and removes the instance.
    """
    seeded = create_seed_connector(connectors_client)
    try:
        resp = connectors_client.post(f"/{seeded}/toggle", json={"type": "sync"})
        assert resp.status_code == 200, resp.text[:300]
        wait_for_state(connectors_client, seeded, is_syncing, "a running sync")
        yield seeded
    finally:
        try:
            connectors_client.post(f"/{seeded}/sync/stop")
            state = wait_for_state(connectors_client, seeded, is_settled, "the end of its sync")
            if state.get("isActive"):
                connectors_client.post(f"/{seeded}/toggle", json={"type": "sync"})
        finally:
            _remove(connectors_client, [seeded])


@pytest.fixture
def member_configured_connector(
    second_user: SecondUser,  # noqa: F811 - the fixture
    member_connector: SeedConnector,
) -> str:
    """A personal instance of the member with API-token credentials saved (nothing is contacted)."""
    seeded = member_connector()
    resp = request_as(second_user, "PUT", f"/{seeded}/config/auth", json={"auth": MEMBER_TOKEN_AUTH})
    assert resp.status_code == 200, resp.text[:300]
    return seeded


@pytest.fixture(scope="module")
def bookstack_source() -> Iterator[StubSource]:
    """A BookStack stand-in whose search lists one book."""
    body = {"data": [{"id": 7, "name": "Spec Audit Book", "slug": "spec-audit-book", "type": "book"}], "total": 1}
    with StubSource(body) as stub:
        yield stub


@pytest.fixture(scope="module")
def bookstack_connector(
    connectors_client: ConnectorsAuditClient, bookstack_source: StubSource
) -> Iterator[str]:
    """A team BookStack instance whose credentials point at the local stand-in."""
    seeded = create_seed_connector(connectors_client, connectorType="BookStack", authType="API_TOKEN")
    try:
        resp = connectors_client.put(
            f"/{seeded}/config/auth",
            json={"auth": {"base_url": bookstack_source.url, "token_id": "spec-audit", "token_secret": "spec-audit"}},
        )
        assert resp.status_code == 200, resp.text[:300]
        yield seeded
    finally:
        _remove(connectors_client, [seeded])


@pytest.fixture
def oauth_provider() -> Iterator[StubSource]:
    """An OAuth provider stand-in: its token endpoint issues a token for any code."""
    with StubSource({}) as stub:
        yield stub


@pytest.fixture
def gitlab_oauth_connector(
    connectors_client: ConnectorsAuditClient,
    pipeshub_client: PipeshubClient,
    oauth_provider: StubSource,
) -> Iterator[str]:
    """A team GitLab OAuth instance whose instance URL is the local provider stand-in.

    Saving client credentials as an admin creates a shared OAuth app, which is
    removed with the instance.
    """
    seeded = create_seed_connector(connectors_client, connectorType="GitLab", authType="OAUTH")
    oauth_app_id = None
    try:
        resp = connectors_client.put(
            f"/{seeded}/config/auth",
            json={
                "auth": {"clientId": "spec-audit-client", "clientSecret": "spec-audit-secret", "instanceUrl": oauth_provider.url},
                "baseUrl": OAUTH_BASE_URL,
            },
        )
        assert resp.status_code == 200, resp.text[:300]
        oauth_app_id = resp.json()["config"]["auth"].get("oauthConfigId")
        yield seeded
    finally:
        _remove(connectors_client, [seeded])
        if oauth_app_id:
            deleted = pipeshub_client.request("DELETE", f"/api/v1/oauth/GitLab/{oauth_app_id}")
            if deleted.status_code >= 300:
                logger.warning("could not delete the GitLab OAuth app: HTTP %s", deleted.status_code)


@pytest.fixture
def agent_capable_connector(connectors_client: ConnectorsAuditClient) -> Iterator[str]:
    """A configured team Confluence instance (API token, never contacted): agent use can be toggled."""
    seeded = create_seed_connector(connectors_client, connectorType="Confluence", authType="API_TOKEN")
    try:
        resp = connectors_client.put(
            f"/{seeded}/config/auth",
            json={"auth": {"baseUrl": "https://spec-audit.invalid", "email": "spec-audit@example.com", "apiToken": "spec-audit"}},
        )
        assert resp.status_code == 200, resp.text[:300]
        yield seeded
    finally:
        _remove(connectors_client, [seeded])


@pytest.fixture
def local_fs_connector(connectors_client: ConnectorsAuditClient) -> Iterator[str]:
    """A personal Local FS instance of the admin that no desktop device has claimed."""
    seeded = create_seed_connector(
        connectors_client,
        connectorType="Local FS",
        scope="personal",
        config={"sync": {"sync_root_path": f"/Users/spec-audit/{uuid.uuid4().hex[:8]}"}},
    )
    try:
        yield seeded
    finally:
        _remove(connectors_client, [seeded])
