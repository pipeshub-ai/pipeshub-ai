# pyright: ignore-file

"""Zammad connector fixtures.

The connector syncs from ``zammad-railsserver``, a Zammad instance in the
integration stack (compose profile ``zammad-source``), so it needs no external
account. See ``zammad_source_helper.py`` for how the admin, the API token and
the content are set up.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import (
    create_connector_and_await_sync,
    destructor,
    source_unavailable,
)
from connectors.zammad.zammad_seed import (
    ATTACHMENT_BODY,
    ATTACHMENT_NAME,
    BILLING_GROUP,
    INVOICE,
    LAPTOP,
    PRINTER,
    SUPPORT_GROUP,
)
from connectors.zammad.zammad_source_helper import ZammadSourceHelper
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


@pytest.fixture(scope="session")
def zammad_source() -> ZammadSourceHelper:
    helper = ZammadSourceHelper(
        # The test process reaches Zammad on a published port.
        base_url=os.getenv("ZAMMAD_TEST_URL", "http://localhost:8096"),
        # Tickets are owned by the admin who files them, and the connector
        # grants access by email, so the admin must be the test user.
        admin_email=os.getenv("PIPESHUB_TEST_USER_EMAIL", "pipeshub-it@example.com"),
    )
    try:
        helper.ensure_admin_and_token()
        helper.ping()
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        source_unavailable(f"Zammad not reachable at {helper.base_url}: {exc}")
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def zammad_connector(
    zammad_source: ZammadSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    # Tickets left by an earlier, interrupted run would throw the counts off.
    zammad_source.delete_tickets_titled("it-")

    run = f"it-{uuid.uuid4().hex[:8]}"
    support = zammad_source.create_group(f"{run} {SUPPORT_GROUP}")
    billing = zammad_source.create_group(f"{run} {BILLING_GROUP}")

    titles = {
        LAPTOP[0]: f"{run} {LAPTOP[0]}",
        PRINTER[0]: f"{run} {PRINTER[0]}",
        INVOICE[0]: f"{run} {INVOICE[0]}",
    }
    ticket_ids = {
        LAPTOP[0]: zammad_source.create_ticket(titles[LAPTOP[0]], LAPTOP[1], support),
        PRINTER[0]: zammad_source.create_ticket(titles[PRINTER[0]], PRINTER[1], support),
        INVOICE[0]: zammad_source.create_ticket(titles[INVOICE[0]], INVOICE[1], billing),
    }
    # An attachment on the laptop ticket, to check the connector streams it.
    zammad_source.add_article(
        ticket_ids[LAPTOP[0]],
        "Attaching the boot log.",
        attachment=(ATTACHMENT_NAME, ATTACHMENT_BODY),
    )
    # The connector reads tickets through Zammad's search, so nothing can be
    # synced until Elasticsearch has indexed them.
    zammad_source.wait_until_searchable(list(titles.values()))

    state: dict[str, Any] = {
        "resource_name": zammad_source.base_url,
        "folder": run,
        "groups": {SUPPORT_GROUP: support, BILLING_GROUP: billing},
        "ticket_ids": ticket_ids,
        "titles": titles,
        "seeded_tickets": sorted(titles.values()),
        "uploaded_count": len(ticket_ids),
    }
    # The connector runs inside the compose network and reaches Zammad by
    # service name.
    config = {
        "auth": {
            "baseUrl": os.getenv("ZAMMAD_CONNECTOR_URL", "http://zammad-railsserver:3000"),
            "token": zammad_source.token,
        }
    }
    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="Zammad",
        connector_name=f"zammad-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        # The tickets only. The attachment on one of them is a record too, but
        # waiting for it here would fail every test if attachment handling
        # changed; TC-ATTACH-001 checks it on its own and says what is missing.
        expected_records=len(ticket_ids),
        # Zammad is registered for team scope only.
        scope="team",
        auth_type="API_TOKEN",
    )

    yield state

    await destructor(
        zammad_source, pipeshub_client, graph_provider, state, connector_type="Zammad"
    )
