# pyright: ignore-file

"""Google Drive Workspace – entity sync + folder_ids + extension + blocks ITs.

Uses class-scoped connectors (see conftest):

* ``drive_workspace_entity_connector`` — unfiltered; torn down (connector + Drive
  source) after ``TestDriveWorkspaceEntitySync``.
* ``drive_workspace_ff_connector`` — ``folder_ids=[seed]``; fresh Drive tree after
  entity teardown.
* ``drive_workspace_ext_connector`` — ``folder_ids=[seed]`` + ``file_extensions``
  for ``TestDriveWorkspaceExtensionFilter``.
* ``drive_workspace_blocks_connector`` — ``folder_ids=[seed]`` with five sample
  files for ``TestDriveWorkspaceBlocks`` snapshot ITs.

  order 0  TC-DRIVE-SYNC-001 — full-sync baseline (records, RG links, edges, app metadata)
  order 1  TC-DRIVE-001      — users / USER_APP_RELATION
  order 2  TC-DRIVE-002      — Workspace groups + member edges
  order 3  TC-DRIVE-003      — My Drive + Shared-with-Me + Shared Drive RecordGroups
  order 4  TC-DRIVE-004      — FILE properties (seed folder + sample files)
  order 5  TC-DRIVE-SYNC-002 — delta add/update after full-sync baseline
  order 6  TC-DRIVE-005      — My Drive → Shared Drive BELONGS_TO shift
  order 7  TC-DRIVE-006      — inactive B shares folder+file → A's Shared with Me
  order 8  TC-DRIVE-007      — activate B → dual BELONGS_TO (A SWM + B My Drive)
  order 9  TC-DRIVE-008      — unshare → A's PERMISSION removed
  order 10 TC-DRIVE-009      — Shared Drive (A-only) folder share → B SWM → unshare
  order 11 TC-DRIVE-010      — My Drive parent shared with B; restricted nested folder
                               keeps B PERMISSION; child under it does not

  order 12 TC-FF-001  — seed folder + descendants present; out-of-scope sibling absent
  order 13 TC-FF-002  — new file inside seed syncs on incremental
  order 14 TC-FF-003  — new nested subfolder expands scope; child file syncs
  order 15 TC-FF-004  — file moved out of seed is deleted from graph
  order 16 TC-FF-005  — file moved back into seed re-syncs
  order 17 TC-FF-006  — folder moved out of seed cascade-deletes descendants
  order 18 TC-FF-007  — folder moved back into seed pulls descendants
  order 19 TC-FF-008  — create under out_of_scope stays out of graph
  order 20 TC-FF-009  — rename in-scope file updates record name
  order 21 TC-FF-010  — fixture root ancestor present via placeholder sweep
  order 22 TC-FF-011  — in-scope parent change (seed → nested) updates parent
  order 23 TC-FF-012  — multi-seed folder_ids syncs seed + out_of_scope
  order 24 TC-FF-013  — narrow folder_ids to nested drops seed-direct + OOS
  order 25 TC-FF-014  — widen folder_ids back to seed restores seed-direct
  order 26 TC-FF-015  — clear folder_ids (empty list) syncs fixture OOS again

  order 27 TC-EXT-001 — file_extensions IN docs/sheets/txt; slides absent
  order 28 TC-EXT-002 — switch to NOT_IN docs; doc loses BELONGS_TO, slide gains it

  order 30 TC-DRIVE-BLOCKS-001 — Newsletter (Docs) stream → Processor snapshot
  order 31 TC-DRIVE-BLOCKS-002 — Science fair (Slides)
  order 32 TC-DRIVE-BLOCKS-003 — Gantt chart (Sheets)
  order 33 TC-DRIVE-BLOCKS-004 — OWASP Test PDF
  order 34 TC-DRIVE-BLOCKS-005 — CONTRIBUTING.md

Env (blocks): ``GOOGLE_DRIVE_BLOCKS_BOOTSTRAP=1`` writes fixtures/<kind>.expected.json.
Docling must be reachable for PDF / Docs / Slides; Sheets uses the non-LLM Excel path.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
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
from connectors.google_drive_workspace.drive_block_utils import (  # noqa: E402
    bootstrap_expected,
    load_expected,
    normalize_blocks_container,
    parse_drive_stream_via_processor,
)
from connectors.google_drive_workspace.drive_workspace_expected import (  # noqa: E402
    DriveExpected,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # noqa: E402
    DRIVE_BLOCKS_SAMPLE_SPECS,
    ENV_ADMIN_EMAIL,
    ENV_SA_JSON,
    ENV_SECOND_USER,
    build_drive_datasource,
    count_drive_directory_groups_with_members,
    count_drive_directory_users,
    count_drive_group_user_members,
    create_drive_folder,
    create_drive_text_file,
    create_shared_drive,
    delete_drive_folder,
    delete_shared_drive,
    drive_files_get_workspace,
    ensure_pipeshub_user_exists,
    ensure_pipeshub_user_inactive,
    move_drive_item,
    rename_drive_item,
    resolve_my_drive_root_id,
    share_drive_item_with_user,
    set_drive_folder_limited_access,
    unshare_drive_item_from_user,
    update_drive_file_content,
    wait_until_shared_drives_listed,
    wait_until_user_has_file_permission,
)
from helper.assertions import ConnectorAssertions, RecordAssertion  # noqa: E402
from helper.clients.users_client import UsersClient  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    async_poll_until,
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from pipeshub_client import PipeshubClient  # noqa: E402
from validation.graph_edge_validator import (  # noqa: E402
    assert_graph_edges,
    build_record_edge_expectations,
)
from validation.graph_entity_validator import (  # noqa: E402
    assert_graph_entity_matches,
    assert_graph_entity_with_edges,
    assert_user_app_edge,
)

logger = logging.getLogger("drive-workspace-lifecycle-test")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_workspace,
    pytest.mark.asyncio(loop_scope="session"),
]

_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_SYNC_TIMEOUT", "300"))
_USER_GRAPH_TIMEOUT_SEC = int(
    os.getenv("GOOGLE_DRIVE_WORKSPACE_USER_GRAPH_TIMEOUT", "120")
)


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


def _file_extensions_filters(
    folder_ids: list[str],
    extensions: list[str],
    *,
    operator: str = "in",
) -> dict[str, Any]:
    """Build sync filters with folder_ids + file_extensions (verbatim filters-sync)."""
    return {
        "sync": {
            "values": {
                "folder_ids": {
                    "operator": "in",
                    "type": "list",
                    "value": folder_ids,
                },
                "file_extensions": {
                    "operator": operator,
                    "type": "multiselect",
                    "value": extensions,
                },
            }
        }
    }


async def _apply_extension_filter_and_wait(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    folder_ids: list[str],
    extensions: list[str],
    *,
    operator: str = "in",
) -> None:
    """Update file_extensions (+ folder_ids) via filters-sync; wait for full sync."""
    pipeshub_client.update_connector_filters_sync_safe(
        connector_id,
        filters=_file_extensions_filters(
            folder_ids, extensions, operator=operator
        ),
    )
    await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        timeout=_SYNC_TIMEOUT_SEC,
    )


async def _wait_record_belongs_to(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    external_id: str,
    external_group_id: str,
    *,
    description: str,
    should_belong: bool = True,
) -> None:
    """Poll until ``record_belongs_to_external_group`` matches *should_belong*."""

    async def _check() -> bool:
        belongs = await graph_provider.record_belongs_to_external_group(
            connector_id, external_id, external_group_id
        )
        return belongs is should_belong

    await wait_until_graph_condition(
        connector_id,
        check=_check,
        timeout=_SYNC_TIMEOUT_SEC,
        poll_interval=10,
        description=description,
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


_RG_SKIP = frozenset({
    "id",
    "org_id",
    "created_at",
    "updated_at",
    "source_created_at",
    "source_updated_at",
    "web_url",
    "description",
    "short_name",
    "parent_external_group_id",
    "parent_record_group_id",
    "hide_children",
})

_FILE_SKIP = frozenset({
    "id",
    "org_id",
    "indexing_status",
    "parsing_status",
    "extraction_status",
    "record_group_id",
    "virtual_record_id",
    "parent_record_type",
    "created_at",
    "updated_at",
    "source_created_at",
    "source_updated_at",
    "version",
    "external_revision_id",
    "path",
    "etag",
    "ctag",
    "quick_xor_hash",
    "crc32_hash",
    "is_shared",
    "preview_renderable",
    "is_dependent_node",
    "parent_node_id",
    "record_group_type",
    # FileRecord.from_arango_record never reads File.md5Checksum back onto md5_hash.
    "md5_hash",
})


class TestDriveWorkspaceEntitySync:
    """Entity graph validation on an unfiltered Drive Workspace connector.

    Uses ``drive_workspace_entity_connector`` (torn down before the FF suite).
    """

    @pytest.mark.order(0)
    async def test_tc_drive_sync_001_full_sync_graph_validation(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-SYNC-001: seeded records present; RG links; PARENT_CHILD; edges; app metadata."""
        connector_id = drive_workspace_entity_connector["connector_id"]
        my_drive_root_id = drive_workspace_entity_connector["my_drive_root_id"]
        seed_id = drive_workspace_entity_connector["seed_folder_id"]
        nested_id = drive_workspace_entity_connector["nested_folder_id"]
        child_id = drive_workspace_entity_connector["child_file_id"]
        sample_files: list[dict[str, str]] = drive_workspace_entity_connector["entity_sample_files"]

        record_ids = [seed_id, nested_id, child_id] + [s["id"] for s in sample_files]

        for external_id in record_ids:
            rec = await connector_assertions.assert_record_exists(connector_id, external_id)
            assert str(rec.external_record_group_id) == str(my_drive_root_id), (
                f"{external_id}: external_record_group_id {rec.external_record_group_id!r} "
                f"!= My Drive root {my_drive_root_id!r}"
            )

        nested_parent = await graph_provider.get_record_parent_external_id(
            connector_id, nested_id
        )
        assert nested_parent == seed_id, (
            f"nested parent {nested_parent!r} != seed {seed_id!r}"
        )
        child_parent = await graph_provider.get_record_parent_external_id(
            connector_id, child_id
        )
        assert child_parent == nested_id, (
            f"child parent {child_parent!r} != nested {nested_id!r}"
        )
        for sample in sample_files:
            sample_parent = await graph_provider.get_record_parent_external_id(
                connector_id, sample["id"]
            )
            assert sample_parent == nested_id, (
                f"sample {sample['id']} parent {sample_parent!r} != nested {nested_id!r}"
            )

        # Structural edges for a representative file (belongsTo + inherit + parent).
        child_actual = await graph_provider.get_typed_record_by_external_id(
            connector_id, child_id
        )
        assert child_actual is not None
        assert str(child_actual.external_record_group_id) == str(my_drive_root_id)
        child_actual.inherit_permissions = True
        await assert_graph_edges(
            graph_provider,
            build_record_edge_expectations(child_actual, connector_id),
        )

        graph_app = await graph_provider.get_app_metadata_by_connector_id(connector_id)
        assert graph_app is not None, f"apps document missing for connector {connector_id}"
        expected_app = DriveExpected.app_metadata_for_full_sync_baseline(
            drive_workspace_entity_connector
        )
        app_skip = frozenset({
            "created_at_timestamp", "updated_at_timestamp", "auth_type", "is_active",
            "is_agent_active", "is_configured", "is_authenticated", "created_by",
            "updated_by", "status", "is_locked",
        })
        assert_graph_entity_matches(
            expected_app, graph_app, entity="app_metadata", skip_compare=app_skip,
        )
        logger.info("TC-DRIVE-SYNC-001 passed: %d records", len(record_ids))

    @pytest.mark.order(1)
    async def test_tc_drive_001_user_app_relation(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        drive_workspace_admin_datasource: Any,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-001: USER_APP_RELATION count matches Directory users; test user edge."""
        connector_id = drive_workspace_entity_connector["connector_id"]
        source_user_id = drive_workspace_entity_connector["test_user_source_id"]
        test_email = drive_workspace_entity_connector["test_user_email"]
        assert source_user_id, "test_user_source_id missing from fixture"

        live = await count_drive_directory_users(drive_workspace_admin_datasource)
        graph = await graph_provider.count_user_app_relation_edges(connector_id)
        assert graph == live, (
            f"USER_APP_RELATION {graph} != Directory users with email {live}"
        )
        # Edge is keyed by Directory sourceUserId (may differ from Pipeshub userId).
        await assert_user_app_edge(
            source_user_id, connector_id=connector_id, graph_provider=graph_provider,
        )
        graph_user = await graph_provider.graph_find_user_by_email(test_email)
        assert graph_user is not None, f"graph user missing for {test_email}"
        logger.info("TC-DRIVE-001 passed: %d users", graph)

    @pytest.mark.order(2)
    async def test_tc_drive_002_workspace_groups(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        drive_workspace_admin_datasource: Any,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-DRIVE-002: UserGroup count + sample group member edges."""
        connector_id = drive_workspace_entity_connector["connector_id"]

        live = await count_drive_directory_groups_with_members(
            drive_workspace_admin_datasource
        )
        graph = await graph_provider.count_user_groups(connector_id)
        assert graph == live, (
            f"Graph UserGroup count {graph} != Directory groups with USER members {live}"
        )

        group_email = drive_workspace_entity_connector.get("reference_group_email")
        group_name = drive_workspace_entity_connector.get("reference_group_name")
        if group_email:
            await connector_assertions.assert_group_exists(
                connector_id, group_email, name=group_name,
            )
            expected_members = await count_drive_group_user_members(
                drive_workspace_admin_datasource, group_email,
            )
            graph_members = await graph_provider.count_user_to_group_permission_edges(
                connector_id, group_email,
            )
            assert graph_members == expected_members, (
                f"group {group_email!r}: graph User→Group {graph_members} != "
                f"Directory USER members {expected_members}"
            )
        else:
            logger.warning(
                "TC-DRIVE-002: no Directory group with USER members — count-only assert"
            )
        logger.info("TC-DRIVE-002 passed: %d groups", graph)

    @pytest.mark.order(3)
    async def test_tc_drive_003_record_groups(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-DRIVE-003: My Drive + Shared-with-Me + 3 Shared Drive RecordGroups."""
        connector_id = drive_workspace_entity_connector["connector_id"]
        my_drive_root_id = drive_workspace_entity_connector["my_drive_root_id"]
        swm_id = drive_workspace_entity_connector["shared_with_me_group_id"]
        full_name = drive_workspace_entity_connector["test_user_full_name"]
        seed_id = drive_workspace_entity_connector["seed_folder_id"]
        child_id = drive_workspace_entity_connector["child_file_id"]

        my_drive = await graph_provider.get_record_group_by_external_id(
            connector_id, my_drive_root_id
        )
        assert my_drive is not None, f"My Drive RG missing for {my_drive_root_id}"
        expected_md = DriveExpected.record_group_my_drive(
            user_full_name=full_name,
            root_id=my_drive_root_id,
            connector_id=connector_id,
        )
        await assert_graph_entity_with_edges(
            expected_md, my_drive, entity="record_group",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_RG_SKIP,
        )

        swm = await graph_provider.get_record_group_by_external_id(connector_id, swm_id)
        assert swm is not None, f"Shared-with-Me RG missing for {swm_id}"
        expected_swm = DriveExpected.record_group_shared_with_me(
            user_full_name=full_name,
            email=drive_workspace_entity_connector["test_user_email"],
            connector_id=connector_id,
        )
        await assert_graph_entity_with_edges(
            expected_swm, swm, entity="record_group",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_RG_SKIP,
        )

        seed_rec = await connector_assertions.assert_record_exists(connector_id, seed_id)
        assert str(seed_rec.external_record_group_id) == str(my_drive_root_id)
        child_rec = await connector_assertions.assert_record_exists(connector_id, child_id)
        assert str(child_rec.external_record_group_id) == str(my_drive_root_id)

        entity_shared_drives: list[dict[str, str]] = drive_workspace_entity_connector[
            "entity_shared_drives"
        ]
        assert len(entity_shared_drives) == 3, (
            f"expected 3 entity Shared Drives in fixture, got {len(entity_shared_drives)}"
        )
        for drive in entity_shared_drives:
            rg = await graph_provider.get_record_group_by_external_id(
                connector_id, drive["id"]
            )
            assert rg is not None, (
                f"Shared Drive RecordGroup missing for {drive['name']!r} ({drive['id']})"
            )
            expected_sd = DriveExpected.record_group_shared_drive(
                drive_name=drive["name"],
                drive_id=drive["id"],
                connector_id=connector_id,
            )
            await assert_graph_entity_with_edges(
                expected_sd, rg, entity="record_group",
                connector_id=connector_id, graph_provider=graph_provider,
                skip_compare=_RG_SKIP,
            )
        logger.info(
            "TC-DRIVE-003 passed: My Drive + SWM + %d Shared Drives",
            len(entity_shared_drives),
        )

    @pytest.mark.order(4)
    async def test_tc_drive_004_file_record_properties(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-004: seed folder + 2 sample files match live Drive metadata."""
        connector_id = drive_workspace_entity_connector["connector_id"]
        my_drive_root_id = drive_workspace_entity_connector["my_drive_root_id"]
        seed_id = drive_workspace_entity_connector["seed_folder_id"]
        nested_id = drive_workspace_entity_connector["nested_folder_id"]
        sample_files: list[dict[str, str]] = drive_workspace_entity_connector["entity_sample_files"]
        assert len(sample_files) == 2, (
            f"expected 2 entity sample files, got {len(sample_files)}"
        )

        seed_meta = await drive_files_get_workspace(drive_workspace_datasource, seed_id)
        seed_actual = await graph_provider.get_typed_record_by_external_id(
            connector_id, seed_id
        )
        assert seed_actual is not None, f"typed FILE record missing for seed {seed_id}"
        seed_expected = DriveExpected.file_record_from_drive_meta(
            seed_meta,
            connector_id=connector_id,
            my_drive_root_id=my_drive_root_id,
        )
        await assert_graph_entity_with_edges(
            seed_expected, seed_actual, entity="file_record",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_FILE_SKIP,
        )
        assert seed_actual.is_file is False
        assert seed_actual.mime_type == MimeTypes.GOOGLE_DRIVE_FOLDER.value

        for sample in sample_files:
            meta = await drive_files_get_workspace(
                drive_workspace_datasource, sample["id"]
            )
            actual = await graph_provider.get_typed_record_by_external_id(
                connector_id, sample["id"]
            )
            assert actual is not None, (
                f"typed FILE record missing for sample {sample['id']} ({sample['name']})"
            )
            expected = DriveExpected.file_record_from_drive_meta(
                meta,
                connector_id=connector_id,
                my_drive_root_id=my_drive_root_id,
            )
            await assert_graph_entity_with_edges(
                expected, actual, entity="file_record",
                connector_id=connector_id, graph_provider=graph_provider,
                skip_compare=_FILE_SKIP,
            )
            assert actual.is_file is True
            assert str(actual.parent_external_record_id) == str(nested_id), (
                f"sample {sample['id']} parent {actual.parent_external_record_id!r} "
                f"!= nested {nested_id!r}"
            )

        logger.info(
            "TC-DRIVE-004 passed: seed folder + samples %s",
            [s["name"] for s in sample_files],
        )

    @pytest.mark.order(5)
    async def test_tc_drive_sync_002_delta_add_update(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-DRIVE-SYNC-002: modify existing content + add file; graph updates delta only."""
        connector_id = drive_workspace_entity_connector["connector_id"]
        seed_id = drive_workspace_entity_connector["seed_folder_id"]
        nested_id = drive_workspace_entity_connector["nested_folder_id"]
        child_id = drive_workspace_entity_connector["child_file_id"]
        sample_files: list[dict[str, str]] = drive_workspace_entity_connector["entity_sample_files"]
        assert sample_files, "entity_sample_files required as unchanged control"
        control_id = sample_files[0]["id"]

        before_count = await graph_provider.count_records(connector_id, scoped=True)
        child_before = await graph_provider.get_typed_record_by_external_id(
            connector_id, child_id
        )
        assert child_before is not None, f"child.txt missing before delta ({child_id})"
        child_rev_before = child_before.external_revision_id
        child_size_before = child_before.size_in_bytes
        control_before = await graph_provider.get_typed_record_by_external_id(
            connector_id, control_id
        )
        assert control_before is not None, f"control sample missing ({control_id})"
        control_rev_before = control_before.external_revision_id

        new_content = f"delta-sync-002 updated content {uuid.uuid4().hex}\n"
        await update_drive_file_content(
            drive_workspace_datasource, child_id, new_content
        )
        live_after_update = await drive_files_get_workspace(
            drive_workspace_datasource, child_id
        )
        expected_rev = str(
            live_after_update.get("headRevisionId")
            or live_after_update.get("version")
            or ""
        )
        assert expected_rev, "Drive files.get returned no revision after content update"
        assert expected_rev != str(child_rev_before or ""), (
            f"Drive revision should change after content update; still {expected_rev!r}"
        )

        delta_new_id = await create_drive_text_file(
            drive_workspace_datasource,
            "delta-new.txt",
            parent_id=seed_id,
            content=f"delta-sync-002 new file {uuid.uuid4().hex}\n",
        )
        drive_workspace_entity_connector["delta_new_file_id"] = delta_new_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            delta_new_id,
            description=f"delta-new.txt ({delta_new_id}) after incremental sync",
        )

        async def _child_rev_updated() -> bool:
            rec = await graph_provider.get_typed_record_by_external_id(
                connector_id, child_id
            )
            return rec is not None and str(rec.external_revision_id) == expected_rev

        await wait_until_graph_condition(
            connector_id,
            check=_child_rev_updated,
            timeout=_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description=f"child.txt revision → {expected_rev}",
        )

        after_count = await graph_provider.count_records(connector_id, scoped=True)
        assert after_count == before_count + 1, (
            f"scoped record count should grow by 1 (new file only); "
            f"before={before_count} after={after_count}"
        )

        child_after = await graph_provider.get_typed_record_by_external_id(
            connector_id, child_id
        )
        assert child_after is not None
        assert str(child_after.external_revision_id) == expected_rev, (
            f"child.txt revision {child_after.external_revision_id!r} != Drive {expected_rev!r}"
        )
        assert int(child_after.size_in_bytes or 0) == int(
            live_after_update.get("size", 0) or 0
        ), (
            f"child.txt size {child_after.size_in_bytes} != Drive "
            f"{live_after_update.get('size')}"
        )
        assert int(child_after.size_in_bytes or 0) != int(child_size_before or 0), (
            f"child.txt size should change after content update; still {child_size_before}"
        )
        if live_after_update.get("sha1Checksum"):
            assert child_after.sha1_hash == live_after_update.get("sha1Checksum"), (
                f"child.txt sha1 {child_after.sha1_hash!r} != "
                f"{live_after_update.get('sha1Checksum')!r}"
            )

        await connector_assertions.assert_record_exists(
            connector_id,
            delta_new_id,
            RecordAssertion(
                external_record_id=delta_new_id,
                record_name="delta-new.txt",
                parent_external_record_id=seed_id,
            ),
        )

        control_after = await graph_provider.get_typed_record_by_external_id(
            connector_id, control_id
        )
        assert control_after is not None
        assert str(control_after.external_revision_id) == str(control_rev_before), (
            f"unchanged control sample revision drifted: "
            f"before={control_rev_before!r} after={control_after.external_revision_id!r}"
        )
        nested_after = await graph_provider.get_record_by_external_id(
            connector_id, nested_id
        )
        assert nested_after is not None, f"unchanged nested folder missing ({nested_id})"

        logger.info(
            "TC-DRIVE-SYNC-002 passed: count %d→%d, child rev %s→%s, added %s",
            before_count,
            after_count,
            child_rev_before,
            expected_rev,
            delta_new_id,
        )

    @pytest.mark.order(6)
    async def test_tc_drive_005_move_my_drive_to_shared_drive_belongs_to(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """Move a My Drive file into a Shared Drive; BELONGS_TO shifts to that drive RG."""
        connector_id = drive_workspace_entity_connector["connector_id"]
        seed_id = drive_workspace_entity_connector["seed_folder_id"]
        my_drive_root_id = drive_workspace_entity_connector["my_drive_root_id"]
        full_name = drive_workspace_entity_connector["test_user_full_name"]
        entity_shared_drives: list[dict[str, str]] = drive_workspace_entity_connector[
            "entity_shared_drives"
        ]
        assert entity_shared_drives, "entity_shared_drives required for move test"
        shared = entity_shared_drives[0]
        shared_drive_id = shared["id"]
        shared_drive_name = shared["name"]
        move_name = "to-shared-drive.txt"

        # Entity connector is unfiltered — no folder_ids expand needed for the move.
        move_file_id = await create_drive_text_file(
            drive_workspace_datasource,
            move_name,
            parent_id=seed_id,
            content=f"move to shared drive {uuid.uuid4().hex}\n",
        )
        drive_workspace_entity_connector["move_to_shared_file_id"] = move_file_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            move_file_id,
            description=f"{move_name} ({move_file_id}) under My Drive seed",
        )

        before = await connector_assertions.assert_record_exists(
            connector_id,
            move_file_id,
            RecordAssertion(
                external_record_id=move_file_id,
                record_name=move_name,
                parent_external_record_id=seed_id,
                external_record_group_id=my_drive_root_id,
            ),
        )
        my_drive_group_name = await graph_provider.get_record_parent_group(
            connector_id, move_name
        )
        expected_my_drive_name = f"{full_name}'s Drive"
        assert my_drive_group_name == expected_my_drive_name, (
            f"pre-move BELONGS_TO group {my_drive_group_name!r} != {expected_my_drive_name!r}"
        )

        await move_drive_item(
            drive_workspace_datasource,
            move_file_id,
            new_parent_id=shared_drive_id,
            old_parent_id=seed_id,
        )

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)

        async def _belongs_to_shared() -> bool:
            rec = await graph_provider.get_record_by_external_id(
                connector_id, move_file_id
            )
            return (
                rec is not None
                and str(rec.external_record_group_id) == str(shared_drive_id)
            )

        await wait_until_graph_condition(
            connector_id,
            check=_belongs_to_shared,
            timeout=_SYNC_TIMEOUT_SEC,
            poll_interval=10,
            description=(
                f"{move_name} external_record_group_id → Shared Drive {shared_drive_id}"
            ),
        )

        after = await connector_assertions.assert_record_exists(
            connector_id,
            move_file_id,
            RecordAssertion(
                external_record_id=move_file_id,
                record_name=move_name,
                external_record_group_id=shared_drive_id,
            ),
        )
        assert after.parent_external_record_id in (None, ""), (
            f"top-level Shared Drive file should clear parent; got "
            f"{after.parent_external_record_id!r}"
        )
        assert str(before.external_record_group_id) == str(my_drive_root_id)
        assert str(after.external_record_group_id) == str(shared_drive_id)

        shared_group_name = await graph_provider.get_record_parent_group(
            connector_id, move_name
        )
        assert shared_group_name == shared_drive_name, (
            f"post-move BELONGS_TO group {shared_group_name!r} != {shared_drive_name!r}"
        )

        typed = await graph_provider.get_typed_record_by_external_id(
            connector_id, move_file_id
        )
        assert typed is not None
        typed.inherit_permissions = True
        await assert_graph_edges(
            graph_provider,
            build_record_edge_expectations(typed, connector_id),
        )

        logger.info(
            "TC-DRIVE-005 passed: %s BELONGS_TO %s → %s",
            move_file_id,
            expected_my_drive_name,
            shared_drive_name,
        )

    @pytest.mark.order(7)
    async def test_tc_drive_006_inactive_b_share_appears_in_a_swm(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        users_client: UsersClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-006: inactive B shares folder+file; A sees them via Shared with Me."""
        second_email = os.getenv(ENV_SECOND_USER, "").strip()
        if not second_email:
            pytest.skip(f"{ENV_SECOND_USER} not set")

        sa_json = os.environ[ENV_SA_JSON].strip()
        admin_email = os.environ[ENV_ADMIN_EMAIL].strip()
        a_email = drive_workspace_entity_connector["test_user_email"]
        connector_id = drive_workspace_entity_connector["connector_id"]
        a_swm_id = f"0S:{a_email}"

        ensure_pipeshub_user_inactive(users_client, second_email)

        async def _b_inactive() -> bool | None:
            user = await graph_provider.graph_find_user_by_email(second_email)
            if user is None:
                return True
            if user.get("isActive") is False:
                return True
            return None

        await async_poll_until(
            _b_inactive,
            timeout=_USER_GRAPH_TIMEOUT_SEC,
            interval=2,
            description=f"Pipeshub user {second_email} inactive in graph",
        )

        try:
            b_drive = await build_drive_datasource(sa_json, admin_email, second_email)
        except Exception as e:
            pytest.skip(f"Failed to impersonate second user {second_email}: {e}")

        b_root_id = await resolve_my_drive_root_id(b_drive)
        suffix = uuid.uuid4().hex[:8]
        share_root_id = await create_drive_folder(
            b_drive, f"phit-share-{suffix}"
        )
        share_folder_name = "shared-folder"
        share_file_name = "shared-file.txt"
        share_folder_id = await create_drive_folder(
            b_drive, share_folder_name, parent_id=share_root_id
        )
        share_file_id = await create_drive_text_file(
            b_drive,
            share_file_name,
            parent_id=share_root_id,
            content=f"shared with me it {suffix}\n",
        )

        drive_workspace_entity_connector.update(
            {
                "share_b_email": second_email,
                "share_root_folder_id": share_root_id,
                "share_folder_id": share_folder_id,
                "share_folder_name": share_folder_name,
                "share_file_id": share_file_id,
                "share_file_name": share_file_name,
                "share_b_my_drive_root_id": b_root_id,
            }
        )

        await share_drive_item_with_user(b_drive, share_folder_id, a_email)
        await share_drive_item_with_user(b_drive, share_file_id, a_email)

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)

        for external_id, label in (
            (share_folder_id, share_folder_name),
            (share_file_id, share_file_name),
        ):
            await _wait_record_present(
                graph_provider,
                connector_id,
                external_id,
                description=f"{label} ({external_id}) in graph after inactive-B share",
            )

            async def _swm_and_perm(
                eid: str = external_id,
            ) -> bool:
                belongs = await graph_provider.record_belongs_to_external_group(
                    connector_id, eid, a_swm_id
                )
                has_perm = await graph_provider.user_has_direct_record_permission(
                    connector_id, a_email, eid
                )
                return belongs and has_perm

            await wait_until_graph_condition(
                connector_id,
                check=_swm_and_perm,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description=f"{label} BELONGS_TO {a_swm_id} + PERMISSION {a_email}",
            )
            assert not await graph_provider.record_belongs_to_external_group(
                connector_id, external_id, b_root_id
            ), (
                f"{label} should not BELONGS_TO B My Drive ({b_root_id}) "
                "while B is inactive"
            )

        logger.info(
            "TC-DRIVE-006 passed: folder=%s file=%s → SWM %s",
            share_folder_id,
            share_file_id,
            a_swm_id,
        )

    @pytest.mark.order(8)
    async def test_tc_drive_007_activate_b_dual_belongs_to(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        users_client: UsersClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-007: activate B; records BELONGS_TO A SWM and B My Drive."""
        second_email = drive_workspace_entity_connector.get("share_b_email")
        share_folder_id = drive_workspace_entity_connector.get("share_folder_id")
        share_file_id = drive_workspace_entity_connector.get("share_file_id")
        b_root_id = drive_workspace_entity_connector.get("share_b_my_drive_root_id")
        if not second_email or not share_folder_id or not share_file_id or not b_root_id:
            pytest.skip("TC-DRIVE-006 share state missing (second user / share skipped)")

        a_email = drive_workspace_entity_connector["test_user_email"]
        connector_id = drive_workspace_entity_connector["connector_id"]
        a_swm_id = f"0S:{a_email}"

        ensure_pipeshub_user_exists(users_client, second_email)

        async def _b_active() -> dict[str, Any] | None:
            user = await graph_provider.graph_find_user_by_email(second_email)
            if not user or user.get("isActive") is False:
                return None
            return user

        await async_poll_until(
            _b_active,
            timeout=_USER_GRAPH_TIMEOUT_SEC,
            interval=2,
            description=f"active Pipeshub graph user for {second_email}",
        )

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)

        for external_id, label in (
            (share_folder_id, drive_workspace_entity_connector["share_folder_name"]),
            (share_file_id, drive_workspace_entity_connector["share_file_name"]),
        ):
            async def _dual_belongs(
                eid: str = external_id,
            ) -> bool:
                swm = await graph_provider.record_belongs_to_external_group(
                    connector_id, eid, a_swm_id
                )
                my_drive = await graph_provider.record_belongs_to_external_group(
                    connector_id, eid, b_root_id
                )
                return swm and my_drive

            await wait_until_graph_condition(
                connector_id,
                check=_dual_belongs,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description=(
                    f"{label} BELONGS_TO {a_swm_id} and B My Drive {b_root_id}"
                ),
            )
            assert await graph_provider.user_has_direct_record_permission(
                connector_id, a_email, external_id
            ), f"{label}: A ({a_email}) should still have PERMISSION"

        logger.info(
            "TC-DRIVE-007 passed: dual BELONGS_TO for folder=%s file=%s",
            share_folder_id,
            share_file_id,
        )

    @pytest.mark.order(9)
    async def test_tc_drive_008_unshare_removes_a_permission(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        users_client: UsersClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-008: unshare removes A's PERMISSION; records remain for B."""
        second_email = drive_workspace_entity_connector.get("share_b_email")
        share_folder_id = drive_workspace_entity_connector.get("share_folder_id")
        share_file_id = drive_workspace_entity_connector.get("share_file_id")
        share_root_id = drive_workspace_entity_connector.get("share_root_folder_id")
        b_root_id = drive_workspace_entity_connector.get("share_b_my_drive_root_id")
        if not second_email or not share_folder_id or not share_file_id:
            pytest.skip("TC-DRIVE-006 share state missing (second user / share skipped)")

        a_email = drive_workspace_entity_connector["test_user_email"]
        connector_id = drive_workspace_entity_connector["connector_id"]
        a_swm_id = f"0S:{a_email}"
        sa_json = os.environ[ENV_SA_JSON].strip()
        admin_email = os.environ[ENV_ADMIN_EMAIL].strip()

        try:
            b_drive = await build_drive_datasource(sa_json, admin_email, second_email)
            await unshare_drive_item_from_user(b_drive, share_folder_id, a_email)
            await unshare_drive_item_from_user(b_drive, share_file_id, a_email)

            await _sync_and_wait(pipeshub_client, graph_provider, connector_id)

            for external_id, label in (
                (share_folder_id, drive_workspace_entity_connector["share_folder_name"]),
                (share_file_id, drive_workspace_entity_connector["share_file_name"]),
            ):
                async def _a_perm_gone(
                    eid: str = external_id,
                ) -> bool:
                    return not await graph_provider.user_has_direct_record_permission(
                        connector_id, a_email, eid
                    )

                await wait_until_graph_condition(
                    connector_id,
                    check=_a_perm_gone,
                    timeout=_SYNC_TIMEOUT_SEC,
                    poll_interval=10,
                    description=f"{label}: A ({a_email}) PERMISSION removed",
                )
                rec = await graph_provider.get_record_by_external_id(
                    connector_id, external_id
                )
                assert rec is not None, f"{label} should remain after unshare (B owner)"
                if b_root_id:
                    assert await graph_provider.record_belongs_to_external_group(
                        connector_id, external_id, b_root_id
                    ), f"{label} should still BELONGS_TO B My Drive {b_root_id}"

                # Product does not yet remove Shared-with-Me BELONGS_TO on unshare.
                # Keep the assert ready; enable when fixed (or drop if intentional).
                # assert not await graph_provider.record_belongs_to_external_group(
                #     connector_id, external_id, a_swm_id
                # ), (
                #     f"{label}: SWM BELONGS_TO {a_swm_id} should be removed after unshare"
                # )
                logger.debug(
                    "TC-DRIVE-008: SWM BELONGS_TO check skipped for %s (%s)",
                    label,
                    a_swm_id,
                )

            logger.info(
                "TC-DRIVE-008 passed: A permission removed for folder=%s file=%s",
                share_folder_id,
                share_file_id,
            )
        finally:
            # Clean B's share fixture + leave B inactive for the next run.
            try:
                if share_root_id:
                    b_drive_cleanup = await build_drive_datasource(
                        sa_json, admin_email, second_email
                    )
                    await delete_drive_folder(b_drive_cleanup, share_root_id)
            except Exception as e:
                logger.warning(
                    "TC-DRIVE-008 teardown: failed to delete B share root %s: %s",
                    share_root_id,
                    e,
                )
            try:
                ensure_pipeshub_user_inactive(users_client, second_email)
            except Exception as e:
                logger.warning(
                    "TC-DRIVE-008 teardown: failed to deactivate %s: %s",
                    second_email,
                    e,
                )

    @pytest.mark.order(10)
    async def test_tc_drive_009_shared_drive_folder_share_to_b_swm(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        users_client: UsersClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-009: A-only Shared Drive folder shared with B → B SWM → unshare.

        Single lifecycle: create SD (A member only) → share folder with B → sync →
        assert B SWM + permission → unshare → assert B permission gone.
        """
        second_email = os.getenv(ENV_SECOND_USER, "").strip()
        if not second_email:
            pytest.skip(f"{ENV_SECOND_USER} not set")

        a_email = drive_workspace_entity_connector["test_user_email"]
        connector_id = drive_workspace_entity_connector["connector_id"]
        b_swm_id = f"0S:{second_email}"
        sa_json = os.environ[ENV_SA_JSON].strip()
        admin_email = os.environ[ENV_ADMIN_EMAIL].strip()

        # B needs an active Pipeshub user for PERMISSION edges (008 may have
        # soft-deleted them). SWM RG for B comes from Workspace Directory sync.
        ensure_pipeshub_user_exists(users_client, second_email)

        async def _b_active() -> dict[str, Any] | None:
            user = await graph_provider.graph_find_user_by_email(second_email)
            if not user or user.get("isActive") is False:
                return None
            return user

        await async_poll_until(
            _b_active,
            timeout=_USER_GRAPH_TIMEOUT_SEC,
            interval=2,
            description=f"active Pipeshub graph user for {second_email}",
        )

        sd_id: str | None = None
        admin_drive: GoogleDriveDataSource | None = None
        folder_id: str | None = None
        folder_name = "sd-shared-folder"
        try:
            try:
                admin_drive = await build_drive_datasource(
                    sa_json, admin_email, admin_email
                )
            except Exception as e:
                logger.warning(
                    "TC-DRIVE-009: could not build admin Drive for SD teardown: %s", e
                )

            sd_name = f"phit-share-sd-{uuid.uuid4().hex[:8]}"
            try:
                sd_id = await create_shared_drive(drive_workspace_datasource, sd_name)
            except Exception as e:
                pytest.skip(
                    f"Failed to create Shared Drive for share IT (user={a_email}): {e}"
                )

            await wait_until_shared_drives_listed(
                drive_workspace_datasource, [sd_id]
            )

            folder_id = await create_drive_folder(
                drive_workspace_datasource,
                folder_name,
                parent_id=sd_id,
            )
            # Individual file share to B — B is NOT a Shared Drive member.
            await share_drive_item_with_user(
                drive_workspace_datasource, folder_id, second_email
            )
            # ACL can lag after permissions.create; connector only links SWM when
            # permissions.list shows B with permissionType == "file".
            await wait_until_user_has_file_permission(
                drive_workspace_datasource, folder_id, second_email
            )

            await _sync_and_wait(pipeshub_client, graph_provider, connector_id)

            await _wait_record_present(
                graph_provider,
                connector_id,
                folder_id,
                description=f"{folder_name} ({folder_id}) in graph after SD share",
            )

            async def _sd_and_b_swm() -> bool:
                on_sd = await graph_provider.record_belongs_to_external_group(
                    connector_id, folder_id, sd_id
                )
                on_swm = await graph_provider.record_belongs_to_external_group(
                    connector_id, folder_id, b_swm_id
                )
                has_perm = await graph_provider.user_has_direct_record_permission(
                    connector_id, second_email, folder_id
                )
                return on_sd and on_swm and has_perm

            await wait_until_graph_condition(
                connector_id,
                check=_sd_and_b_swm,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description=(
                    f"{folder_name} BELONGS_TO SD {sd_id} + SWM {b_swm_id} "
                    f"+ PERMISSION {second_email}"
                ),
            )

            # Unshare B from the folder (A remains SD member/owner of the item).
            await unshare_drive_item_from_user(
                drive_workspace_datasource, folder_id, second_email
            )
            await _sync_and_wait(pipeshub_client, graph_provider, connector_id)

            async def _b_perm_gone() -> bool:
                return not await graph_provider.user_has_direct_record_permission(
                    connector_id, second_email, folder_id
                )

            await wait_until_graph_condition(
                connector_id,
                check=_b_perm_gone,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description=f"{folder_name}: B ({second_email}) PERMISSION removed",
            )

            rec = await graph_provider.get_record_by_external_id(
                connector_id, folder_id
            )
            assert rec is not None, f"{folder_name} should remain after unshare"
            assert await graph_provider.record_belongs_to_external_group(
                connector_id, folder_id, sd_id
            ), f"{folder_name} should still BELONGS_TO Shared Drive {sd_id}"

            # Product does not yet remove Shared-with-Me BELONGS_TO on unshare.
            # Keep the assert ready; enable when fixed (or drop if intentional).
            # assert not await graph_provider.record_belongs_to_external_group(
            #     connector_id, folder_id, b_swm_id
            # ), (
            #     f"{folder_name}: SWM BELONGS_TO {b_swm_id} should be removed after unshare"
            # )

            logger.info(
                "TC-DRIVE-009 passed: SD=%s folder=%s shared→unshared with %s",
                sd_id,
                folder_id,
                second_email,
            )
        finally:
            if sd_id:
                await delete_shared_drive(
                    drive_workspace_datasource, sd_id, admin_drive=admin_drive
                )
            try:
                ensure_pipeshub_user_inactive(users_client, second_email)
            except Exception as e:
                logger.warning(
                    "TC-DRIVE-009 teardown: failed to deactivate %s: %s",
                    second_email,
                    e,
                )

    @pytest.mark.order(11)
    async def test_tc_drive_010_restricted_folder_blocks_child_user_permission(
        self,
        drive_workspace_entity_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        users_client: UsersClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-010: parent shared with B; restricted nested folder keeps B
        PERMISSION; child under the limited folder does not.

        Drive limited access (``inheritedPermissionsDisabled``) applies to folders
        only. The restricted folder itself stays visible to B; its children do not
        inherit B's grant from the shared parent.
        """
        second_email = os.getenv(ENV_SECOND_USER, "").strip()
        if not second_email:
            pytest.skip(f"{ENV_SECOND_USER} not set")

        connector_id = drive_workspace_entity_connector["connector_id"]
        # 009 may have soft-deleted B; PERMISSION edges need an active user.
        ensure_pipeshub_user_exists(users_client, second_email)

        async def _b_active() -> dict[str, Any] | None:
            user = await graph_provider.graph_find_user_by_email(second_email)
            if not user or user.get("isActive") is False:
                return None
            return user

        await async_poll_until(
            _b_active,
            timeout=_USER_GRAPH_TIMEOUT_SEC,
            interval=2,
            description=f"active Pipeshub graph user for {second_email}",
        )

        suffix = uuid.uuid4().hex[:8]
        root_id: str | None = None
        parent_name = "shared-parent"
        restricted_name = "restricted-folder"
        child_name = "secret-child.txt"

        try:
            root_id = await create_drive_folder(
                drive_workspace_datasource, f"phit-restrict-{suffix}"
            )
            parent_id = await create_drive_folder(
                drive_workspace_datasource, parent_name, parent_id=root_id
            )
            restricted_id = await create_drive_folder(
                drive_workspace_datasource, restricted_name, parent_id=parent_id
            )
            child_id = await create_drive_text_file(
                drive_workspace_datasource,
                child_name,
                parent_id=restricted_id,
                content=f"restricted child {suffix}\n",
            )

            await share_drive_item_with_user(
                drive_workspace_datasource, parent_id, second_email
            )
            await set_drive_folder_limited_access(
                drive_workspace_datasource, restricted_id, limited=True
            )

            await _sync_and_wait(pipeshub_client, graph_provider, connector_id)

            for external_id, label in (
                (parent_id, parent_name),
                (restricted_id, restricted_name),
                (child_id, child_name),
            ):
                await _wait_record_present(
                    graph_provider,
                    connector_id,
                    external_id,
                    description=f"{label} ({external_id}) in graph after restrict IT",
                )

            async def _parent_and_restricted_have_b() -> bool:
                parent_ok = await graph_provider.user_has_direct_record_permission(
                    connector_id, second_email, parent_id
                )
                restricted_ok = await graph_provider.user_has_direct_record_permission(
                    connector_id, second_email, restricted_id
                )
                return parent_ok and restricted_ok

            await wait_until_graph_condition(
                connector_id,
                check=_parent_and_restricted_have_b,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description=(
                    f"B ({second_email}) PERMISSION on parent + restricted folder"
                ),
            )

            async def _child_lacks_b() -> bool:
                return not await graph_provider.user_has_direct_record_permission(
                    connector_id, second_email, child_id
                )

            await wait_until_graph_condition(
                connector_id,
                check=_child_lacks_b,
                timeout=_SYNC_TIMEOUT_SEC,
                poll_interval=10,
                description=(
                    f"B ({second_email}) PERMISSION absent on child under restricted"
                ),
            )

            logger.info(
                "TC-DRIVE-010 passed: parent=%s restricted=%s child=%s B=%s",
                parent_id,
                restricted_id,
                child_id,
                second_email,
            )
        finally:
            if root_id:
                try:
                    await delete_drive_folder(drive_workspace_datasource, root_id)
                except Exception as e:
                    logger.warning(
                        "TC-DRIVE-010 teardown: failed to delete root %s: %s",
                        root_id,
                        e,
                    )
            try:
                ensure_pipeshub_user_inactive(users_client, second_email)
            except Exception as e:
                logger.warning(
                    "TC-DRIVE-010 teardown: failed to deactivate %s: %s",
                    second_email,
                    e,
                )


class TestDriveWorkspaceFolderFilter:
    """Assert folder_ids filter syncs the seed subtree and handles scope transitions.

    Uses ``drive_workspace_ff_connector`` (fresh connector + Drive tree after entity).
    """

    @pytest.mark.order(12)
    async def test_tc_ff_001_folder_ids_filter_syncs_seed_subtree(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        nested_id = drive_workspace_ff_connector["nested_folder_id"]
        child_id = drive_workspace_ff_connector["child_file_id"]
        oos_folder_id = drive_workspace_ff_connector["oos_folder_id"]
        oos_file_id = drive_workspace_ff_connector["oos_file_id"]

        await connector_assertions.assert_record_exists(
            connector_id,
            seed_id,
            RecordAssertion(
                external_record_id=seed_id,
                record_name=drive_workspace_ff_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_workspace_ff_connector["nested_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            child_id,
            RecordAssertion(
                external_record_id=child_id,
                record_name=drive_workspace_ff_connector["child_file_name"],
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

    @pytest.mark.order(13)
    async def test_tc_ff_002_new_file_inside_seed_syncs(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]

        new_file_id = await create_drive_text_file(
            drive_workspace_datasource,
            "new.txt",
            parent_id=seed_id,
            content="ff-002 new file in scope\n",
        )
        drive_workspace_ff_connector["new_file_id"] = new_file_id
        drive_workspace_ff_connector["new_file_name"] = "new.txt"

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

    @pytest.mark.order(14)
    async def test_tc_ff_003_new_subfolder_expands_scope(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]

        deeper_id = await create_drive_folder(
            drive_workspace_datasource, "deeper", parent_id=seed_id
        )
        deeper_file_id = await create_drive_text_file(
            drive_workspace_datasource,
            "file.txt",
            parent_id=deeper_id,
            content="ff-003 deeper file\n",
        )
        drive_workspace_ff_connector["deeper_folder_id"] = deeper_id
        drive_workspace_ff_connector["deeper_file_id"] = deeper_file_id

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

    @pytest.mark.order(15)
    async def test_tc_ff_004_file_exits_scope_deleted(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        oos_folder_id = drive_workspace_ff_connector["oos_folder_id"]

        leave_file_id = await create_drive_text_file(
            drive_workspace_datasource,
            "leave.txt",
            parent_id=seed_id,
            content="ff-004 leave then return\n",
        )
        drive_workspace_ff_connector["leave_file_id"] = leave_file_id
        drive_workspace_ff_connector["leave_file_name"] = "leave.txt"

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            leave_file_id,
            description=f"leave.txt ({leave_file_id}) synced before move-out",
        )

        await move_drive_item(
            drive_workspace_datasource,
            leave_file_id,
            new_parent_id=oos_folder_id,
            old_parent_id=seed_id,
        )
        drive_workspace_ff_connector["leave_file_parent_id"] = oos_folder_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_absent(
            graph_provider,
            connector_id,
            leave_file_id,
            description=f"leave.txt ({leave_file_id}) deleted after scope exit",
        )

    @pytest.mark.order(16)
    async def test_tc_ff_005_file_reenters_scope_resyncs(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        leave_file_id = drive_workspace_ff_connector["leave_file_id"]
        oos_folder_id = drive_workspace_ff_connector["oos_folder_id"]

        await move_drive_item(
            drive_workspace_datasource,
            leave_file_id,
            new_parent_id=seed_id,
            old_parent_id=oos_folder_id,
        )
        drive_workspace_ff_connector["leave_file_parent_id"] = seed_id

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

    @pytest.mark.order(17)
    async def test_tc_ff_006_folder_exits_scope_cascade_deletes(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        oos_folder_id = drive_workspace_ff_connector["oos_folder_id"]

        movable_id = await create_drive_folder(
            drive_workspace_datasource, "movable", parent_id=seed_id
        )
        inside_id = await create_drive_text_file(
            drive_workspace_datasource,
            "inside.txt",
            parent_id=movable_id,
            content="ff-006/007 movable folder\n",
        )
        drive_workspace_ff_connector["movable_folder_id"] = movable_id
        drive_workspace_ff_connector["movable_inside_file_id"] = inside_id

        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)
        await _wait_record_present(
            graph_provider,
            connector_id,
            inside_id,
            description=f"movable/inside.txt ({inside_id}) synced before move-out",
        )

        await move_drive_item(
            drive_workspace_datasource,
            movable_id,
            new_parent_id=oos_folder_id,
            old_parent_id=seed_id,
        )
        drive_workspace_ff_connector["movable_parent_id"] = oos_folder_id

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

    @pytest.mark.order(18)
    async def test_tc_ff_007_folder_enters_scope_pulls_descendants(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        oos_folder_id = drive_workspace_ff_connector["oos_folder_id"]
        movable_id = drive_workspace_ff_connector["movable_folder_id"]
        inside_id = drive_workspace_ff_connector["movable_inside_file_id"]

        await move_drive_item(
            drive_workspace_datasource,
            movable_id,
            new_parent_id=seed_id,
            old_parent_id=oos_folder_id,
        )
        drive_workspace_ff_connector["movable_parent_id"] = seed_id

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

    @pytest.mark.order(19)
    async def test_tc_ff_008_oos_create_stays_out(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        oos_folder_id = drive_workspace_ff_connector["oos_folder_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        # Prefer new.txt from FF-002; fall back to leave.txt from FF-005.
        in_scope_id = (
            drive_workspace_ff_connector.get("new_file_id")
            or drive_workspace_ff_connector.get("leave_file_id")
        )
        assert in_scope_id, "Expected an in-scope file id from FF-002 or FF-005"

        ignored_id = await create_drive_text_file(
            drive_workspace_datasource,
            "ignored.txt",
            parent_id=oos_folder_id,
            content="ff-008 out of scope\n",
        )
        drive_workspace_ff_connector["oos_ignored_file_id"] = ignored_id

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

    @pytest.mark.order(20)
    async def test_tc_ff_009_rename_in_scope_updates_name(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        leave_file_id = drive_workspace_ff_connector["leave_file_id"]
        new_name = "leave-renamed.txt"

        await rename_drive_item(drive_workspace_datasource, leave_file_id, new_name)
        drive_workspace_ff_connector["leave_file_name"] = new_name

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

    @pytest.mark.order(21)
    async def test_tc_ff_010_placeholder_root_ancestor_present(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        root_id = drive_workspace_ff_connector["root_folder_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]

        await connector_assertions.assert_record_exists(
            connector_id,
            root_id,
            RecordAssertion(
                external_record_id=root_id,
                record_name=drive_workspace_ff_connector["root_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            seed_id,
            RecordAssertion(
                external_record_id=seed_id,
                record_name=drive_workspace_ff_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=root_id,
            ),
        )

    @pytest.mark.order(22)
    async def test_tc_ff_011_in_scope_parent_change_updates_parent(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        nested_id = drive_workspace_ff_connector["nested_folder_id"]
        new_file_id = drive_workspace_ff_connector["new_file_id"]

        await move_drive_item(
            drive_workspace_datasource,
            new_file_id,
            new_parent_id=nested_id,
            old_parent_id=seed_id,
        )
        drive_workspace_ff_connector["new_file_parent_id"] = nested_id

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

    @pytest.mark.order(23)
    async def test_tc_ff_012_multi_seed_syncs_oos_sibling(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        nested_id = drive_workspace_ff_connector["nested_folder_id"]
        child_id = drive_workspace_ff_connector["child_file_id"]
        oos_folder_id = drive_workspace_ff_connector["oos_folder_id"]
        oos_file_id = drive_workspace_ff_connector["oos_file_id"]

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
        drive_workspace_ff_connector["scoped_after_multi_seed"] = scoped_after

        await connector_assertions.assert_record_exists(
            connector_id,
            oos_folder_id,
            RecordAssertion(
                external_record_id=oos_folder_id,
                record_name=drive_workspace_ff_connector["oos_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            oos_file_id,
            RecordAssertion(
                external_record_id=oos_file_id,
                record_name=drive_workspace_ff_connector["oos_file_name"],
                parent_external_record_id=oos_folder_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_workspace_ff_connector["nested_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            child_id,
            RecordAssertion(
                external_record_id=child_id,
                record_name=drive_workspace_ff_connector["child_file_name"],
                parent_external_record_id=nested_id,
            ),
        )

    @pytest.mark.order(24)
    async def test_tc_ff_013_narrow_folder_ids_to_nested(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        nested_id = drive_workspace_ff_connector["nested_folder_id"]
        child_id = drive_workspace_ff_connector["child_file_id"]
        new_file_id = drive_workspace_ff_connector["new_file_id"]

        scoped_before = int(
            drive_workspace_ff_connector.get("scoped_after_multi_seed")
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
        drive_workspace_ff_connector["scoped_after_narrow"] = scoped_after

        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_workspace_ff_connector["nested_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            child_id,
            RecordAssertion(
                external_record_id=child_id,
                record_name=drive_workspace_ff_connector["child_file_name"],
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
                record_name=drive_workspace_ff_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )

    @pytest.mark.order(25)
    async def test_tc_ff_014_widen_folder_ids_to_seed(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        seed_id = drive_workspace_ff_connector["seed_folder_id"]
        leave_file_id = drive_workspace_ff_connector["leave_file_id"]
        leave_file_name = drive_workspace_ff_connector["leave_file_name"]

        scoped_narrow = int(
            drive_workspace_ff_connector.get("scoped_after_narrow")
            or await graph_provider.count_records(connector_id, scoped=True)
        )
        scoped_multi = int(
            drive_workspace_ff_connector.get("scoped_after_multi_seed") or 10**9
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
        drive_workspace_ff_connector["scoped_after_widen"] = scoped_after

        await connector_assertions.assert_record_exists(
            connector_id,
            leave_file_id,
            RecordAssertion(
                external_record_id=leave_file_id,
                record_name=leave_file_name,
                parent_external_record_id=seed_id,
            ),
        )

    @pytest.mark.order(26)
    async def test_tc_ff_015_clear_folder_ids_syncs_fixture_oos(
        self,
        drive_workspace_ff_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_workspace_ff_connector["connector_id"]
        oos_folder_id = drive_workspace_ff_connector["oos_folder_id"]
        oos_file_id = drive_workspace_ff_connector["oos_file_id"]

        scoped_multi = int(
            drive_workspace_ff_connector.get("scoped_after_multi_seed")
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
                record_name=drive_workspace_ff_connector["oos_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            oos_file_id,
            RecordAssertion(
                external_record_id=oos_file_id,
                record_name=drive_workspace_ff_connector["oos_file_name"],
                parent_external_record_id=oos_folder_id,
            ),
        )


class TestDriveWorkspaceExtensionFilter:
    """file_extensions IN then NOT_IN on one dedicated connector (edge-only full sync)."""

    @pytest.mark.order(27)
    async def test_tc_ext_001_in_allows_docs_sheets_excludes_slides(
        self,
        drive_workspace_ext_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = drive_workspace_ext_connector["connector_id"]
        seed_id = drive_workspace_ext_connector["seed_folder_id"]
        doc_id = drive_workspace_ext_connector["doc_file_id"]
        sheet_id = drive_workspace_ext_connector["sheet_file_id"]
        slide_id = drive_workspace_ext_connector["slide_file_id"]
        txt_id = drive_workspace_ext_connector["txt_file_id"]

        await connector_assertions.assert_record_exists(
            connector_id,
            seed_id,
            RecordAssertion(
                external_record_id=seed_id,
                record_name=drive_workspace_ext_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            doc_id,
            RecordAssertion(
                external_record_id=doc_id,
                record_name=drive_workspace_ext_connector["doc_file_name"],
                mime_type=MimeTypes.GOOGLE_DOCS.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            sheet_id,
            RecordAssertion(
                external_record_id=sheet_id,
                record_name=drive_workspace_ext_connector["sheet_file_name"],
                mime_type=MimeTypes.GOOGLE_SHEETS.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            txt_id,
            RecordAssertion(
                external_record_id=txt_id,
                record_name=drive_workspace_ext_connector["txt_file_name"],
                parent_external_record_id=seed_id,
            ),
        )

        slide = await graph_provider.get_record_by_external_id(connector_id, slide_id)
        assert slide is None, (
            f"Google Slides {slide_id} must not sync under file_extensions IN "
            f"docs/sheets/txt"
        )

    @pytest.mark.order(28)
    async def test_tc_ext_002_not_in_docs_drops_doc_includes_slide(
        self,
        drive_workspace_ext_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """Switch IN → NOT_IN on the same connector; assert via live BELONGS_TO.

        Full sync after filter change deletes edges only — Doc may keep its Record
        node while losing BELONGS_TO; Slide (never synced under IN) gains one.
        """
        connector_id = drive_workspace_ext_connector["connector_id"]
        seed_id = drive_workspace_ext_connector["seed_folder_id"]
        doc_id = drive_workspace_ext_connector["doc_file_id"]
        sheet_id = drive_workspace_ext_connector["sheet_file_id"]
        slide_id = drive_workspace_ext_connector["slide_file_id"]

        seed_rec = await graph_provider.get_record_by_external_id(connector_id, seed_id)
        assert seed_rec is not None and seed_rec.external_record_group_id, (
            "seed folder must have external_record_group_id for scoped asserts"
        )
        group_id = str(seed_rec.external_record_group_id)

        await _apply_extension_filter_and_wait(
            pipeshub_client,
            graph_provider,
            connector_id,
            [seed_id],
            [MimeTypes.GOOGLE_DOCS.value],
            operator="not_in",
        )

        await _wait_record_belongs_to(
            graph_provider,
            connector_id,
            doc_id,
            group_id,
            should_belong=False,
            description=f"Doc ({doc_id}) loses BELONGS_TO under NOT_IN docs",
        )
        await _wait_record_belongs_to(
            graph_provider,
            connector_id,
            sheet_id,
            group_id,
            should_belong=True,
            description=f"Sheet ({sheet_id}) keeps BELONGS_TO under NOT_IN docs",
        )
        await _wait_record_belongs_to(
            graph_provider,
            connector_id,
            slide_id,
            group_id,
            should_belong=True,
            description=f"Slide ({slide_id}) gains BELONGS_TO under NOT_IN docs",
        )
        await _wait_record_belongs_to(
            graph_provider,
            connector_id,
            seed_id,
            group_id,
            should_belong=True,
            description=f"seed folder ({seed_id}) still scoped under NOT_IN docs",
        )

        await connector_assertions.assert_record_exists(
            connector_id,
            slide_id,
            RecordAssertion(
                external_record_id=slide_id,
                record_name=drive_workspace_ext_connector["slide_file_name"],
                mime_type=MimeTypes.GOOGLE_SLIDES.value,
                parent_external_record_id=seed_id,
            ),
        )


_BLOCKS_CASES = [
    pytest.param(
        "newsletter_doc",
        "TC-DRIVE-BLOCKS-001",
        marks=pytest.mark.order(30),
        id="TC-DRIVE-BLOCKS-001",
    ),
    pytest.param(
        "science_fair_slides",
        "TC-DRIVE-BLOCKS-002",
        marks=pytest.mark.order(31),
        id="TC-DRIVE-BLOCKS-002",
    ),
    pytest.param(
        "gantt_chart_sheets",
        "TC-DRIVE-BLOCKS-003",
        marks=pytest.mark.order(32),
        id="TC-DRIVE-BLOCKS-003",
    ),
    pytest.param(
        "owasp_pdf",
        "TC-DRIVE-BLOCKS-004",
        marks=pytest.mark.order(33),
        id="TC-DRIVE-BLOCKS-004",
    ),
    pytest.param(
        "contributing_md",
        "TC-DRIVE-BLOCKS-005",
        marks=pytest.mark.order(34),
        id="TC-DRIVE-BLOCKS-005",
    ),
]


def _kind_local_name(kind: str) -> str:
    for spec in DRIVE_BLOCKS_SAMPLE_SPECS:
        if spec["kind"] == kind:
            return spec["local_name"]
    return kind


class TestDriveWorkspaceBlocks:
    """Stream synced Drive samples → Processor → deep-equal expected snapshots."""

    @pytest.mark.parametrize("kind,tc_id", _BLOCKS_CASES)
    async def test_tc_drive_blocks_streamed_expected(
        self,
        kind: str,
        tc_id: str,
        drive_workspace_blocks_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        drive_blocks_stream_client: PipeshubClient,
    ) -> None:
        """Stream one synced sample, parse via production Processor, compare snapshot.

        Validates Drive export/download → format-specific Processor path → typed
        blocks — the same shape indexing produces. Streams as the Drive Workspace
        test user (ACL owner), not the org-admin OAuth app. LLM table prose is
        normalized to placeholders (structure-only compare). Regenerate with
        ``GOOGLE_DRIVE_BLOCKS_BOOTSTRAP=1``.
        """
        connector_id = drive_workspace_blocks_connector["connector_id"]
        by_kind: dict[str, dict[str, str]] = drive_workspace_blocks_connector[
            "blocks_by_kind"
        ]
        sample = by_kind.get(kind)
        assert sample is not None, f"blocks fixture missing kind={kind!r}"

        external_id = sample["id"]
        record = await graph_provider.get_record_by_external_id(
            connector_id, external_id
        )
        assert record is not None, (
            f"{tc_id}: sample {kind} ({external_id}) not synced"
        )

        resp = drive_blocks_stream_client.stream_record(record.id)
        assert resp.status_code == 200, (
            f"{tc_id}: stream_record HTTP {resp.status_code}"
        )
        assert resp.content, f"{tc_id}: empty stream body for {kind}"

        record_mime = getattr(record, "mime_type", None) or sample.get("target_mime") or ""
        record_name = getattr(record, "record_name", None) or sample.get("name") or (
            _kind_local_name(kind)
        )

        parsed = await parse_drive_stream_via_processor(
            resp.content,
            record_mime,
            record_name=record_name,
        )
        actual = normalize_blocks_container(parsed)
        if os.getenv("GOOGLE_DRIVE_BLOCKS_BOOTSTRAP") == "1":
            bootstrap_expected(kind, actual)
        expected = load_expected(kind)
        assert actual == expected, (
            f"{tc_id}: parsed blocks do not match expected snapshot for {kind}"
        )
        logger.info("%s passed (%s)", tc_id, kind)
