# pyright: ignore-file

"""Confluence syncs that go wrong, and syncs that must not complain, against the connectors simulator.

These run on the pack's space (``CONFLUENCE_TEST_SPACE_KEY``) with its real mix of content:
restrictions, folders, comments and embedded images. Each fault is switched on with the
simulator's fault rules around one sync and cleared in ``finally``; the checks are those of the
simulator harness (``harness/connector_in_process.py --failure-checks``) made against a running
stack:

- a clean sync sends no notification;
- a throttled sync keeps every record and reports nothing;
- a space list that cannot be read sends one notification and removes nothing, even on a full sync;
- a page listing cut off part way sends one notification, and the next sync reads the rest;
- the hourly quota running out stops the sync with one notification, and the next sync after
  the reset reads the rest;
- notifications go to the connector's creator and to the org admins, and to nobody else.

The connector of each failure check is created for it, so its first sync is the one the fault
hits. ``confluence_connector`` (conftest) is the module's long-lived connector for the rest.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from urllib.parse import urlsplit

import pytest

from connectors.confluence import confluence_sim as sim
from connectors.confluence.confluence_v1_test_utils import (
    SpaceContentSnapshot,
    assert_api_content_matches_graph,
    fetch_space_content_snapshot,
)
from connectors.confluence.constants import connector_base_url
from helper.graph_provider_utils import wait_for_sync_completion
from helper.second_user import create_second_user, delete_second_user
from helper.second_user import log_in as log_in_user
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("confluence-sim-faults")

pytestmark = [pytest.mark.integration, pytest.mark.confluence, pytest.mark.asyncio(loop_scope="session")]

CONTENT_SEARCH = "^/wiki/rest/api/content/search$"


@pytest.fixture(scope="module", autouse=True)
def _simulator_only() -> None:
    sim.require_simulator()


def _inbox(client: PipeshubClient, name: str) -> sim.Inbox:
    return sim.Inbox(client.base_url, name, email=os.getenv("PIPESHUB_TEST_USER_EMAIL"),
                     password=os.getenv("PIPESHUB_TEST_USER_PASSWORD"))


async def _missing(snapshot: SpaceContentSnapshot, graph_provider: Any, connector_id: str) -> list[str]:
    return [item.id for item in snapshot.all_content
            if await graph_provider.get_record_by_external_id(connector_id, item.id) is None]


@asynccontextmanager
async def _fresh_connector(client: PipeshubClient, graph_provider: Any) -> AsyncIterator[tuple[str, str]]:
    """A connector on the pack space that has never synced; deleted after the test."""
    space_key = os.environ["CONFLUENCE_TEST_SPACE_KEY"].strip()
    name = f"confluence-sim-fault-{uuid.uuid4().hex[:8]}"
    instance = client.create_connector(
        connector_type="Confluence", instance_name=name, scope="team", auth_type="API_TOKEN",
        config={
            "auth": {"authType": "API_TOKEN", "baseUrl": connector_base_url(),
                     "email": os.getenv("CONFLUENCE_TEST_EMAIL"), "apiToken": os.getenv("CONFLUENCE_TEST_API_TOKEN")},
            "filters": {"sync": {"values": {"space_keys": {"operator": "in", "type": "list", "value": [space_key]}}}},
        },
    )
    connector_id = instance.connector_id
    assert connector_id
    try:
        yield connector_id, name
    finally:
        try:
            client.toggle_sync(connector_id, enable=False)
            client.delete_connector(connector_id)
            client.wait(25)
            await graph_provider.assert_all_records_cleaned(
                connector_id, timeout=int(os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", "300")))
        except Exception as e:  # noqa: BLE001 - teardown must not mask the result
            logger.warning("TEARDOWN: delete/clean failed for %s: %s", connector_id, e)


async def _first_sync(client: PipeshubClient, graph_provider: Any, connector_id: str) -> None:
    client.toggle_sync(connector_id, enable=True)
    await wait_for_sync_completion(client, graph_provider, connector_id, timeout=sim.SYNC_TIMEOUT_SEC)


@pytest.mark.order(1)
async def test_clean_full_and_incremental_syncs_send_no_notice(
    confluence_connector: dict[str, Any], pipeshub_client: PipeshubClient, graph_provider: Any,
) -> None:
    """9a: the fixture's clean full sync, and a clean incremental after it, notify nobody."""
    cid = confluence_connector["connector_id"]
    inbox = _inbox(pipeshub_client, confluence_connector["connector_name"])
    await inbox.expect(0, "clean full sync")
    await sim.run_sync(pipeshub_client, graph_provider, cid)
    await inbox.expect(0, "clean incremental sync")


