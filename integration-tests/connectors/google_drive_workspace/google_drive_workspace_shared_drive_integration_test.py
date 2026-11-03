# pyright: ignore-file

"""Google Drive Workspace – Shared Drive folder_ids + drive_ids lean ITs.

Creates/tears down temporary Shared Drives. Covers only Shared Drive-specific
plumbing; full leave/return/rename matrix lives in the My Drive suite.

  order 1  TC-SD-FF-001  — seed + descendants present; OOS + Drive B ignored
  order 2  TC-SD-FF-002  — new nested subfolder expands scope (corpora=drive)
  order 3  TC-SD-FF-003  — new file under seed syncs on incremental
  order 4  TC-SD-FF-004  — folder_ids=[Shared Drive root] expands top-level tree

  order 5  TC-DRIVE-IDS-001 — drive_ids=[A] only: Drive A in, Drive B out
  order 6  TC-DRIVE-IDS-002 — widen drive_ids to [A,B]: Drive B gains BELONGS_TO
  order 7  TC-DRIVE-IDS-003 — drive picker pages through every Shared Drive; search finds A
  order 8  TC-DRIVE-IDS-004 — shrink drive_ids to [B]: Drive A's records removed
"""

from __future__ import annotations

import os
import sys
from collections.abc import Awaitable, Callable
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
    record_present,
    sync_until,
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
    pytest.mark.google_drive_workspace,
    pytest.mark.asyncio(loop_scope="session"),
]

_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_SYNC_TIMEOUT", "300"))


def _all_present(
    graph_provider: GraphProviderProtocol, connector_id: str, *external_ids: str
) -> Callable[[], Awaitable[bool]]:
    checks = [record_present(graph_provider, connector_id, eid) for eid in external_ids]

    async def _present() -> bool:
        for check in checks:
            if not await check():
                return False
        return True

    return _present


