# pyright: ignore-file

"""ServiceNow connector regressions: each test pins one behaviour a fixed bug broke, end to end.

The connectors here run with manual indexing (records sync, nothing is indexed): these checks are
about what is synced and kept, and one KB holds more than a page of articles.

  TC-SYNC-12   more than one page of articles, and of users, is synced (paging by X-Total-Count)
  TC-ATT-02    an article with more than 100 attachments keeps every one
  regression   only current versions are records: expired, inactive, in an inactive KB, outdated
  TC-RES-05    an expired access token is refreshed once and the sync carries on
  TC-RES-06    a revoked refresh token stops the sync and deletes nothing
  TC-LIFE-04   a connector deleted while it syncs leaves nothing behind
  TC-SEC-01    an instance URL that is not https is refused
  auth types   basic / API key / client credentials, once the connector offers them (skip until then)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.servicenow.constants import (
    ENV_INSTANCE_URL,
    ENV_PASSWORD,
    ENV_USERNAME,
    SERVICENOW_CONNECTOR_TYPE,
    SYNC_TIMEOUT,
    connector_offers_auth,
)
from connectors.servicenow.servicenow_helpers import (
    Persona,
    delete_connector,
    inject_fresh_token,
    kb_filter,
    merge_filters,
    poll,
    record,
    servicenow_connector,
    sync,
    wait_absent,
    wait_present,
)
from connectors.servicenow.servicenow_seed import ServiceNowSeed, readable_numbers
from helper.clients.connectors_client import ConnectorOAuthConfigsClient
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from helper.oauth_token_helper import inject_connector_credentials

logger = logging.getLogger("servicenow-regression")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.servicenow,
    pytest.mark.asyncio(loop_scope="session"),
]

PAGE_ARTICLES = 105
MANY_ATTACHMENTS = 101
MANUAL = {"indexing": {"values": {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": True}}}}


@dataclass
class World:
    seed: ServiceNowSeed
    connector_id: str
    page_ids: list[str]


def _build(seed: ServiceNowSeed, p: dict[str, Persona]) -> list[str]:
    uc = seed.criterion("uc-readers", group=seed.group("readers", [p["hr"].sys_id]))
    r = seed.kb("r", read=[uc])
    off = seed.kb("off", read=[uc])
    seed.article("current", r)
    seed.article("expired", r)
    seed.article("inactive", r)
    seed.article("versioned", r)
    seed.article("in-off-kb", off)
    many = seed.article("many-files", r)
    for i in range(MANY_ATTACHMENTS):
        seed.attach(many, f"phit-file-{i:03d}.txt", f"attachment {i}".encode(), "text/plain")
    seed.publish_pending()
    seed.force(seed.articles["expired"].sys_id, valid_to="2020-01-01")
    seed.force(seed.articles["inactive"].sys_id, active="false")
    seed.checkout(seed.articles["versioned"], text="<p>second version</p>")
    seed.rest.patch("kb_knowledge_base", off, {"active": "false"})
    return seed.bulk_articles(seed.kb("page", read=[uc]), PAGE_ARTICLES)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def world(sn_seed: ServiceNowSeed, sn_personas: dict[str, Persona], sn_oauth_config: str,
                pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol) -> AsyncIterator[World]:
    page_ids = await asyncio.to_thread(_build, sn_seed, sn_personas)
    async with servicenow_connector(
        pipeshub_client, graph_provider, sn_oauth_config, name="servicenow-reg-it",
        filters=merge_filters(kb_filter(sn_seed.kbs.values()), MANUAL), min_records=PAGE_ARTICLES,
    ) as connector_id:
        yield World(sn_seed, connector_id, page_ids)


async def test_tc_sync_12_more_than_one_page_of_articles_and_users(world: World,
                                                                    graph_provider: GraphProviderProtocol) -> None:
    expected = readable_numbers(world.seed.rest, [world.seed.kbs["page"]])
    assert len(expected) == PAGE_ARTICLES
    rows = await graph_provider.fetch_records_by_type(world.connector_id, "WEBPAGE")
    missing = expected - {r.get("externalRecordId") for r in rows}
    assert not missing, f"{len(missing)} of {PAGE_ARTICLES} articles were not synced: {sorted(missing)[:5]}"
    users = world.seed.rest.count("sys_user", "active=true^emailISNOTEMPTY")
    linked = await graph_provider.count_user_app_relation_edges(world.connector_id)
    assert linked >= min(users, 101), f"{linked} users synced; the instance has {users} active users with an email"


async def test_tc_att_02_more_than_100_attachments_on_one_article(world: World,
                                                                 graph_provider: GraphProviderProtocol) -> None:
    art = world.seed.articles["many-files"]

    async def _all() -> bool:
        rows = await graph_provider.fetch_records_by_type(world.connector_id, "FILE")
        return sum(1 for r in rows if r.get("externalParentId") == art.number) == MANY_ATTACHMENTS
    await poll(_all, f"{art.number} should have {MANY_ATTACHMENTS} file records", 300)


@pytest.mark.parametrize("key", ["expired", "inactive", "in-off-kb"])
async def test_reg_only_what_servicenow_search_shows_is_synced(world: World, graph_provider: GraphProviderProtocol,
                                                               key: str) -> None:
    await wait_present(graph_provider, world.connector_id, world.seed.articles["current"].number)
    assert await record(graph_provider, world.connector_id, world.seed.articles[key].number) is None
    assert await graph_provider.get_record_group_by_external_id(world.connector_id, world.seed.kbs["off"]) is None


async def test_reg_an_outdated_version_is_not_a_second_record(world: World, graph_provider: GraphProviderProtocol) -> None:
    number = world.seed.articles["versioned"].number
    await wait_present(graph_provider, world.connector_id, number)
    rows = await graph_provider.fetch_records_by_type(world.connector_id, "WEBPAGE")
    assert sum(1 for r in rows if r.get("externalRecordId") == number) == 1
    assert sum(1 for r in rows if r.get("recordName") == world.seed.articles["versioned"].title) == 1


async def test_tc_res_05_expired_access_token_is_refreshed_and_the_sync_completes(
    world: World, sn_oauth_config: str, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
) -> None:
    """The stored access token is refused (401); the connector refreshes it once through the OAuth app."""
    scope = merge_filters(kb_filter([world.seed.kbs["r"]]), MANUAL)
    async with servicenow_connector(pipeshub_client, graph_provider, sn_oauth_config, name="servicenow-res05-it",
                                    filters=scope, min_records=2,
                                    credentials=(f"expired-{uuid.uuid4().hex}", None)) as cid:
        await wait_present(graph_provider, cid, world.seed.articles["current"].number)


async def test_tc_res_06_revoked_refresh_token_deletes_nothing(
    world: World, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
) -> None:
    cid = world.connector_id
    before = await graph_provider.count_records(cid)
    await inject_connector_credentials(cid, f"revoked-{uuid.uuid4().hex}", f"revoked-{uuid.uuid4().hex}")
    try:
        pipeshub_client.toggle_sync(cid, enable=False)
        pipeshub_client.toggle_sync(cid, enable=True)
    except Exception as e:  # noqa: BLE001 - refusing to start with dead credentials is a fine answer too
        logger.info("Enabling with revoked credentials was refused: %s", type(e).__name__)
    await asyncio.sleep(60)
    await wait_for_sync_completion(pipeshub_client, graph_provider, cid, timeout=SYNC_TIMEOUT)
    assert await graph_provider.count_records(cid) == before, "a sync that could not sign in deleted records"
    await inject_fresh_token(cid)
    await sync(pipeshub_client, graph_provider, cid)
    assert await graph_provider.count_records(cid) == before


async def test_tc_life_04_delete_while_syncing_leaves_nothing(
    world: World, sn_oauth_config: str, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
) -> None:
    async with servicenow_connector(pipeshub_client, graph_provider, sn_oauth_config, name="servicenow-life04-it",
                                    filters=merge_filters(kb_filter([world.seed.kbs["page"]]), MANUAL),
                                    enable=False) as cid:
        pipeshub_client.toggle_sync(cid, enable=True)
        await poll(lambda: graph_provider.count_records(cid), "the sync should have started writing records", 300)
    # servicenow_connector deleted it and checked the graph is clean.


async def test_tc_sec_01_an_http_instance_url_is_refused(pipeshub_client: PipeshubClient,
                                                        graph_provider: GraphProviderProtocol) -> None:
    host = os.environ[ENV_INSTANCE_URL].strip().rstrip("/").replace("https://", "http://", 1)
    api = ConnectorOAuthConfigsClient(pipeshub_client)
    resp = api.create_config(SERVICENOW_CONNECTOR_TYPE, {
        "oauthInstanceName": f"servicenow-it-http-{uuid.uuid4().hex[:8]}",
        "config": {"instanceUrl": host, "authorizeUrl": f"{host}/oauth_auth.do", "tokenUrl": f"{host}/oauth_token.do",
                   "clientId": "phit-not-used", "clientSecret": "phit-not-used"},
    })
    if resp.status_code >= 400:
        return  # refused at save
    config_id = str((resp.json().get("oauthConfig") or {}).get("_id") or "")
    instance = pipeshub_client.create_connector(
        connector_type=SERVICENOW_CONNECTOR_TYPE, instance_name=f"servicenow-it-http-{uuid.uuid4().hex[:6]}",
        scope="team", auth_type="OAUTH", config={"auth": {"oauthConfigId": config_id}})
    refused = False
    try:
        await inject_fresh_token(instance.connector_id)
        try:
            pipeshub_client.toggle_sync(instance.connector_id, enable=True)
        except Exception:  # noqa: BLE001 - a refusal to enable is what this asks for
            refused = True
        if not refused:
            await asyncio.sleep(30)
            refused = not pipeshub_client.get_connector(instance.connector_id).get("isAuthenticated", False)
    finally:
        await delete_connector(pipeshub_client, graph_provider, instance.connector_id)
        api.delete_config(SERVICENOW_CONNECTOR_TYPE, config_id)
    assert refused, "a connector pointed at an http:// instance URL was accepted"


@pytest.mark.parametrize("auth_type", ["BASIC_AUTH", "API_TOKEN", "OAUTH_CLIENT_CREDENTIALS"])
async def test_auth_types_sync_the_same_knowledge_base(world: World, auth_type: str, pipeshub_client: PipeshubClient,
                                                       graph_provider: GraphProviderProtocol) -> None:
    """Each sign-in method the connector offers syncs the same articles (planned field names)."""
    if not connector_offers_auth(auth_type):
        pytest.skip(f"the ServiceNow connector does not offer {auth_type} yet")
    base = os.environ[ENV_INSTANCE_URL].strip().rstrip("/")
    if auth_type == "BASIC_AUTH":
        auth = {"instanceUrl": base, "username": os.environ[ENV_USERNAME], "password": os.environ[ENV_PASSWORD]}
    elif auth_type == "API_TOKEN":
        if not os.getenv("SERVICENOW_TEST_API_KEY"):
            pytest.skip("SERVICENOW_TEST_API_KEY is not set (an instance REST API key)")
        auth = {"instanceUrl": base, "apiKey": os.environ["SERVICENOW_TEST_API_KEY"]}
    else:
        if os.getenv("SERVICENOW_TEST_CLIENT_CREDENTIALS", "") != "1":
            pytest.skip("the client-credentials grant must be enabled on the instance's OAuth app "
                        "(set SERVICENOW_TEST_CLIENT_CREDENTIALS=1 once it is)")
        auth = {"instanceUrl": base, "clientId": os.environ["SERVICENOW_TEST_CLIENT_ID"],
                "clientSecret": os.environ["SERVICENOW_TEST_CLIENT_SECRET"]}
    instance = pipeshub_client.create_connector(
        connector_type=SERVICENOW_CONNECTOR_TYPE, instance_name=f"servicenow-it-{auth_type.lower()}-{uuid.uuid4().hex[:6]}",
        scope="team", auth_type=auth_type,
        config={"auth": auth, "filters": merge_filters(kb_filter([world.seed.kbs["r"]]), MANUAL)})
    try:
        pipeshub_client.toggle_sync(instance.connector_id, enable=True)
        await wait_for_sync_completion(pipeshub_client, graph_provider, instance.connector_id, timeout=SYNC_TIMEOUT,
                                       min_records=2)
        await wait_present(graph_provider, instance.connector_id, world.seed.articles["current"].number)
        await wait_absent(graph_provider, instance.connector_id, world.seed.articles["expired"].number)
    finally:
        await delete_connector(pipeshub_client, graph_provider, instance.connector_id)
