# pyright: ignore-file

"""Zammad in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are tickets on the ``zammad-railsserver`` container. The connector finds
tickets through Zammad's search, so every source change here waits until that
search returns it before a sync is asked to see it.

Sharing: a Zammad ticket's access is its group; there is no per-ticket share.
The permission item sits alone in its own group, and the second person is a
Zammad agent with the PipesHub second user's email, given or denied that group.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connector_lifecycle import create_connector_and_await_sync, destructor

from connectors.scenario_matrix import (
    Action,
    ConnectorScenarioMatrix,
    FilterOutcome,
    Role,
    ScenarioAdapter,
    SourceItem,
)
from connectors.zammad.zammad_source_helper import ZammadSourceHelper
from helper.graph_provider import GraphProviderProtocol
from helper.second_user import SecondUser
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("zammad-scenario-matrix")


class ZammadAdapter(ScenarioAdapter):
    source = "Zammad"
    # A group filter change runs a full sync that drops every sync edge and reads
    # tickets only from the groups the filter keeps; nothing deletes the others.
    filter_outcome = FilterOutcome.DESCOPED

    def __init__(
        self, *, zammad: ZammadSourceHelper, run: str, groups: dict[str, int], sharee_id: int,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.zammad = zammad
        self.run = run
        self.groups = groups
        self.sharee_id = sharee_id

    def _group_for(self, role: Role) -> int:
        if role is Role.FILTERED:
            return self.groups["filtered"]
        if role is Role.PERMISSION:
            return self.groups["shared"]
        return self.groups["main"]

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        title = f"{self.run} {role.value}-{token}"
        ticket_id = self.zammad.create_ticket(title, text, self._group_for(role))
        self.zammad.wait_until_searchable([title])
        return SourceItem(
            role=role, key=str(ticket_id), record_name=title, text=text, token=token,
            external_id=str(ticket_id),
        )

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        title = f"{self.run} renamed-{item.token}"
        self.zammad.update_ticket_title(int(item.key), title)
        self.zammad.wait_until_searchable([title])
        return SourceItem(
            role=item.role, key=item.key, record_name=title, text=item.text,
            token=item.token, external_id=item.external_id,
        )

    async def change_permission(self, item: SourceItem, sharee: SecondUser, *, grant: bool) -> None:
        self.zammad.set_user_groups(self.sharee_id, [self.groups["shared"]] if grant else [])

    async def delete_item(self, item: SourceItem) -> None:
        self.zammad.delete_ticket(int(item.key))

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        return {
            "sync": {
                "values": {
                    "group_ids": {
                        "operator": "not_in",
                        "value": [str(self.groups["filtered"])],
                        "type": "list",
                    }
                }
            }
        }

    async def cleanup(self) -> None:
        self.zammad.set_user_groups(self.sharee_id, [])


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    zammad_source: ZammadSourceHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    second_user: SecondUser,
) -> AsyncGenerator[ZammadAdapter, None]:
    # Tickets left by an earlier, interrupted run would become records here too.
    zammad_source.delete_tickets_titled("it-")

    run = f"it-mx-{uuid.uuid4().hex[:8]}"
    groups = {
        name: zammad_source.create_group(f"{run} {name}")
        for name in ("main", "shared", "filtered")
    }
    seed_title = f"{run} seed"
    zammad_source.create_ticket(seed_title, "Scenario matrix seed ticket.", groups["main"])
    zammad_source.wait_until_searchable([seed_title])
    # Created before the connector, so its first sync already maps the agent to
    # the PipesHub second user; the scenarios only change the agent's groups.
    sharee_id = zammad_source.create_agent(second_user.email, run)

    state: dict[str, Any] = {"resource_name": zammad_source.base_url, "folder": run}
    adapter: ZammadAdapter | None = None
    try:
        await create_connector_and_await_sync(
            pipeshub_client,
            graph_provider,
            state,
            connector_type="Zammad",
            connector_name=f"zammad-matrix-{uuid.uuid4().hex[:8]}",
            connector_config={
                "auth": {
                    "baseUrl": os.getenv("ZAMMAD_CONNECTOR_URL", "http://zammad-railsserver:3000"),
                    "token": zammad_source.token,
                }
            },
            expected_records=1,
            scope="team",
            auth_type="API_TOKEN",
        )
        adapter = ZammadAdapter(
            zammad=zammad_source,
            run=run,
            groups=groups,
            sharee_id=sharee_id,
            client=pipeshub_client,
            graph=graph_provider,
            connector_id=state["connector_id"],
            sharee=second_user,
        )
        yield adapter
    finally:
        if adapter is not None:
            await adapter.cleanup()
        if "connector_id" in state:
            await destructor(
                zammad_source, pipeshub_client, graph_provider, state, connector_type="Zammad"
            )
        else:
            zammad_source.clear_objects(zammad_source.base_url, run)
        for cleanup, target in [(zammad_source.delete_user, sharee_id)] + [
            (zammad_source.delete_group, g) for g in groups.values()
        ]:
            try:
                cleanup(target)
            except Exception as exc:  # noqa: BLE001 - Zammad refuses to delete what history references
                logger.warning("Zammad kept %s %s at teardown: %s", cleanup.__name__, target, exc)


_DELETE_BUG = (
    "Zammad deletions never reach the index: every sync asks Zammad's search only for "
    "tickets updated since the group's checkpoint (_fetch_tickets_for_group_batch, "
    "sources/zammad/connector.py:926-1070) and nothing in the connector calls "
    "on_record_deleted, so a deleted ticket's record, vectors and search hit stay"
)


@pytest.mark.integration
@pytest.mark.zammad
class TestZammadScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Zammad"
    UNSUPPORTED = {
        Action.UPDATE_CONTENT.value: (
            "a ticket's text is its articles, and Zammad's API cannot edit an article's body "
            "(articles are append-only), so old text can never leave a ticket"
        ),
    }
    KNOWN_BUGS = {"incr_delete": _DELETE_BUG}
