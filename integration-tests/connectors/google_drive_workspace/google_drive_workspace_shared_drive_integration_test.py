# pyright: ignore-file

"""Google Drive Workspace – Shared Drive folder_ids lean integration tests.

Creates/tears down temporary Shared Drives. Covers only Shared Drive-specific
plumbing; full leave/return/rename matrix lives in the My Drive suite.

  order 1  TC-SD-FF-001  — seed + descendants present; OOS + Drive B ignored
  order 2  TC-SD-FF-002  — new nested subfolder expands scope (corpora=drive)
  order 3  TC-SD-FF-003  — new file under seed syncs on incremental
  order 4  TC-SD-FF-004  — folder_ids=[Shared Drive root] expands top-level tree
"""

from __future__ import annotations

import os
import sys
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


def _restart_sync(pipeshub_client: PipeshubClient, connector_id: str) -> None:
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
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_workspace_shared_drive_connector["nested_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
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

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            deeper_id,
            description=f"deeper folder ({deeper_id}) after Shared Drive expansion",
        )
        await _wait_record_present(
            graph_provider,
            connector_id,
            deeper_child_id,
            description=f"deeper-child.txt ({deeper_child_id}) after Shared Drive expansion",
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

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            new_file_id,
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
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
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
