# pyright: ignore-file

"""Dropbox (team) in the shared scenario matrix (``connectors/scenario_matrix.py``), against the Dropbox simulator.

The team is ``matrix_scenario``: alice, who owns every item the matrix makes (in ``Matrix/`` in her home), and bob,
who is shared with. Items are plain-text files whose bytes the simulator serves as the matrix wrote them, so the
vector and search checks read the real text. The scenario the filter excludes is a ``.md`` file; the exclusion
filter is "extensions in [txt]" (Dropbox has no folder scope filter). Search runs as alice.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from connectors.dropbox_sim.dropbox_it import dropbox_connector, pipeshub_people, simulator
from connectors.dropbox_sim.dropbox_sim import DOMAIN, DropboxSim, extension_filter, matrix_scenario
from connectors.scenario_matrix import ConnectorScenarioMatrix, Role, ScenarioAdapter, SourceItem
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.second_user import SecondUser, log_in_existing_user, log_out_existing_user  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

ROOT = "mx-dir"


class DropboxAdapter(ScenarioAdapter):
    source = "Dropbox"

    def __init__(self, *, sim: DropboxSim, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.sim = sim

    def _item(self, item: SourceItem, **changes: Any) -> SourceItem:
        fields = {"role": item.role, "key": item.key, "record_name": item.record_name, "text": item.text,
                  "token": item.token, "external_id": item.external_id, "extra": dict(item.extra)}
        fields.update(changes)
        return SourceItem(**fields)

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        parent, extra = ROOT, {}
        if role is Role.FOLDER:
            parent = extra["folder_key"] = f"mx-folder-{token}"
            extra["folder"] = f"dir-{token}"
            self.sim.ops({"op": "add_folder", "in": ROOT, "key": parent, "name": extra["folder"]})
        name = f"{role.value}-{token}.{'md' if role is Role.FILTERED else 'txt'}"
        key = f"mx-{token}"
        self.sim.ops({"op": "add_file", "in": parent, "key": key, "name": name, "text": text})
        return SourceItem(role=role, key=key, record_name=name, text=text, token=token,
                          external_id=self.sim.item_id(key), extra=extra)

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        self.sim.ops({"op": "edit", "item": item.key, "text": text})
        return self._item(item, text=text, token=token)

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        name = f"renamed-{item.token}.txt"
        self.sim.ops({"op": "rename", "item": item.key, "name": name})
        return self._item(item, record_name=name)

    async def move_item(self, item: SourceItem) -> SourceItem:
        folder = f"moved-{item.token}"
        self.sim.ops({"op": "add_folder", "in": ROOT, "key": f"mx-moved-{item.token}", "name": folder},
                     {"op": "move", "item": item.key, "to": f"mx-moved-{item.token}"})
        return self._item(item, extra={**item.extra, "folder": folder})

    async def rename_folder(self, item: SourceItem) -> SourceItem:
        new = f"dir-{item.token}-renamed"
        self.sim.ops({"op": "rename", "item": item.extra["folder_key"], "name": new})
        return self._item(item, extra={**item.extra, "folder": new})

    async def change_permission(self, item: SourceItem, sharee: SecondUser, *, grant: bool) -> None:
        op = "share_file" if grant else "unshare_file"
        self.sim.ops({"op": op, "item": item.key, "principal": "user:bob", "access": "viewer"})

    async def delete_item(self, item: SourceItem) -> None:
        self.sim.ops({"op": "delete", "item": item.key})

    async def exclusion_filter(self, excluded: SourceItem, kept: list[SourceItem]) -> dict[str, Any]:
        assert excluded.record_name.endswith(".md") and all(k.record_name.endswith(".txt") for k in kept)
        return {"sync": {"values": extension_filter(["txt"])}}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    pipeshub_client: PipeshubClient,
    users_client: Any,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[DropboxAdapter, None]:
    sim = simulator()
    scenario = matrix_scenario(DOMAIN)
    sim.load(scenario)
    users = await pipeshub_people(users_client, graph_provider, {m["key"]: m["email"] for m in scenario["members"]})
    async with dropbox_connector(pipeshub_client, graph_provider, sim, "Dropbox", "team",
                                 auto_index=True) as connector_id:
        owner = log_in_existing_user(pipeshub_client, users["alice"]["id"], users["alice"]["email"])
        sharee = log_in_existing_user(pipeshub_client, users["bob"]["id"], users["bob"]["email"])
        try:
            yield DropboxAdapter(sim=sim, client=pipeshub_client, graph=graph_provider, connector_id=connector_id,
                                 owner=owner, sharee=sharee)
        finally:
            for user in (owner, sharee):
                log_out_existing_user(pipeshub_client, user)


@pytest.mark.integration
@pytest.mark.dropbox_sim
class TestDropboxScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "Dropbox"
    TREE_MOVES = True
