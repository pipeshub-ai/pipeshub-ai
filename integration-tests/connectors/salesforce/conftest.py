# pyright: ignore-file

"""Salesforce connector fixtures (see DESIGN.md).

Two sources, chosen from the environment:
- the simulator (``salesforce_sim.env``): the suite loads its own org, can change it, and
  asks the simulator's oracle who may read what;
- a live org, read only (``SALESFORCE_TEST_*``): nothing is ever written to Salesforce, so
  every case that changes the org skips there.
"""

import logging
import os
import uuid
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio

from connectors.gmail.gmail_users import ensure_active_user  # type: ignore[import-not-found]
from connectors.salesforce.constants import (  # type: ignore[import-not-found]
    API_VERSION,
    LIVE_VARS,
    SALESFORCE_CONNECTOR_BASE_URL,
    SALESFORCE_SIM_URL,
    SALESFORCE_TEST_LOGIN_URL,
    SIM_USERS,
    SYNC_TIMEOUT_SEC,
)
from connectors.salesforce.salesforce_sim import SalesforceSim  # type: ignore[import-not-found]
from connectors.salesforce.scenario import email, scenario  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from helper.oauth_token_helper import authenticate_connector_with_refresh_token  # type: ignore[import-not-found]
from helper.source_credentials import source_unavailable  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("salesforce-conftest")


class SalesforceSource:
    """Where the suite's Salesforce is, and how to read it as the connector's account."""

    def __init__(self, *, mode: str, instance_url: str, connector_instance_url: str, token_url: str,
                 client_id: str, client_secret: str, refresh_token: str,
                 sim: SalesforceSim | None = None, ids: dict[str, str] | None = None) -> None:
        self.mode = mode
        self.instance_url = instance_url
        self.connector_instance_url = connector_instance_url
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.sim = sim
        self.ids = ids or {}
        self.admin_email = ""
        self._access_token: str | None = None

    @property
    def simulated(self) -> bool:
        return self.mode == "sim"

    def access_token(self) -> str:
        if self._access_token is None:
            resp = httpx.post(self.token_url, data={
                "grant_type": "refresh_token", "client_id": self.client_id,
                "client_secret": self.client_secret, "refresh_token": self.refresh_token}, timeout=30)
            resp.raise_for_status()
            self._access_token = resp.json()["access_token"]
        return self._access_token

    def soql(self, q: str) -> list[dict[str, Any]]:
        """Every row of a read-only query, as the connector's account."""
        headers = {"Authorization": f"Bearer {self.access_token()}"}
        out: list[dict[str, Any]] = []
        with httpx.Client(base_url=self.instance_url, headers=headers, timeout=60) as client:
            resp = client.get(f"/services/data/v{API_VERSION}/query", params={"q": q})
            while True:
                resp.raise_for_status()
                body = resp.json()
                out.extend(body.get("records") or [])
                if body.get("done", True):
                    return out
                resp = client.get(body["nextRecordsUrl"])