class TestDriveWorkspaceSharedDriveFolderFilter:
    """Lean Shared Drive coverage for folder_ids + drive_ids."""

    @pytest.mark.order(1)
    async def test_tc_sd_ff_001_seed_subtree_and_drive_b_ignored(
        self,
        drive_workspace_shared_drive_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = drive_workspace_shared_drive_connector["connector_id"]
        seed_id = drive_workspace_shared_drive_connector["seed_folder_id"]
        nested_id = drive_workspace_shared_drive_connector["nested_folder_id"]
        child_id = drive_workspace_shared_drive_connector["child_file_id"]
        oos_folder_id = drive_workspace_shared_drive_connector["oos_folder_id"]
        oos_file_id = drive_workspace_shared_drive_connector["oos_file_id"]
        drive_b_file_id = drive_workspace_shared_drive_connector[
            "drive_b_ignored_file_id"
        ]

        await connector_assertions.assert_record_exists(
            connector_id,
            seed_id,
            RecordAssertion(
                external_record_id=seed_id,
                record_name=drive_workspace_shared_drive_connector["seed_folder_name"],
                mime_type=MimeTypes.FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_workspace_shared_drive_connector["nested_folder_name"],
                mime_type=MimeTypes.FOLDER.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            child_id,
            RecordAssertion(
                external_record_id=child_id,
                record_name=drive_workspace_shared_drive_connector["child_file_name"],
                parent_external_record_id=nested_id,
            ),
        )

        oos_folder = await graph_provider.get_record_by_external_id(
            connector_id, oos_folder_id
        )
        assert oos_folder is None, (
            f"Out-of-scope folder {oos_folder_id} should not sync under folder_ids"
        )
        oos_file = await graph_provider.get_record_by_external_id(
            connector_id, oos_file_id
        )
        assert oos_file is None, (
            f"Out-of-scope file {oos_file_id} should not sync under folder_ids"
        )

        drive_b_file = await graph_provider.get_record_by_external_id(
            connector_id, drive_b_file_id
        )
        assert drive_b_file is None, (
            f"Drive B ignored.txt ({drive_b_file_id}) must not sync when "
            f"drive_ids=[Drive A]"
        )

    @pytest.mark.order(2)
    async def test_tc_sd_ff_002_nested_subfolder_expands_scope(
        self,
        drive_workspace_shared_drive_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_shared_drive_connector["connector_id"]
        seed_id = drive_workspace_shared_drive_connector["seed_folder_id"]

        deeper_id = await create_drive_folder(
            drive_workspace_datasource, "deeper", parent_id=seed_id
        )
        deeper_child_id = await create_drive_text_file(
            drive_workspace_datasource,
            "deeper-child.txt",
            parent_id=deeper_id,
            content="sd-ff-002 nested expansion\n",
        )
        drive_workspace_shared_drive_connector["deeper_folder_id"] = deeper_id
        drive_workspace_shared_drive_connector["deeper_child_file_id"] = deeper_child_id

        await sync_until(
            pipeshub_client,
            graph_provider,
            connector_id,
            _all_present(graph_provider, connector_id, deeper_id, deeper_child_id),
            description=(
                f"deeper folder ({deeper_id}) and deeper-child.txt ({deeper_child_id}) "
                "after Shared Drive expansion"
            ),
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            deeper_id,
            RecordAssertion(
                external_record_id=deeper_id,
                record_name="deeper",
                mime_type=MimeTypes.FOLDER.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            deeper_child_id,
            RecordAssertion(
                external_record_id=deeper_child_id,
                record_name="deeper-child.txt",
                parent_external_record_id=deeper_id,
            ),
        )

    @pytest.mark.order(3)
    async def test_tc_sd_ff_003_new_file_under_seed_syncs(
        self,
        drive_workspace_shared_drive_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_shared_drive_connector["connector_id"]
        seed_id = drive_workspace_shared_drive_connector["seed_folder_id"]

        new_file_id = await create_drive_text_file(
            drive_workspace_datasource,
            "new.txt",
            parent_id=seed_id,
            content="sd-ff-003 incremental create\n",
        )
        drive_workspace_shared_drive_connector["new_file_id"] = new_file_id

        await sync_until(
            pipeshub_client,
            graph_provider,
            connector_id,
            record_present(graph_provider, connector_id, new_file_id),
            description=f"new.txt ({new_file_id}) under Shared Drive seed",
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

    @pytest.mark.order(4)
    async def test_tc_sd_ff_004_seed_equals_shared_drive_root(
        self,
        drive_workspace_shared_drive_root_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_shared_drive_root_connector["connector_id"]
        drive_a_id = drive_workspace_shared_drive_root_connector["drive_a_id"]
        root_folder_id = drive_workspace_shared_drive_root_connector["root_folder_id"]
        root_file_id = drive_workspace_shared_drive_root_connector["root_file_id"]

        # Top-level Shared Drive items have parents=[driveId] at the source; the
        # connector clears parent_external_record_id when parent == drive_id and
        # attaches the Shared Drive as the record group instead.
        await connector_assertions.assert_record_exists(
            connector_id,
            root_folder_id,
            RecordAssertion(
                external_record_id=root_folder_id,
                record_name=drive_workspace_shared_drive_root_connector[
                    "root_folder_name"
                ],
                mime_type=MimeTypes.FOLDER.value,
                external_record_group_id=drive_a_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            root_file_id,
            RecordAssertion(
                external_record_id=root_file_id,
                record_name=drive_workspace_shared_drive_root_connector[
                    "root_file_name"
                ],
                parent_external_record_id=root_folder_id,
                external_record_group_id=drive_a_id,
            ),
        )


def _drive_ids_filters(drive_ids: list[str]) -> dict[str, Any]:
    """Build filters payload for ``config.filters.sync.values.drive_ids``."""
    return {
        "sync": {
            "values": {
                "drive_ids": {
                    "operator": "in",
                    "type": "list",
                    "value": drive_ids,
                }
            }
        }
    }


async def _apply_drive_ids_filter(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    drive_ids: list[str],
) -> None:
    """Update drive_ids via filters-sync and wait for the pending full sync."""
    pipeshub_client.update_connector_filters_sync_safe(
        connector_id,
        filters=_drive_ids_filters(drive_ids),
    )
    await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        timeout=_SYNC_TIMEOUT_SEC,
    )


class TestDriveWorkspaceDriveIdsFilter:
    """Pure drive_ids filter — no folder_ids (full Shared Drive A in scope)."""

    @pytest.mark.order(5)
    async def test_tc_drive_ids_001_drive_a_in_drive_b_out(
        self,
        drive_workspace_drive_ids_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = drive_workspace_drive_ids_connector["connector_id"]
        drive_a_id = drive_workspace_drive_ids_connector["drive_a_id"]
        drive_b_id = drive_workspace_drive_ids_connector["drive_b_id"]
        file_a_id = drive_workspace_drive_ids_connector["drive_a_file_id"]
        file_b_id = drive_workspace_drive_ids_connector["drive_b_file_id"]

        rg_a = await graph_provider.get_record_group_by_external_id(
            connector_id, drive_a_id
        )
        assert rg_a is not None, (
            f"Shared Drive A RecordGroup missing for drive_ids=[A] ({drive_a_id})"
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            file_a_id,
            RecordAssertion(
                external_record_id=file_a_id,
                record_name=drive_workspace_drive_ids_connector["drive_a_file_name"],
                external_record_group_id=drive_a_id,
            ),
        )
        assert await graph_provider.record_belongs_to_external_group(
            connector_id, file_a_id, drive_a_id
        ), f"{file_a_id} should BELONGS_TO Shared Drive A {drive_a_id}"

        file_b = await graph_provider.get_record_by_external_id(connector_id, file_b_id)
        assert file_b is None, (
            f"Drive B file {file_b_id} must not sync when drive_ids=[Drive A]"
        )
        rg_b = await graph_provider.get_record_group_by_external_id(
            connector_id, drive_b_id
        )
        assert rg_b is None, (
            f"Shared Drive B RecordGroup must not sync when drive_ids=[Drive A] "
            f"({drive_b_id})"
        )

    @pytest.mark.order(6)
    async def test_tc_drive_ids_002_widen_includes_drive_b(
        self,
        drive_workspace_drive_ids_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_drive_ids_connector["connector_id"]
        drive_a_id = drive_workspace_drive_ids_connector["drive_a_id"]
        drive_b_id = drive_workspace_drive_ids_connector["drive_b_id"]
        file_b_id = drive_workspace_drive_ids_connector["drive_b_file_id"]

        await _apply_drive_ids_filter(
            pipeshub_client,
            graph_provider,
            connector_id,
            [drive_a_id, drive_b_id],
        )

        async def _b_scoped() -> bool:
            return await graph_provider.record_belongs_to_external_group(
                connector_id, file_b_id, drive_b_id
            )

        await wait_until_graph_condition(
            connector_id,
            check=_b_scoped,
            timeout=_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description=(
                f"Drive B file ({file_b_id}) BELONGS_TO after drive_ids widen"
            ),
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            file_b_id,
            RecordAssertion(
                external_record_id=file_b_id,
                record_name=drive_workspace_drive_ids_connector["drive_b_file_name"],
                external_record_group_id=drive_b_id,
            ),
        )
        rg_b = await graph_provider.get_record_group_by_external_id(
            connector_id, drive_b_id
        )
        assert rg_b is not None, (
            f"Shared Drive B RecordGroup missing after widen ({drive_b_id})"
        )

    @pytest.mark.order(7)
    async def test_tc_drive_ids_003_filter_options_page_through_shared_drives(
        self,
        drive_workspace_drive_ids_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-DRIVE-IDS-003 (GD SDF1): the drive picker lists every Shared Drive, one page at a time."""
        connector_id = drive_workspace_drive_ids_connector["connector_id"]
        expected = {
            drive_workspace_drive_ids_connector["drive_a_id"],
            drive_workspace_drive_ids_connector["drive_b_id"],
        }

        seen: list[str] = []
        cursor = None
        for _ in range(200):
            page = pipeshub_client.get_filter_options(
                connector_id, "drive_ids", limit=1, cursor=cursor
            )
            options = page.get("options") or []
            assert len(options) <= 1, f"limit=1 returned {len(options)} options"
            seen.extend(str(o["id"]) for o in options)
            if not page.get("hasMore"):
                break
            cursor = page.get("cursor")
            assert cursor, f"hasMore=true but no cursor on page {len(seen)}"
        else:
            pytest.fail("drive_ids options never reported hasMore=false after 200 pages")

        assert len(seen) == len(set(seen)), f"a Shared Drive was listed twice across pages: {seen}"
        missing = expected - set(seen)
        assert not missing, f"Shared Drives missing from the picker: {missing} (listed {len(seen)})"

        a_name = drive_workspace_drive_ids_connector["drive_a_name"]
        found = pipeshub_client.get_filter_options(connector_id, "drive_ids", search=a_name)
        ids = {str(o["id"]) for o in found.get("options") or []}
        assert ids == {drive_workspace_drive_ids_connector["drive_a_id"]}, (
            f"searching {a_name!r} returned {ids}, expected only Drive A"
        )

    @pytest.mark.order(8)
    async def test_tc_drive_ids_004_shrink_to_drive_b_drops_drive_a(
        self,
        drive_workspace_drive_ids_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-IDS-004 (GD SDF4): shrinking drive_ids to [B] removes Drive A's records."""
        connector_id = drive_workspace_drive_ids_connector["connector_id"]
        drive_b_id = drive_workspace_drive_ids_connector["drive_b_id"]
        file_a_id = drive_workspace_drive_ids_connector["drive_a_file_id"]
        file_b_id = drive_workspace_drive_ids_connector["drive_b_file_id"]

        await _apply_drive_ids_filter(pipeshub_client, graph_provider, connector_id, [drive_b_id])

        async def _a_out_b_in() -> bool:
            a = await graph_provider.get_record_by_external_id(connector_id, file_a_id)
            b = await graph_provider.get_record_by_external_id(connector_id, file_b_id)
            return a is None and b is not None

        await wait_until_graph_condition(
            connector_id,
            check=_a_out_b_in,
            timeout=_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description=f"drive_ids=[B]: Drive A file {file_a_id} gone, Drive B file {file_b_id} kept",
        )
