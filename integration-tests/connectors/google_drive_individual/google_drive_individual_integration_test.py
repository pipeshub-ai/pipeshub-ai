# pyright: ignore-file

"""Google Drive personal – folder_ids sync filter integration tests.

  order 1  TC-FF-001  — seed folder + descendants present; out-of-scope sibling absent
  order 2  TC-FF-002  — new file inside seed syncs on incremental
  order 3  TC-FF-003  — new nested subfolder expands scope; child file syncs
  order 4  TC-FF-004  — file moved out of seed is deleted from graph
  order 5  TC-FF-005  — file moved back into seed re-syncs
  order 6  TC-FF-006  — folder moved out of seed cascade-deletes descendants
  order 7  TC-FF-007  — folder moved back into seed pulls descendants
  order 8  TC-FF-008  — create under out_of_scope stays out of graph
  order 9  TC-FF-009  — rename in-scope file updates record name
  order 10 TC-FF-010  — fixture root ancestor present via placeholder sweep
  order 11 TC-FF-011  — in-scope parent change (seed → nested) updates parent
  order 12 TC-FF-012  — multi-seed folder_ids syncs seed + out_of_scope
  order 13 TC-FF-013  — narrow folder_ids to nested drops seed-direct + OOS
  order 14 TC-FF-014  — widen folder_ids back to seed restores seed-direct
  order 15 TC-FF-015  — clear folder_ids (empty list) syncs fixture OOS again
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import MimeTypes  # type: ignore[import-not-found]  # noqa: E402
from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]  # noqa: E402
    GoogleDriveDataSource,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # noqa: E402
    create_drive_folder,
    create_drive_text_file,
    move_drive_item,
    rename_drive_item,
)
from helper.assertions import ConnectorAssertions, RecordAssertion  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from pipeshub_client import PipeshubClient  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_individual,
    pytest.mark.asyncio(loop_scope="session"),
]

_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_INDIVIDUAL_SYNC_TIMEOUT", "300"))


def _restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
    """Disable then re-enable the connector to trigger a fresh incremental sync."""
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(8)


async def _wait_record_present(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    external_id: str,
    *,
    description: str,
) -> None:
    async def _present() -> bool:
        return (
            await graph_provider.get_record_by_external_id(connector_id, external_id)
            is not None
        )

    await wait_until_graph_condition(
        connector_id,
        check=_present,
        timeout=_SYNC_TIMEOUT_SEC,
        poll_interval=10,
        description=description,
    )


async def _wait_record_absent(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    external_id: str,
    *,
    description: str,
) -> None:
    async def _absent() -> bool:
        return (
            await graph_provider.get_record_by_external_id(connector_id, external_id)
            is None
        )

    await wait_until_graph_condition(
        connector_id,
        check=_absent,
        timeout=_SYNC_TIMEOUT_SEC,
        poll_interval=10,
        description=description,
    )


async def _sync_and_wait(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
) -> None:
    _restart_sync(pipeshub_client, connector_id)
    await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        timeout=_SYNC_TIMEOUT_SEC,
    )


def _folder_ids_filters(folder_ids: list[str]) -> dict[str, Any]:
    """Build filters payload for ``config.filters.sync.values.folder_ids``."""
    return {
        "sync": {
            "values": {
                "folder_ids": {
                    "operator": "in",
                    "type": "list",
                    "value": folder_ids,
                }
            }
        }
    }


async def _apply_folder_ids_filter(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    folder_ids: list[str],
) -> None:
    """Update folder_ids via filters-sync and wait for the pending full sync.

    Changing sync filters sets ``pendingFullSync``, so the re-enable inside
    ``update_connector_filters_sync_safe`` is itself a full sync — no separate
    toggle restart is needed.

    Full sync wipes+recreates BELONGS_TO (and other sync edges) connector-wide;
    out-of-scope Record nodes are left in place (same model as Jira filter ITs).
    """
    pipeshub_client.update_connector_filters_sync_safe(
        connector_id,
        filters=_folder_ids_filters(folder_ids),
    )
    await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        timeout=_SYNC_TIMEOUT_SEC,
    )


async def _wait_scoped_record_count(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    *,
    check: Callable[[int], bool],
    description: str,
) -> int:
    """Wait until ``count_records(scoped=True)`` satisfies *check*; return that count.

    Scoped counts only include Records with a live Record→RecordGroup BELONGS_TO
    edge — the correct signal after a filter-change full sync (nodes are not deleted).
    """
    latest: dict[str, int] = {"count": -1}

    async def _ok() -> bool:
        count = await graph_provider.count_records(connector_id, scoped=True)
        latest["count"] = count
        return check(count)

    await wait_until_graph_condition(
        connector_id,
        check=_ok,
        timeout=_SYNC_TIMEOUT_SEC,
        poll_interval=10,
        description=description,
    )
    return latest["count"]


class TestDriveIndividualFolderFilter:
    """Assert folder_ids filter syncs the seed subtree and handles scope transitions."""

    @pytest.mark.order(1)
    async def test_tc_ff_001_folder_ids_filter_syncs_seed_subtree(
        self,
        drive_individual_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        nested_id = drive_individual_connector["nested_folder_id"]
        child_id = drive_individual_connector["child_file_id"]
        oos_folder_id = drive_individual_connector["oos_folder_id"]
        oos_file_id = drive_individual_connector["oos_file_id"]

        await connector_assertions.assert_record_exists(
            connector_id,
            seed_id,
            RecordAssertion(
                external_record_id=seed_id,
                record_name=drive_individual_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_individual_connector["nested_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            child_id,
            RecordAssertion(
                external_record_id=child_id,
                record_name=drive_individual_connector["child_file_name"],
                parent_external_record_id=nested_id,
            ),
        )

        oos_folder = await connector_assertions.graph.get_record_by_external_id(
            connector_id, oos_folder_id
        )
        assert oos_folder is None, (
            f"Out-of-scope folder {oos_folder_id} should not sync under folder_ids filter"
        )

        oos_file = await connector_assertions.graph.get_record_by_external_id(
            connector_id, oos_file_id
        )
        assert oos_file is None, (
            f"Out-of-scope file {oos_file_id} should not sync under folder_ids filter"
        )

    @pytest.mark.order(2)
    async def test_tc_ff_002_new_file_inside_seed_syncs(
        self,
        drive_individual_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]

        new_file_id = await create_drive_text_file(
            drive_individual_datasource,
            "new.txt",
            parent_id=seed_id,
            content="ff-002 new file in scope\n",
        )
        drive_individual_connector["new_file_id"] = new_file_id
        drive_individual_connector["new_file_name"] = "new.txt"

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            new_file_id,
            description=f"new.txt ({new_file_id}) in graph",
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            new_file_id,
            RecordAssertion(
                external_record_id=new_file_id,
                record_name="new.txt",
                parent_external_record_id=seed_id,
            ),
        )

    @pytest.mark.order(3)
    async def test_tc_ff_003_new_subfolder_expands_scope(
        self,
        drive_individual_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]

        deeper_id = await create_drive_folder(
            drive_individual_datasource, "deeper", parent_id=seed_id
        )
        deeper_file_id = await create_drive_text_file(
            drive_individual_datasource,
            "file.txt",
            parent_id=deeper_id,
            content="ff-003 deeper file\n",
        )
        drive_individual_connector["deeper_folder_id"] = deeper_id
        drive_individual_connector["deeper_file_id"] = deeper_file_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            deeper_file_id,
            description=f"deeper/file.txt ({deeper_file_id}) in graph",
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            deeper_id,
            RecordAssertion(
                external_record_id=deeper_id,
                record_name="deeper",
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            deeper_file_id,
            RecordAssertion(
                external_record_id=deeper_file_id,
                record_name="file.txt",
                parent_external_record_id=deeper_id,
            ),
        )

    @pytest.mark.order(4)
    async def test_tc_ff_004_file_exits_scope_deleted(
        self,
        drive_individual_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        oos_folder_id = drive_individual_connector["oos_folder_id"]

        leave_file_id = await create_drive_text_file(
            drive_individual_datasource,
            "leave.txt",
            parent_id=seed_id,
            content="ff-004 leave then return\n",
        )
        drive_individual_connector["leave_file_id"] = leave_file_id
        drive_individual_connector["leave_file_name"] = "leave.txt"

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            leave_file_id,
            description=f"leave.txt ({leave_file_id}) synced before move-out",
        )

        await move_drive_item(
            drive_individual_datasource,
            leave_file_id,
            new_parent_id=oos_folder_id,
            old_parent_id=seed_id,
        )
        drive_individual_connector["leave_file_parent_id"] = oos_folder_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_absent(
            graph_provider,
            connector_id,
            leave_file_id,
            description=f"leave.txt ({leave_file_id}) deleted after scope exit",
        )

    @pytest.mark.order(5)
    async def test_tc_ff_005_file_reenters_scope_resyncs(
        self,
        drive_individual_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        leave_file_id = drive_individual_connector["leave_file_id"]
        oos_folder_id = drive_individual_connector["oos_folder_id"]

        await move_drive_item(
            drive_individual_datasource,
            leave_file_id,
            new_parent_id=seed_id,
            old_parent_id=oos_folder_id,
        )
        drive_individual_connector["leave_file_parent_id"] = seed_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            leave_file_id,
            description=f"leave.txt ({leave_file_id}) re-synced after scope enter",
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            leave_file_id,
            RecordAssertion(
                external_record_id=leave_file_id,
                record_name="leave.txt",
                parent_external_record_id=seed_id,
            ),
        )

    @pytest.mark.order(6)
    async def test_tc_ff_006_folder_exits_scope_cascade_deletes(
        self,
        drive_individual_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        oos_folder_id = drive_individual_connector["oos_folder_id"]

        movable_id = await create_drive_folder(
            drive_individual_datasource, "movable", parent_id=seed_id
        )
        inside_id = await create_drive_text_file(
            drive_individual_datasource,
            "inside.txt",
            parent_id=movable_id,
            content="ff-006/007 movable folder\n",
        )
        drive_individual_connector["movable_folder_id"] = movable_id
        drive_individual_connector["movable_inside_file_id"] = inside_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            inside_id,
            description=f"movable/inside.txt ({inside_id}) synced before move-out",
        )

        await move_drive_item(
            drive_individual_datasource,
            movable_id,
            new_parent_id=oos_folder_id,
            old_parent_id=seed_id,
        )
        drive_individual_connector["movable_parent_id"] = oos_folder_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_absent(
            graph_provider,
            connector_id,
            movable_id,
            description=f"movable folder ({movable_id}) deleted after scope exit",
        )
        await _wait_record_absent(
            graph_provider,
            connector_id,
            inside_id,
            description=f"inside.txt ({inside_id}) cascade-deleted after folder scope exit",
        )

    @pytest.mark.order(7)
    async def test_tc_ff_007_folder_enters_scope_pulls_descendants(
        self,
        drive_individual_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        oos_folder_id = drive_individual_connector["oos_folder_id"]
        movable_id = drive_individual_connector["movable_folder_id"]
        inside_id = drive_individual_connector["movable_inside_file_id"]

        await move_drive_item(
            drive_individual_datasource,
            movable_id,
            new_parent_id=seed_id,
            old_parent_id=oos_folder_id,
        )
        drive_individual_connector["movable_parent_id"] = seed_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            movable_id,
            description=f"movable folder ({movable_id}) re-synced after scope enter",
        )
        await _wait_record_present(
            graph_provider,
            connector_id,
            inside_id,
            description=f"inside.txt ({inside_id}) pulled with folder enter-scope",
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            movable_id,
            RecordAssertion(
                external_record_id=movable_id,
                record_name="movable",
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            inside_id,
            RecordAssertion(
                external_record_id=inside_id,
                record_name="inside.txt",
                parent_external_record_id=movable_id,
            ),
        )

    @pytest.mark.order(8)
    async def test_tc_ff_008_oos_create_stays_out(
        self,
        drive_individual_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        oos_folder_id = drive_individual_connector["oos_folder_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        in_scope_id = (
            drive_individual_connector.get("new_file_id")
            or drive_individual_connector.get("leave_file_id")
        )
        assert in_scope_id, "Expected an in-scope file id from FF-002 or FF-005"

        ignored_id = await create_drive_text_file(
            drive_individual_datasource,
            "ignored.txt",
            parent_id=oos_folder_id,
            content="ff-008 out of scope\n",
        )
        drive_individual_connector["oos_ignored_file_id"] = ignored_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)

        ignored = await graph_provider.get_record_by_external_id(
            connector_id, ignored_id
        )
        assert ignored is None, (
            f"Out-of-scope ignored.txt ({ignored_id}) must not sync under folder_ids"
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            in_scope_id,
            RecordAssertion(
                external_record_id=in_scope_id,
                parent_external_record_id=seed_id,
            ),
        )

    @pytest.mark.order(9)
    async def test_tc_ff_009_rename_in_scope_updates_name(
        self,
        drive_individual_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        leave_file_id = drive_individual_connector["leave_file_id"]
        new_name = "leave-renamed.txt"

        await rename_drive_item(drive_individual_datasource, leave_file_id, new_name)
        drive_individual_connector["leave_file_name"] = new_name

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            leave_file_id,
            description=f"{new_name} ({leave_file_id}) present after rename",
        )

        async def _renamed() -> bool:
            record = await graph_provider.get_record_by_external_id(
                connector_id, leave_file_id
            )
            return record is not None and record.record_name == new_name

        await wait_until_graph_condition(
            connector_id,
            check=_renamed,
            timeout=_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description=f"leave.txt renamed to {new_name}",
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            leave_file_id,
            RecordAssertion(
                external_record_id=leave_file_id,
                record_name=new_name,
                parent_external_record_id=seed_id,
            ),
        )

    @pytest.mark.order(10)
    async def test_tc_ff_010_placeholder_root_ancestor_present(
        self,
        drive_individual_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        root_id = drive_individual_connector["root_folder_id"]
        seed_id = drive_individual_connector["seed_folder_id"]

        await connector_assertions.assert_record_exists(
            connector_id,
            root_id,
            RecordAssertion(
                external_record_id=root_id,
                record_name=drive_individual_connector["root_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            seed_id,
            RecordAssertion(
                external_record_id=seed_id,
                record_name=drive_individual_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=root_id,
            ),
        )

    @pytest.mark.order(11)
    async def test_tc_ff_011_in_scope_parent_change_updates_parent(
        self,
        drive_individual_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        nested_id = drive_individual_connector["nested_folder_id"]
        new_file_id = drive_individual_connector["new_file_id"]

        await move_drive_item(
            drive_individual_datasource,
            new_file_id,
            new_parent_id=nested_id,
            old_parent_id=seed_id,
        )
        drive_individual_connector["new_file_parent_id"] = nested_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)

        async def _parent_updated() -> bool:
            record = await graph_provider.get_record_by_external_id(
                connector_id, new_file_id
            )
            return (
                record is not None
                and record.parent_external_record_id == nested_id
            )

        await wait_until_graph_condition(
            connector_id,
            check=_parent_updated,
            timeout=_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description=f"new.txt ({new_file_id}) parent → nested",
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            new_file_id,
            RecordAssertion(
                external_record_id=new_file_id,
                record_name="new.txt",
                parent_external_record_id=nested_id,
            ),
        )

    @pytest.mark.order(12)
    async def test_tc_ff_012_multi_seed_syncs_oos_sibling(
        self,
        drive_individual_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        nested_id = drive_individual_connector["nested_folder_id"]
        child_id = drive_individual_connector["child_file_id"]
        oos_folder_id = drive_individual_connector["oos_folder_id"]
        oos_file_id = drive_individual_connector["oos_file_id"]

        scoped_before = await graph_provider.count_records(
            connector_id, scoped=True
        )

        await _apply_folder_ids_filter(
            pipeshub_client,
            graph_provider,
            connector_id,
            [seed_id, oos_folder_id],
        )
        await _wait_record_present(
            graph_provider,
            connector_id,
            oos_file_id,
            description=f"sibling.txt ({oos_file_id}) record present under multi-seed",
        )
        # oos folder + sibling.txt (+ ignored.txt from FF-008 if still under oos)
        scoped_after = await _wait_scoped_record_count(
            graph_provider,
            connector_id,
            check=lambda c: c >= scoped_before + 2,
            description=(
                f"scoped BELONGS_TO count grew after multi-seed "
                f"(was {scoped_before})"
            ),
        )
        drive_individual_connector["scoped_after_multi_seed"] = scoped_after

        await connector_assertions.assert_record_exists(
            connector_id,
            oos_folder_id,
            RecordAssertion(
                external_record_id=oos_folder_id,
                record_name=drive_individual_connector["oos_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            oos_file_id,
            RecordAssertion(
                external_record_id=oos_file_id,
                record_name=drive_individual_connector["oos_file_name"],
                parent_external_record_id=oos_folder_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_individual_connector["nested_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            child_id,
            RecordAssertion(
                external_record_id=child_id,
                record_name=drive_individual_connector["child_file_name"],
                parent_external_record_id=nested_id,
            ),
        )

    @pytest.mark.order(13)
    async def test_tc_ff_013_narrow_folder_ids_to_nested(
        self,
        drive_individual_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        nested_id = drive_individual_connector["nested_folder_id"]
        child_id = drive_individual_connector["child_file_id"]
        new_file_id = drive_individual_connector["new_file_id"]

        scoped_before = int(
            drive_individual_connector.get("scoped_after_multi_seed")
            or await graph_provider.count_records(connector_id, scoped=True)
        )

        await _apply_folder_ids_filter(
            pipeshub_client,
            graph_provider,
            connector_id,
            [nested_id],
        )
        # Narrowing drops BELONGS_TO for seed-direct + OOS; Record nodes remain.
        scoped_after = await _wait_scoped_record_count(
            graph_provider,
            connector_id,
            check=lambda c: c < scoped_before,
            description=(
                f"scoped BELONGS_TO count shrank after narrow to nested "
                f"(was {scoped_before})"
            ),
        )
        drive_individual_connector["scoped_after_narrow"] = scoped_after

        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_individual_connector["nested_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            child_id,
            RecordAssertion(
                external_record_id=child_id,
                record_name=drive_individual_connector["child_file_name"],
                parent_external_record_id=nested_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            new_file_id,
            RecordAssertion(
                external_record_id=new_file_id,
                record_name="new.txt",
                parent_external_record_id=nested_id,
            ),
        )
        # Seed remains as placeholder/ancestor node (may or may not keep BELONGS_TO).
        await connector_assertions.assert_record_exists(
            connector_id,
            seed_id,
            RecordAssertion(
                external_record_id=seed_id,
                record_name=drive_individual_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )

    @pytest.mark.order(14)
    async def test_tc_ff_014_widen_folder_ids_to_seed(
        self,
        drive_individual_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        seed_id = drive_individual_connector["seed_folder_id"]
        leave_file_id = drive_individual_connector["leave_file_id"]
        leave_file_name = drive_individual_connector["leave_file_name"]

        scoped_narrow = int(
            drive_individual_connector.get("scoped_after_narrow")
            or await graph_provider.count_records(connector_id, scoped=True)
        )
        scoped_multi = int(
            drive_individual_connector.get("scoped_after_multi_seed") or 10**9
        )

        await _apply_folder_ids_filter(
            pipeshub_client,
            graph_provider,
            connector_id,
            [seed_id],
        )
        # Seed-direct items regain BELONGS_TO; OOS subtree stays without it.
        scoped_after = await _wait_scoped_record_count(
            graph_provider,
            connector_id,
            check=lambda c: c > scoped_narrow and c < scoped_multi,
            description=(
                f"scoped count between narrow ({scoped_narrow}) and "
                f"multi-seed ({scoped_multi}) after widen to seed"
            ),
        )
        drive_individual_connector["scoped_after_widen"] = scoped_after

        await connector_assertions.assert_record_exists(
            connector_id,
            leave_file_id,
            RecordAssertion(
                external_record_id=leave_file_id,
                record_name=leave_file_name,
                parent_external_record_id=seed_id,
            ),
        )

    @pytest.mark.order(15)
    async def test_tc_ff_015_clear_folder_ids_syncs_fixture_oos(
        self,
        drive_individual_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_connector["connector_id"]
        oos_folder_id = drive_individual_connector["oos_folder_id"]
        oos_file_id = drive_individual_connector["oos_file_id"]

        scoped_multi = int(
            drive_individual_connector.get("scoped_after_multi_seed")
            or await graph_provider.count_records(connector_id, scoped=True)
        )

        # Empty folder_ids disables the folder filter (syncs full My Drive into graph).
        # Orphan OOS nodes may already exist from FF-012; scoped count proves re-scope.
        # Teardown deletes only the fixture root on Drive.
        await _apply_folder_ids_filter(
            pipeshub_client,
            graph_provider,
            connector_id,
            [],
        )
        await _wait_scoped_record_count(
            graph_provider,
            connector_id,
            check=lambda c: c >= scoped_multi,
            description=(
                f"scoped count >= multi-seed ({scoped_multi}) after clearing folder_ids"
            ),
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            oos_folder_id,
            RecordAssertion(
                external_record_id=oos_folder_id,
                record_name=drive_individual_connector["oos_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            oos_file_id,
            RecordAssertion(
                external_record_id=oos_file_id,
                record_name=drive_individual_connector["oos_file_name"],
                parent_external_record_id=oos_folder_id,
            ),
        )
