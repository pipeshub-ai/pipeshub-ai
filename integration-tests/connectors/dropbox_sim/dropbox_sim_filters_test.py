# pyright: ignore-file

"""Dropbox sync filters, against the Dropbox simulator (design: DESIGN.md here).

Dropbox's sync filters are file extensions, modified date and created date (Dropbox's ``client_modified``). It has
no folder or team-folder scope filter, so nothing here mirrors Drive's ``folder_ids`` cases. A filter is saved as
the UI saves it (connector off, save, on again), which makes that sync a full one. Folders always pass the file
filters: they hold the tree together. "Index Shared Items" is in the lifecycle module (it needs indexing on), and
the personal connector's filter in ``dropbox_sim_test.py``.

The tenant is ``filter_scenario``: in alice's home note.txt, plan.md, README (no extension), Reports/report.pdf
(modified 40 days ago, created 60) and Reports/sheet.csv (modified and created 40 days ago); old.txt (modified 40,
created 60) and spec.md in the Projects team folder; logo.txt in the Design shared folder.

One connector, in order:
  DBX-F-01  extensions in [md, pdf]: the rest leave, in homes, team and shared folders alike; folders stay
  DBX-F-02  a file renamed into the filter comes in; a file renamed out of it leaves, on an incremental sync (M7)
  DBX-F-03  extensions not in [txt]: a file with no extension passes, .txt files leave
  DBX-F-04  the filter is cleared: everything comes back
  DBX-F-05  modified before 10 days ago: only the old files stay; an old file edited today leaves on the next
            incremental sync (M7)
  DBX-F-06  created between 90 and 30 days ago
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from connectors.dropbox_sim.dropbox_it import (
    apply_filters,
    assert_access_matches_source,
    assert_no_simulator_problems,
    assert_succeeded,
    records,
    sync,
)
from connectors.dropbox_sim.dropbox_sim import date_filter, extension_filter, filter_scenario
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.dropbox_sim,
    pytest.mark.asyncio(loop_scope="session"),
]

DAY_MS = 86_400_000
FILES = ["a-note", "a-plan", "a-report", "a-sheet", "a-readme", "p-spec", "p-old", "d-logo"]


def _days_ago(days: int) -> int:
    return int(time.time() * 1000) - days * DAY_MS


async def _scope(graph: GraphProviderProtocol, ctx: dict[str, Any], inside: list[str], outside: list[str]) -> None:
    await records(graph, ctx, inside, present=True)
    await records(graph, ctx, outside, present=False)


class TestDropboxTeamSyncFilters:
    TENANT = staticmethod(filter_scenario)

    @pytest.mark.order(0)
    async def test_dbx_f_01_extensions_in(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                          pipeshub_client: PipeshubClient) -> None:
        await records(graph_provider, dropbox_team, FILES + ["a-dir"])
        await apply_filters(pipeshub_client, graph_provider, dropbox_team, sync_values=extension_filter(["md", "pdf"]))
        await _scope(graph_provider, dropbox_team, ["a-plan", "a-report", "p-spec", "a-dir"],
                     ["a-note", "a-sheet", "a-readme", "p-old", "d-logo"])
        assert_succeeded(pipeshub_client, dropbox_team["connector_id"], "the full sync after the filter change")
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, ["a-plan", "p-spec"],
                                           "under the extension filter")

    @pytest.mark.order(1)
    async def test_dbx_f_02_renamed_across_the_filter(self, dropbox_team: dict[str, Any],
                                                      graph_provider: GraphProviderProtocol,
                                                      pipeshub_client: PipeshubClient) -> None:
        """A file that leaves the sync's scope leaves PipesHub on the next sync, not at some later full sync."""
        dropbox_team["sim"].ops({"op": "rename", "item": "a-note", "name": "note.md"},
                                {"op": "rename", "item": "a-plan", "name": "plan.txt"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await _scope(graph_provider, dropbox_team, ["a-note"], ["a-plan"])

    @pytest.mark.order(2)
    async def test_dbx_f_03_extensions_not_in(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                              pipeshub_client: PipeshubClient) -> None:
        await apply_filters(pipeshub_client, graph_provider, dropbox_team,
                            sync_values=extension_filter(["txt"], operator="not_in"))
        await _scope(graph_provider, dropbox_team, ["a-note", "a-report", "a-sheet", "a-readme", "p-spec"],
                     ["a-plan", "p-old", "d-logo"])

    @pytest.mark.order(3)
    async def test_dbx_f_04_filter_cleared(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                           pipeshub_client: PipeshubClient) -> None:
        await apply_filters(pipeshub_client, graph_provider, dropbox_team)
        await records(graph_provider, dropbox_team, FILES)
        await assert_access_matches_source(pipeshub_client, graph_provider, dropbox_team, ["p-old", "d-logo"],
                                           "after the filter was cleared")

    @pytest.mark.order(4)
    async def test_dbx_f_05_modified_before_and_an_edit_leaving_it(self, dropbox_team: dict[str, Any],
                                                                   graph_provider: GraphProviderProtocol,
                                                                   pipeshub_client: PipeshubClient) -> None:
        await apply_filters(pipeshub_client, graph_provider, dropbox_team,
                            sync_values=date_filter("modified", "is_before", None, _days_ago(10)))
        await _scope(graph_provider, dropbox_team, ["a-report", "a-sheet", "p-old", "a-dir"],
                     ["a-note", "a-plan", "a-readme", "p-spec", "d-logo"])
        # Edited today, the file is no longer "modified before 10 days ago".
        dropbox_team["sim"].ops({"op": "edit", "item": "p-old"})
        await sync(pipeshub_client, graph_provider, dropbox_team)
        await _scope(graph_provider, dropbox_team, ["a-report", "a-sheet"], ["p-old"])

    @pytest.mark.order(5)
    async def test_dbx_f_06_created_between(self, dropbox_team: dict[str, Any], graph_provider: GraphProviderProtocol,
                                            pipeshub_client: PipeshubClient) -> None:
        await apply_filters(pipeshub_client, graph_provider, dropbox_team,
                            sync_values=date_filter("created", "is_between", _days_ago(90), _days_ago(30)))
        # An edit moves the modified date only: p-old was still created 60 days ago.
        await _scope(graph_provider, dropbox_team, ["a-report", "a-sheet", "p-old"],
                     ["a-note", "a-plan", "a-readme", "p-spec", "d-logo"])
        assert_succeeded(pipeshub_client, dropbox_team["connector_id"], "the created-date full sync")
        assert_no_simulator_problems(dropbox_team["sim"])
