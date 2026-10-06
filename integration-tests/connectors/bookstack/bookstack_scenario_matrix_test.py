# pyright: ignore-file

"""BookStack in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are pages on the ``bookstack-source`` container, written through its REST
API. The connector reads changes from BookStack's audit log, so every scenario
after the first full sync exercises that incremental path.

Sharing: BookStack grants page access to roles. The second person is a
BookStack account with the PipesHub second user's email in a role that has no
system permissions, so it sees a page only when the page is shared with that
role.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import create_connector_and_await_sync, destructor

from connectors.bookstack.bookstack_source_helper import (
    TOKEN_ID,
    TOKEN_SECRET,
    BookStackSourceHelper,
)
from connectors.scenario_matrix import (
    ConnectorScenarioMatrix,
    Role,
    ScenarioAdapter,
    SourceItem,
)
from helper.graph_provider import GraphProviderProtocol
from helper.second_user import SecondUser
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

SHAREE_PASSWORD = "Pipeshub-matrix-2026!"


class BookStackAdapter(ScenarioAdapter):
    source = "BookStack"

    def __init__(
        self, *, bookstack: BookStackSourceHelper, books: dict[str, int], role_id: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.bookstack = bookstack
        self.books = books
        self.role_id = role_id

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        book = self.books["filtered" if role is Role.FILTERED else "main"]
        name = f"{role.value}-{token}"
        page_id = self.bookstack.create_page(name, text, book_id=book)
        return SourceItem(
            role=role, key=str(page_id), record_name=name, text=text, token=token,
            external_id=f"page/{page_id}",
        )

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        self.bookstack.update_page(int(item.key), text)
        return SourceItem(
            role=item.role, key=item.key, record_name=item.record_name, text=text,
            token=token, external_id=item.external_id,
        )

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        name = f"renamed-{item.token}"
        self.bookstack.rename_page(int(item.key), name)
        return SourceItem(
            role=item.role, key=item.key, record_name=name, text=item.text,
            token=item.token, external_id=item.external_id,
        )

    async def change_permission(self, item: SourceItem, sharee: SecondUser, *, grant: bool) -> None:
        self.bookstack.set_page_role_view(int(item.key), self.role_id, view=grant)

    async def delete_item(self, item: SourceItem) -> None:
        self.bookstack.delete_page(int(item.key))

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        return {
            "sync": {
                "values": {
                    "book_ids": {
                        "operator": "not_in",
                        "value": [str(self.books["filtered"])],
                        "type": "list",
                    }
                }
            }
        }


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    bookstack_source: BookStackSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    second_user: SecondUser,
) -> AsyncGenerator[BookStackAdapter, None]:
    # Books left by an earlier, interrupted run would become records here too.
    bookstack_source.delete_books_named("it-")

    run = f"it-mx-{uuid.uuid4().hex[:8]}"
    books = {
        "main": bookstack_source.create_book(f"{run} Main"),
        "filtered": bookstack_source.create_book(f"{run} Filtered"),
    }
    bookstack_source.create_page("seed", "Scenario matrix seed page.", book_id=books["main"])
    # Created before the connector, so its first full sync already knows the
    # role and its member; the scenarios only change what the role may see.
    role_id = bookstack_source.create_role(f"{run} sharee")
    user_id = bookstack_source.create_user(
        f"{run} sharee", second_user.email, role_id, SHAREE_PASSWORD
    )

    state: dict[str, Any] = {"resource_name": bookstack_source.base_url, "folder": run}
    try:
        await create_connector_and_await_sync(
            pipeshub_client,
            graph_provider,
            state,
            connector_type="BookStack",
            connector_name=f"bookstack-matrix-{uuid.uuid4().hex[:8]}",
            connector_config={
                "auth": {
                    "base_url": os.getenv("BOOKSTACK_CONNECTOR_URL", "http://bookstack-source"),
                    "token_id": TOKEN_ID,
                    "token_secret": TOKEN_SECRET,
                }
            },
            expected_records=1,
            scope="team",
            auth_type="API_TOKEN",
        )
        yield BookStackAdapter(
            bookstack=bookstack_source,
            books=books,
            role_id=role_id,
            client=pipeshub_client,
            graph=graph_provider,
            connector_id=state["connector_id"],
            sharee=second_user,
        )
    finally:
        if "connector_id" in state:
            await destructor(
                bookstack_source, pipeshub_client, graph_provider, state,
                connector_type="BookStack",
            )
        else:
            bookstack_source.clear_objects(bookstack_source.base_url, run)
        bookstack_source.delete_user(user_id)
        bookstack_source.delete_role(role_id)


@pytest.mark.integration
@pytest.mark.bookstack
class TestBookStackScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "BookStack"