@pytest.fixture(scope="session")
def salesforce_source(pipeshub_client: PipeshubClient) -> SalesforceSource:
    if SALESFORCE_SIM_URL:
        sim = SalesforceSim()
        if not sim.reachable():
            pytest.fail(f"SALESFORCE_SIM_URL is set but the simulator does not answer at {SALESFORCE_SIM_URL}")
        admin_email = (os.getenv("PIPESHUB_TEST_USER_EMAIL") or pipeshub_client._claims().get("email") or "").lower()
        loaded = sim.load(scenario(admin_email or None))
        oauth = loaded["oauth"]
        source = SalesforceSource(
            mode="sim", instance_url=SALESFORCE_SIM_URL,
            connector_instance_url=SALESFORCE_CONNECTOR_BASE_URL or oauth["instance_url"],
            token_url=f"{SALESFORCE_SIM_URL}/services/oauth2/token",
            client_id=oauth["client_id"], client_secret=oauth["client_secret"],
            refresh_token=oauth["refresh_token"], sim=sim, ids=loaded["ids"])
        source.admin_email = admin_email or email("admin")
        return source
    missing = [v for v in LIVE_VARS if not os.getenv(v)]
    if missing:
        source_unavailable("Neither the Salesforce simulator nor a live Salesforce org is configured.",
                           secrets=LIVE_VARS)
    instance = os.environ["SALESFORCE_TEST_INSTANCE_URL"].rstrip("/")
    return SalesforceSource(
        mode="live", instance_url=instance, connector_instance_url=instance,
        token_url=f"{SALESFORCE_TEST_LOGIN_URL}/services/oauth2/token",
        client_id=os.environ["SALESFORCE_TEST_CLIENT_ID"], client_secret=os.environ["SALESFORCE_TEST_CLIENT_SECRET"],
        refresh_token=os.environ["SALESFORCE_TEST_REFRESH_TOKEN"])


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def salesforce_users(salesforce_source: SalesforceSource, users_client: Any,
                           graph_provider: GraphProviderProtocol) -> dict[str, str]:
    """PipesHub users for the simulated org's people (email -> user id)."""
    if not salesforce_source.simulated:
        return {}
    users = {email(k): await ensure_active_user(users_client, graph_provider, email(k)) for k in SIM_USERS}
    users[salesforce_source.admin_email] = await ensure_active_user(
        users_client, graph_provider, salesforce_source.admin_email)
    return users


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def salesforce_connector(
    salesforce_source: SalesforceSource,
    salesforce_users: dict[str, str],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[str]:
    instance = pipeshub_client.create_connector(
        connector_type="Salesforce",
        instance_name=f"salesforce-it-{uuid.uuid4().hex[:10]}",
        scope="team",
        auth_type="OAUTH",
        config={"auth": {"instance_url": salesforce_source.connector_instance_url,
                         "clientId": salesforce_source.client_id,
                         "clientSecret": salesforce_source.client_secret}},
    )
    connector_id = instance.connector_id
    assert connector_id, "PipesHub did not return a Salesforce connector id"
    env = {"SALESFORCE_IT_REFRESH_TOKEN": salesforce_source.refresh_token,
           "SALESFORCE_IT_CLIENT_ID": salesforce_source.client_id,
           "SALESFORCE_IT_CLIENT_SECRET": salesforce_source.client_secret}
    os.environ.update(env)
    try:
        try:
            # A file of this run's own: the shared .env.refresh_tokens would override the
            # token just issued with one a former run rotated away.
            await authenticate_connector_with_refresh_token(
                connector_id=connector_id,
                refresh_token_env_var="SALESFORCE_IT_REFRESH_TOKEN",
                token_url=salesforce_source.token_url,
                client_id_env_var="SALESFORCE_IT_CLIENT_ID",
                client_secret_env_var="SALESFORCE_IT_CLIENT_SECRET",
                env_file=tmp_path_factory.mktemp("salesforce-tokens") / ".env.refresh_tokens",
            )
        except Exception as e:  # noqa: BLE001 - reported as the suite's set-up failure
            pytest.fail(f"Could not authenticate the Salesforce connector: {e}")
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=SYNC_TIMEOUT_SEC)
        yield connector_id
    finally:
        for key in env:
            os.environ.pop(key, None)
        try:
            pipeshub_client.toggle_sync(connector_id, enable=False)
        except Exception:  # noqa: BLE001 - teardown carries on
            logger.exception("Could not disable Salesforce test connector %s", connector_id)
        pipeshub_client.delete_connector(connector_id)
        await graph_provider.assert_all_records_cleaned(connector_id, timeout=300)
        if salesforce_source.sim is not None:
            gaps = salesforce_source.sim.unmodeled()
            assert not gaps, f"the simulator does not model what the connector used: {gaps}"
