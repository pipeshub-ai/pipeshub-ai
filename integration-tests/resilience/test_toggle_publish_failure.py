"""Turning a connector's sync on or off does not stick when its event cannot be published.

A toggle flips ``isActive`` in the graph and then publishes ``appEnabled`` or
``appDisabled`` to the ``entity-events`` topic. If the publish fails after the
flip, the connector would read as syncing with no sync behind it (or as stopped
while it still runs), so the product puts the flag back.

This test makes only that publish fail: in Redis the ``entity-events`` stream is
moved aside and a plain string takes its name, while Redis keeps serving
configuration and every other topic. Then, on a Web connector pointed at the
stack's fixture site:

  * turning sync on fails, the connector still reads as off, and nothing syncs;
  * once the topic is back, turning it on works and the site's pages sync;
  * turning sync off fails the same way, and the connector still reads as on;
  * once the topic is back, turning it off works;
  * the topic comes back as the stream it was, consumer groups included.

The scenario runs once, in a module fixture; each test below checks one thing
it saw, so a failure names the part that broke.

    RESILIENCE_TOGGLE_SETTLE_SEC  how long a failed enable gets to sync anything anyway (default 15)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio

from helper.compose_control import ComposeStack
from helper.connector_lifecycle import destructor, source_unavailable
from helper.fault_switches import REDIS_CLI, stream_off_script, stream_on_script
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_until_graph_condition
from helper.web_fixtures import WebFixtures
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("resilience")

pytestmark = [pytest.mark.resilience, pytest.mark.asyncio(loop_scope="session")]

TOPIC = "entity-events"
SETTLE = int(os.getenv("RESILIENCE_TOGGLE_SETTLE_SEC", "15"))
# The five pages under site/docs (see connectors/web/conftest.py).
START_PATH = "site/docs/"
SITE_PAGES = 5
SERVER_ERROR = 500


def _redis(compose: ComposeStack, command: str) -> str:
    return compose.exec("redis", ["sh", "-c", f"{REDIS_CLI} {command}"]).stdout.strip()


def _consumer_groups(compose: ComposeStack) -> list[str]:
    lines = _redis(compose, f"xinfo groups {TOPIC}").splitlines()
    return sorted(value for label, value in zip(lines, lines[1:]) if label == "name")


def _publish_off(compose: ComposeStack) -> None:
    compose.exec("redis", ["sh", "-c", stream_off_script(TOPIC)])


def _publish_on(compose: ComposeStack) -> None:
    compose.exec("redis", ["sh", "-c", stream_on_script(TOPIC)])


def _toggle(client: PipeshubClient, connector_id: str) -> dict[str, Any]:
    resp = client.request("POST", f"/api/v1/connectors/{connector_id}/toggle", json={"type": "sync"})
    return {"status": resp.status_code, "body": resp.text[:500]}


def _is_active(client: PipeshubClient, connector_id: str) -> bool:
    return bool(client.get_connector(connector_id).get("isActive"))


@dataclass
class ToggleOutage:
    """What each toggle did while the topic was off, and after it came back."""

    failed_enable: dict[str, Any] = field(default_factory=dict)
    active_after_failed_enable: bool | None = None
    records_after_failed_enable: int = 0
    enable_retry: dict[str, Any] = field(default_factory=dict)
    active_after_enable_retry: bool | None = None
    records_after_enable_retry: int = 0
    failed_disable: dict[str, Any] = field(default_factory=dict)
    active_after_failed_disable: bool | None = None
    disable_retry: dict[str, Any] = field(default_factory=dict)
    active_after_disable_retry: bool | None = None
    groups_before: list[str] = field(default_factory=list)
    groups_after: list[str] = field(default_factory=list)
    topic_type_after: str = ""


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def toggle_outage(
    compose: ComposeStack,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> ToggleOutage:
    """Toggle once each way with the topic off, then again with it back; every test here reads the result."""
    if _redis(compose, f"type {TOPIC}") != "stream":
        pytest.skip(f"{TOPIC} is not a Redis stream here, so the message broker is not Redis Streams")
    web_fixtures = WebFixtures()
    try:
        web_fixtures.check_available()
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        source_unavailable(f"web-fixtures service not reachable at {web_fixtures.test_url}: {exc}")
    web_fixtures.reset()

    start_url = web_fixtures.url_for_connector(START_PATH)
    result = ToggleOutage(groups_before=_consumer_groups(compose))
    connector_id = pipeshub_client.create_connector(
        connector_type="Web",
        instance_name=f"resilience-toggle-{uuid.uuid4().hex[:8]}",
        scope="team",
        config={
            "sync": {
                "url": start_url,
                "type": "recursive",
                "depth": 2,
                "max_pages": 50,
                "restrict_to_start_path": True,
                "follow_external": False,
            }
        },
    ).connector_id
    assert connector_id, "the Web connector was created without an id"

    async def _synced() -> bool:
        return await graph_provider.count_records(connector_id) >= SITE_PAGES

    try:
        _publish_off(compose)
        try:
            result.failed_enable = _toggle(pipeshub_client, connector_id)
        finally:
            _publish_on(compose)
        # An event that was published after all would start a sync within this window.
        await asyncio.sleep(SETTLE)
        result.active_after_failed_enable = _is_active(pipeshub_client, connector_id)
        result.records_after_failed_enable = await graph_provider.count_records(connector_id)

        if not result.active_after_failed_enable:
            result.enable_retry = _toggle(pipeshub_client, connector_id)
        result.active_after_enable_retry = _is_active(pipeshub_client, connector_id)
        if result.active_after_enable_retry:
            try:
                await wait_until_graph_condition(connector_id, check=_synced, description="first sync")
            except TimeoutError:
                logger.warning("Connector %s did not sync %d pages after it was enabled", connector_id, SITE_PAGES)
            result.records_after_enable_retry = await graph_provider.count_records(connector_id)

            _publish_off(compose)
            try:
                result.failed_disable = _toggle(pipeshub_client, connector_id)
            finally:
                _publish_on(compose)
            result.active_after_failed_disable = _is_active(pipeshub_client, connector_id)

            if result.active_after_failed_disable:
                result.disable_retry = _toggle(pipeshub_client, connector_id)
            result.active_after_disable_retry = _is_active(pipeshub_client, connector_id)

        result.topic_type_after = _redis(compose, f"type {TOPIC}")
        result.groups_after = _consumer_groups(compose)
        return result
    finally:
        # Safe when the topic is already back, and it runs even if a step above raised.
        _publish_on(compose)
        await destructor(
            web_fixtures,
            pipeshub_client,
            graph_provider,
            {"connector_id": connector_id, "resource_name": start_url},
            connector_type="Web",
        )


async def test_enabling_fails_when_its_event_cannot_be_published(toggle_outage: ToggleOutage) -> None:
    assert toggle_outage.failed_enable["status"] >= SERVER_ERROR, (
        f"with {TOPIC} unpublishable, turning sync on did not fail, so the fault did not take effect "
        f"or the failure was swallowed: {toggle_outage.failed_enable}"
    )


async def test_failed_enable_leaves_the_connector_off(toggle_outage: ToggleOutage) -> None:
    assert toggle_outage.active_after_failed_enable is False, (
        "after turning sync on failed, the connector still reads as active: it shows as syncing "
        "with no event published and no sync behind it"
    )


async def test_failed_enable_syncs_nothing(toggle_outage: ToggleOutage) -> None:
    assert toggle_outage.records_after_failed_enable == 0, (
        f"{toggle_outage.records_after_failed_enable} record(s) were synced within {SETTLE}s of an enable "
        "that was reported as failed"
    )


async def test_enabling_works_once_the_topic_is_back(toggle_outage: ToggleOutage) -> None:
    assert toggle_outage.enable_retry.get("status") == 200, (
        f"turning sync on again once {TOPIC} was back failed: {toggle_outage.enable_retry}"
    )
    assert toggle_outage.active_after_enable_retry is True, "the connector does not read as active after the retry"
    assert toggle_outage.records_after_enable_retry >= SITE_PAGES, (
        f"after the retry only {toggle_outage.records_after_enable_retry} of {SITE_PAGES} pages were synced, "
        "so the connector left behind by the failed enable did not start cleanly"
    )


async def test_disabling_fails_when_its_event_cannot_be_published(toggle_outage: ToggleOutage) -> None:
    assert toggle_outage.failed_disable, "sync was never turned on, so turning it off was not attempted"
    assert toggle_outage.failed_disable["status"] >= SERVER_ERROR, (
        f"with {TOPIC} unpublishable, turning sync off did not fail: {toggle_outage.failed_disable}"
    )


async def test_failed_disable_leaves_the_connector_on(toggle_outage: ToggleOutage) -> None:
    assert toggle_outage.active_after_failed_disable is True, (
        "after turning sync off failed, the connector reads as inactive: it shows as stopped "
        "while no event told the sync to stop"
    )


async def test_disabling_works_once_the_topic_is_back(toggle_outage: ToggleOutage) -> None:
    assert toggle_outage.disable_retry.get("status") == 200, (
        f"turning sync off again once {TOPIC} was back failed: {toggle_outage.disable_retry}"
    )
    assert toggle_outage.active_after_disable_retry is False, "the connector still reads as active after the retry"


async def test_the_topic_comes_back_as_it_was(toggle_outage: ToggleOutage) -> None:
    assert toggle_outage.topic_type_after == "stream", (
        f"{TOPIC} is a {toggle_outage.topic_type_after or 'missing key'} after the test, not a stream"
    )
    assert toggle_outage.groups_after == toggle_outage.groups_before, (
        f"{TOPIC} had consumer groups {toggle_outage.groups_before} before the test "
        f"and {toggle_outage.groups_after} after"
    )