async def test_throttled_sync_keeps_every_record(
    confluence_connector: dict[str, Any], pipeshub_client: PipeshubClient, graph_provider: Any,
) -> None:
    """Every Confluence call rate limited: the sync waits out each Retry-After and loses nothing."""
    snapshot = confluence_connector["content_snapshot"]
    throttle = {"match": {"path_regex": "^/wiki/"}, "trigger": {"probability": 1.0},
                "effect": {"type": "rate_limit", "capacity": 20, "refill_per_s": 8}}
    async with _fresh_connector(pipeshub_client, graph_provider) as (cid, name):
        inbox = _inbox(pipeshub_client, name)
        with sim.faults(throttle):
            await _first_sync(pipeshub_client, graph_provider, cid)
            throttled = sum(1 for f in sim.faults_fired() if f["effect"] == "rate_limit")
        assert throttled > 0, "no request was throttled; the check proved nothing"
        assert not await _missing(snapshot, graph_provider, cid)
        await inbox.expect(0, "throttled sync")


async def test_failed_space_list_notifies_creator_and_admins_and_removes_nothing(
    confluence_connector: dict[str, Any], pipeshub_client: PipeshubClient, graph_provider: Any,
) -> None:
    """A full sync whose space list cannot be read: one notice, to the creator and every org admin
    but not to other members, and every stored record stays."""
    cid = confluence_connector["connector_id"]
    name = confluence_connector["connector_name"]
    creator = _inbox(pipeshub_client, name)
    creator.mark_seen()
    before = await graph_provider.count_records(cid)
    admin = create_second_user(pipeshub_client)
    member = create_second_user(pipeshub_client)
    try:
        resp = pipeshub_client.request("PUT", f"/api/v1/users/{admin.user_id}", json={"role": "admin"})
        assert resp.status_code < 300, f"could not make the second user an admin: {resp.status_code} {resp.text[:200]}"
        # A role change ends the user's sessions, so the new admin logs in again.
        admin_token = log_in_user(pipeshub_client.base_url, admin.email, pipeshub_client.timeout_seconds)
        admin_inbox = sim.Inbox(pipeshub_client.base_url, name, token=admin_token)
        member_inbox = sim.Inbox(pipeshub_client.base_url, name, token=member.token)

        no_spaces = {"match": {"method": "GET", "path_regex": "^/wiki/api/v2/spaces$"},
                     "effect": {"type": "status", "code": 503}}
        with sim.faults(no_spaces):
            await sim.run_sync(pipeshub_client, graph_provider, cid, full=True)
        notices = await creator.expect(1, "full sync with the space list failing")
        assert "space list" in notices[0]["message"], notices[0]["message"]
        assert [n["title"] for n in await admin_inbox.expect(1, "the other org admin")] == [notices[0]["title"]]
        await member_inbox.expect(0, "a member who neither created the connector nor administers the org")

        assert await graph_provider.count_records(cid) == before, "the failed full sync removed records"
        assert not await _missing(confluence_connector["content_snapshot"], graph_provider, cid)
        await sim.run_sync(pipeshub_client, graph_provider, cid)
        await creator.expect(0, "clean incremental after the failure")
    finally:
        pipeshub_client.request("PUT", f"/api/v1/users/{admin.user_id}", json={"role": "member"})
        delete_second_user(pipeshub_client, admin)
        delete_second_user(pipeshub_client, member)


async def test_cut_off_listing_resumes_to_every_record(
    confluence_connector: dict[str, Any], pipeshub_client: PipeshubClient, graph_provider: Any,
) -> None:
    """The page listing fails after two pages of results: one notice; the next sync reads the rest."""
    snapshot = confluence_connector["content_snapshot"]
    cut_off = {"match": {"path_regex": CONTENT_SEARCH}, "trigger": {"after_n": 2},
               "effect": {"type": "status", "code": 503}}
    async with _fresh_connector(pipeshub_client, graph_provider) as (cid, name):
        inbox = _inbox(pipeshub_client, name)
        with sim.faults(cut_off):
            await _first_sync(pipeshub_client, graph_provider, cid)
            assert sim.faults_fired(), "the listing was never cut off"
        assert await _missing(snapshot, graph_provider, cid), "every record arrived; the cut-off proved nothing"
        await inbox.expect(1, "sync with the listing cut off")

        await sim.run_sync(pipeshub_client, graph_provider, cid)
        assert not await _missing(snapshot, graph_provider, cid), "the next sync did not read the rest"
        await inbox.expect(0, "the sync that resumed the listing")


