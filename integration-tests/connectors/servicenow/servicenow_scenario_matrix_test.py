# pyright: ignore-file

"""ServiceNow in the shared scenario matrix (``connectors/scenario_matrix.py``).

Items are knowledge articles in this run's KBs; folders are categories. Every edit of a published
article is a checkout of a new version and a publish (published rows are read-only), so the same
record has to survive it. Sharing is ServiceNow's own: the permission item's KB lets a group read,
and granting adds the sharee's ServiceNow user to that group. The owner the scenarios search as is a
throw-away ServiceNow user who can read the main KB, with a PipesHub account under the same email.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.scenario_matrix import (
    Action,
    ConnectorScenarioMatrix,
    Role,
    ScenarioAdapter,
    SourceItem,
)
from connectors.servicenow.constants import KB_FILTER, NO_KB_FILTER
from connectors.servicenow.servicenow_helpers import (
    add_account,
    kb_filter,
    servicenow_connector,
)
from connectors.servicenow.servicenow_seed import ServiceNowSeed
from helper.graph_provider import GraphProviderProtocol
from helper.second_user import SecondUser


class ServiceNowAdapter(ScenarioAdapter):
    source = "ServiceNow"

    def __init__(self, *, seed: ServiceNowSeed, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.seed = seed

    def _kb(self, role: Role) -> str:
        return self.seed.kbs["filtered" if role is Role.FILTERED else "perm" if role is Role.PERMISSION else "main"]

    def _item(self, key: str, role: Role, text: str, token: str, **extra: Any) -> SourceItem:
        art = self.seed.articles[key]
        return SourceItem(role=role, key=key, record_name=art.title, text=text, token=token,
                          external_id=art.number, extra=extra)

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        s, key = self.seed, f"mx-{role.value}-{token}"

        def create() -> dict[str, Any]:
            extra: dict[str, Any] = {}
            category = None
            if role is Role.MOVE:
                category = s.categories["mx-from"]
            elif role is Role.FOLDER:
                category = s.category(f"mx-folder-{token}", s.kbs["main"])
            if category:
                extra["folder"] = s.rest.row("kb_category", category, "label")["label"]
            s.article(key, self._kb(role), category=category, title=s.name(f"mx-{token}"), text=f"<p>{text}</p>")
            s.publish_pending()
            return extra
        extra = await asyncio.to_thread(create)
        return self._item(key, role, text, token, **extra)

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        await asyncio.to_thread(self.seed.checkout, self.seed.articles[item.key], text=f"<p>{text}</p>")
        return self._item(item.key, item.role, text, token, **item.extra)

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        await asyncio.to_thread(self.seed.checkout, self.seed.articles[item.key],
                                short_description=self.seed.name(f"mx-renamed-{item.token}"))
        return self._item(item.key, item.role, item.text, item.token, **item.extra)

    async def change_permission(self, item: SourceItem, sharee: SecondUser, *, grant: bool) -> None:
        s = self.seed
        user = s.users["mx-sharee"].sys_id
        await asyncio.to_thread(s.add_member if grant else s.remove_member, s.groups["mx-perm"], user)

    async def delete_item(self, item: SourceItem) -> None:
        await asyncio.to_thread(self.seed.rest.delete, "kb_knowledge", self.seed.articles[item.key].sys_id)

    async def move_item(self, item: SourceItem) -> SourceItem:
        s = self.seed
        await asyncio.to_thread(s.checkout, s.articles[item.key], kb_category=s.categories["mx-to"])
        label = await asyncio.to_thread(lambda: s.rest.row("kb_category", s.categories["mx-to"], "label")["label"])
        return self._item(item.key, item.role, item.text, item.token, folder=label)

    async def rename_folder(self, item: SourceItem) -> SourceItem:
        s = self.seed
        category = s.articles[item.key].category
        label = s.name(f"mx-folder-renamed-{item.token}")
        await asyncio.to_thread(s.rest.patch, "kb_category", category, {"label": label})
        # ServiceNow cuts kb_category.label to its field length: name the folder as stored.
        label = await asyncio.to_thread(lambda: s.rest.row("kb_category", category, "label")["label"])
        return self._item(item.key, item.role, item.text, item.token, folder=label)

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        return kb_filter([self.seed.kbs["main"], self.seed.kbs["perm"]])

    async def cleanup(self) -> None:
        """The module's seed teardown removes every object; nothing to undo here."""


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    sn_seed: ServiceNowSeed,
    sn_oauth_config: str,
    sn_accounts: dict[str, SecondUser],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[ServiceNowAdapter, None]:
    s = sn_seed

    def build() -> None:
        owner = s.user("mx-owner")
        s.user("mx-sharee")
        main = s.criterion("uc-mx-main", group=s.group("mx-main", [owner.sys_id]))
        perm = s.criterion("uc-mx-perm", group=s.group("mx-perm", [owner.sys_id]))
        s.kb("main", read=[main])
        s.kb("perm", read=[perm])
        s.kb("filtered", read=[main])
        s.category("mx-from", s.kbs["main"])
        s.category("mx-to", s.kbs["main"])
        s.article("mx-seed", s.kbs["main"])
        s.publish_pending()

    await asyncio.to_thread(build)
    # Accounts exist before the connector, so its first user sync links them.
    owner = await asyncio.to_thread(add_account, pipeshub_client, sn_accounts, "mx-owner", s.users["mx-owner"].email)
    sharee = await asyncio.to_thread(add_account, pipeshub_client, sn_accounts, "mx-sharee", s.users["mx-sharee"].email)
    async with servicenow_connector(
        pipeshub_client, graph_provider, sn_oauth_config, name="servicenow-matrix",
        filters=kb_filter(s.kbs.values()), min_records=1,
    ) as connector_id:
        yield ServiceNowAdapter(seed=s, client=pipeshub_client, graph=graph_provider, connector_id=connector_id,
                                owner=owner, sharee=sharee)


@pytest.mark.integration
@pytest.mark.servicenow
class TestServiceNowScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "ServiceNow"
    TREE_MOVES = True
    UNSUPPORTED = {} if KB_FILTER else {Action.SET_FILTER.value: NO_KB_FILTER}
