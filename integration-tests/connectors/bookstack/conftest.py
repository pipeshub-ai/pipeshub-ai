# pyright: ignore-file

"""BookStack connector fixtures.

The connector syncs from ``bookstack-source``, a BookStack server in the
integration stack (compose profile ``selfhosted-sources``), so it needs no
external account. See ``bookstack_source_helper.py`` for how the API token and
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
from connectors.bookstack.bookstack_seed import (
    DEFAULT_DB_NAME,
    DEFAULT_DB_PASSWORD,
    DEFAULT_DB_USER,
    ENGINEERING,
    HANDBOOK,
    LEAVE_POLICY,
    POLICIES,
    RUNBOOK,
    WELCOME,
)
from connectors.bookstack.bookstack_source_helper import (
    TOKEN_ID,
    TOKEN_SECRET,
    BookStackSourceHelper,
)
from helper.graph_provider import GraphProviderProtocol
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]


@pytest.fixture(scope="session")
def bookstack_source() -> BookStackSourceHelper:
    helper = BookStackSourceHelper(
        # The test process reaches the server and its database on published ports.
        base_url=os.getenv("BOOKSTACK_TEST_URL", "http://localhost:8094"),
        db_host=os.getenv("BOOKSTACK_DB_HOST", "localhost"),
        db_port=int(os.getenv("BOOKSTACK_DB_PORT", "3308")),
        db_user=os.getenv("BOOKSTACK_DB_USER", DEFAULT_DB_USER),
        db_password=os.getenv("BOOKSTACK_DB_PASSWORD", DEFAULT_DB_PASSWORD),
        db_name=os.getenv("BOOKSTACK_DB_NAME", DEFAULT_DB_NAME),
    )
    try:
        helper.ensure_token()
        helper.ping()
        helper.set_admin_email(os.getenv("PIPESHUB_TEST_USER_EMAIL", "pipeshub-it@example.com"))
    except Exception as exc:  # noqa: BLE001 — any failure means "not available"
        source_unavailable(f"BookStack not reachable at {helper.base_url}: {exc}")
    return helper


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def bookstack_connector(
    bookstack_source: BookStackSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    # Books left by an earlier, interrupted run would throw the counts off.
    bookstack_source.delete_books_named("it-")

    folder = f"it-{uuid.uuid4().hex[:8]}"
    handbook = bookstack_source.create_book(f"{folder} {HANDBOOK}")
    policies = bookstack_source.create_chapter(handbook, POLICIES)
    engineering = bookstack_source.create_book(f"{folder} {ENGINEERING}")
    page_ids = {
        LEAVE_POLICY[0]: bookstack_source.create_page(*LEAVE_POLICY, chapter_id=policies),
        WELCOME[0]: bookstack_source.create_page(*WELCOME, book_id=handbook),
        RUNBOOK[0]: bookstack_source.create_page(*RUNBOOK, book_id=engineering),
    }

    state: dict[str, Any] = {
        "resource_name": bookstack_source.base_url,
        "folder": folder,
        "books": {HANDBOOK: handbook, ENGINEERING: engineering},
        "chapters": {POLICIES: policies},
        "page_ids": page_ids,
        "seeded_pages": sorted(page_ids),
        "uploaded_count": len(page_ids),
    }
    # The connector runs inside the compose network and reaches the server by
    # service name.
    config = {
        "auth": {
            "base_url": os.getenv("BOOKSTACK_CONNECTOR_URL", "http://bookstack-source"),
            "token_id": TOKEN_ID,
            "token_secret": TOKEN_SECRET,
        }
    }
    await create_connector_and_await_sync(
        pipeshub_client,
        graph_provider,
        state,
        connector_type="BookStack",
        connector_name=f"bookstack-lifecycle-test-{uuid.uuid4().hex[:8]}",
        connector_config=config,
        expected_records=len(page_ids),
        # BookStack is registered for team scope only.
        scope="team",
        auth_type="API_TOKEN",
    )

    yield state

    await destructor(
        bookstack_source, pipeshub_client, graph_provider, state, connector_type="BookStack"
    )