async def test_spent_quota_stops_the_sync_and_the_next_one_resumes(
    confluence_connector: dict[str, Any], pipeshub_client: PipeshubClient, graph_provider: Any,
) -> None:
    """8a: a 429 whose Retry-After outlasts the retry policy is the hourly quota: the sync stops with
    one notice instead of waiting, and the first sync after the reset reads everything."""
    snapshot = confluence_connector["content_snapshot"]
    reset_after_s = 65  # over the policy's 60 s ceiling, so it reads as the quota
    quota = {"match": {"path_regex": CONTENT_SEARCH}, "trigger": {"after_n": 2},
             "effect": {"type": "rate_limit", "capacity": 1, "refill_per_s": 1 / reset_after_s}}
    async with _fresh_connector(pipeshub_client, graph_provider) as (cid, name):
        inbox = _inbox(pipeshub_client, name)
        with sim.faults(quota):
            await _first_sync(pipeshub_client, graph_provider, cid)
            assert any(f["effect"] == "rate_limit" for f in sim.faults_fired()), "the quota never ran out"
        stopped_at = time.time()
        assert await _missing(snapshot, graph_provider, cid), "every record arrived; the quota stop proved nothing"
        notices = await inbox.expect(1, "sync that ran out of quota")
        assert "quota" in notices[0]["title"].lower(), notices[0]["title"]

        await sim.poll(lambda: _after(stopped_at + reset_after_s + 5), "the quota reset", timeout=reset_after_s + 30)
        await sim.run_sync(pipeshub_client, graph_provider, cid)
        assert not await _missing(snapshot, graph_provider, cid), "the sync after the reset did not read the rest"
        await inbox.expect(0, "the sync after the quota reset")


async def _after(moment: float) -> bool:
    return time.time() >= moment


def _download(att: dict[str, Any]) -> bytes:
    """An attachment's bytes. The download redirects to the media host, which in CI is a name only
    the compose network resolves, so the redirect is followed on the published port."""
    resp = sim.call("GET", f"/wiki{att['_links']['download']}", allow_redirects=False)
    if resp.is_redirect:
        location = urlsplit(resp.headers["Location"])
        resp = sim.call("GET", location.path + (f"?{location.query}" if location.query else ""))
    assert resp.status_code == 200, f"download {att['id']}: HTTP {resp.status_code}"
    return resp.content


async def _embedded_image(
    snapshot: SpaceContentSnapshot, graph_provider: Any, cid: str,
) -> tuple[str, dict, bytes] | None:
    """A page image inlined into its page's body, so it has no attachment record of its own, with its
    bytes to put back. Pages with a restriction (the sync account may not edit them) and images the
    simulator cannot serve are passed over: they could not be replaced and restored."""
    for item in snapshot.pages:
        if any(r["restrictions"]["user"]["size"] or r["restrictions"]["group"]["size"]
               for r in sim.ok("GET", f"/wiki/rest/api/content/{item.id}/restriction")["results"]):
            continue
        for att in sim.attachments(item.id):
            media_type = (att.get("extensions") or {}).get("mediaType", "")
            if not media_type.startswith("image/") or await sim.find_record(
                    graph_provider, cid, sim.attachment_external_ids(att["id"])) is not None:
                continue
            try:
                return item.id, att, _download(att)
            except AssertionError as e:
                logger.info("passing over image %s: %s", att["id"], e)
    return None


async def test_oversized_embedded_image_is_kept_as_an_attachment(
    confluence_connector: dict[str, Any], pipeshub_client: PipeshubClient, graph_provider: Any,
) -> None:
    """12: an embedded image replaced by one over 10 MiB is too big to inline; it becomes an
    attachment record instead of being lost. The image's original bytes are put back after."""
    cid = confluence_connector["connector_id"]
    inbox = _inbox(pipeshub_client, confluence_connector["connector_name"])
    inbox.mark_seen()
    found = await _embedded_image(confluence_connector["content_snapshot"], graph_provider, cid)
    if found is None:
        pytest.skip(f"no page of space {confluence_connector['space_key']} embeds an image")
    page_id, att, original = found
    media_type = att["extensions"]["mediaType"]
    try:
        big = b"\x89PNG\r\n\x1a\n" + bytes(11 * 1024 * 1024)
        sim.upload(page_id, att["title"], big, media_type, new_version=True)
        await sim.run_sync(pipeshub_client, graph_provider, cid)
        rec = await sim.find_record(graph_provider, cid, sim.attachment_external_ids(att["id"]))
        assert rec is not None, "the oversized image is neither inlined nor stored as an attachment"
        assert rec.parent_external_record_id == page_id
        await inbox.expect(0, "sync with an oversized embedded image")
    finally:
        sim.upload(page_id, att["title"], original, media_type, new_version=True)


@pytest.mark.order(-1)
async def test_final_clean_incremental_sends_no_notice(
    confluence_connector: dict[str, Any], confluence_datasource: Any, pipeshub_client: PipeshubClient,
    graph_provider: Any, connector_assertions: Any,
) -> None:
    """9b: after every fault is cleared, an incremental sync is clean and the space is whole."""
    cid = confluence_connector["connector_id"]
    inbox = _inbox(pipeshub_client, confluence_connector["connector_name"])
    inbox.mark_seen()
    await sim.run_sync(pipeshub_client, graph_provider, cid)
    await inbox.expect(0, "final incremental sync")
    snapshot = await fetch_space_content_snapshot(confluence_datasource, confluence_connector["space_key"])
    await assert_api_content_matches_graph(
        snapshot, confluence_datasource, graph_provider, connector_assertions, cid, phase="final incremental")
