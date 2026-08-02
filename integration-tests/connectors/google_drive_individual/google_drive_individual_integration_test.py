# pyright: ignore-file

"""Google Drive personal – entity sync + folder_ids + extension + blocks ITs.

Uses class-scoped connectors (see conftest):

* ``drive_individual_entity_connector`` — unfiltered; torn down (connector +
  Drive source) after ``TestDriveIndividualEntitySync``.
* ``drive_individual_ff_connector`` — ``folder_ids=[seed]``; fresh Drive tree
  after entity teardown.
* ``drive_individual_ext_connector`` — ``folder_ids=[seed]`` + ``file_extensions``
  for ``TestDriveIndividualExtensionFilter``.
* ``drive_individual_blocks_connector`` — ``folder_ids=[seed]`` with five sample
  files for ``TestDriveIndividualBlocks`` snapshot ITs.

  order 0  TC-DRIVE-IND-SYNC-001 — full-sync baseline (records, RG links, edges, app metadata)
  order 1  TC-DRIVE-IND-001      — the single app user + USER_APP_RELATION
  order 2  TC-DRIVE-IND-002      — no Workspace-only entities (groups / SWM / extra RGs)
  order 3  TC-DRIVE-IND-003      — the one "Google Drive - {email}" RecordGroup
  order 4  TC-DRIVE-IND-004      — FILE properties (seed folder + sample files)
  order 5  TC-DRIVE-IND-SYNC-002 — delta add/update after full-sync baseline

  order 6  TC-FF-001  — seed folder + descendants present; out-of-scope sibling absent
  order 7  TC-FF-002  — new file inside seed syncs on incremental
  order 8  TC-FF-003  — new nested subfolder expands scope; child file syncs
  order 9  TC-FF-004  — file moved out of seed is deleted from graph
  order 10 TC-FF-005  — file moved back into seed re-syncs
  order 11 TC-FF-006  — folder moved out of seed cascade-deletes descendants
  order 12 TC-FF-007  — folder moved back into seed pulls descendants
  order 13 TC-FF-008  — create under out_of_scope stays out of graph
  order 14 TC-FF-009  — rename in-scope file updates record name
  order 15 TC-FF-010  — fixture root ancestor present via placeholder sweep
  order 16 TC-FF-011  — in-scope parent change (seed → nested) updates parent
  order 17 TC-FF-012  — multi-seed folder_ids syncs seed + out_of_scope
  order 18 TC-FF-013  — narrow folder_ids to nested drops seed-direct + OOS
  order 19 TC-FF-014  — widen folder_ids back to seed restores seed-direct
  order 20 TC-FF-015  — clear folder_ids (empty list) syncs fixture OOS again

  order 21 TC-EXT-001 — file_extensions IN docs/sheets/txt; slides absent
  order 22 TC-EXT-002 — switch to NOT_IN docs; doc loses BELONGS_TO, slide gains it

  order 23 TC-DRIVE-IND-BLOCKS-001 — Newsletter (Docs) stream → Processor snapshot
  order 24 TC-DRIVE-IND-BLOCKS-002 — Science fair (Slides)
  order 25 TC-DRIVE-IND-BLOCKS-003 — Gantt chart (Sheets)
  order 26 TC-DRIVE-IND-BLOCKS-004 — CONTRIBUTING.md
  (PDF has no snapshot — Docling layout is not a Drive export contract)

  order 28-32 TC-DRIVE-IND-IDX-001..005 — same five samples (incl. PDF) reach
                                          indexingStatus COMPLETED via the live pipeline

Blocks snapshots are the Workspace ``fixtures/<kind>.expected.json`` files — same
samples, same export mimes, same Processor path. A mismatch is a real personal-vs-
workspace divergence, not a fixture that needs forking. Docling must be reachable
for Docs / Slides; Sheets uses the non-LLM Excel path. PDF is covered by IDX only.

The blocks connector runs with auto-indexing ON so the IDX suite can share its
five records; the snapshot tests parse in-process and are unaffected.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import (  # type: ignore[import-not-found]  # noqa: E402
    MimeTypes,
    ProgressStatus,
)
from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]  # noqa: E402
    GoogleDriveDataSource,
)
from connectors.google_drive_individual.drive_individual_expected import (  # noqa: E402
    DriveIndividualExpected,
)
from connectors.google_drive_individual.drive_individual_test_utils import (  # noqa: E402
    drive_files_get_personal,
)
from connectors.google_drive_workspace.drive_block_utils import (  # noqa: E402
    bootstrap_expected,
    load_expected,
    normalize_blocks_container,
    parse_drive_stream_via_processor,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # noqa: E402
    DRIVE_BLOCKS_SAMPLE_SPECS,
    create_drive_folder,
    create_drive_text_file,
    move_drive_item,
    rename_drive_item,
    update_drive_file_content,
)
from helper.assertions import ConnectorAssertions, RecordAssertion  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    sync_until_condition,
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.indexing_wait import (  # noqa: E402
    wait_until_record_indexing_completed,
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

logger = logging.getLogger("drive-individual-lifecycle-test")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_individual,
    pytest.mark.asyncio(loop_scope="session"),
]

_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_INDIVIDUAL_SYNC_TIMEOUT", "300"))
_INDEXING_WAIT_SEC = int(os.getenv("GOOGLE_DRIVE_INDIVIDUAL_INDEXING_WAIT", "300"))
# Drive Changes API often lags writes; settle then re-sync until the graph matches.
_DRIVE_CHANGES_SETTLE_SEC = int(os.getenv("GOOGLE_DRIVE_CHANGES_SETTLE", "15"))
_DRIVE_CHANGES_RETRY_GAP_SEC = int(os.getenv("GOOGLE_DRIVE_CHANGES_RETRY_GAP", "15"))


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


async def _settle_drive_changes() -> None:
    """Pause so Drive Changes API can publish recent writes before the next sync."""
    if _DRIVE_CHANGES_SETTLE_SEC <= 0:
        return
    logger.info(
        "Waiting %ds for Drive Changes API settle...",
        _DRIVE_CHANGES_SETTLE_SEC,
    )
    await asyncio.sleep(_DRIVE_CHANGES_SETTLE_SEC)


async def _sync_until(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    *,
    check: Callable[[], Awaitable[bool]],
    description: str,
) -> None:
    """Settle, sync, and re-sync until *check* passes (Drive Changes lag)."""

    async def _sync() -> None:
        await _sync_and_wait(pipeshub_client, graph_provider, connector_id)

    await sync_until_condition(
        connector_id,
        sync_fn=_sync,
        check=check,
        timeout=_SYNC_TIMEOUT_SEC,
        settle_sec=_DRIVE_CHANGES_SETTLE_SEC,
        retry_gap_sec=_DRIVE_CHANGES_RETRY_GAP_SEC,
        description=description,
    )


async def _sync_until_record_present(
    pipeshub_client: PipeshubClient,
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

    await _sync_until(
        pipeshub_client,
        graph_provider,
        connector_id,
        check=_present,
        description=description,
    )


async def _sync_until_record_absent(
    pipeshub_client: PipeshubClient,
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

    await _sync_until(
        pipeshub_client,
        graph_provider,
        connector_id,
        check=_absent,
        description=description,
    )


async def _sync_until_records_present(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    external_ids: list[str],
    *,
    description: str,
) -> None:
    async def _present() -> bool:
        for external_id in external_ids:
            if (
                await graph_provider.get_record_by_external_id(
                    connector_id, external_id
                )
                is None
            ):
                return False
        return True

    await _sync_until(
        pipeshub_client,
        graph_provider,
        connector_id,
        check=_present,
        description=description,
    )


async def _sync_until_records_absent(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    external_ids: list[str],
    *,
    description: str,
) -> None:
    async def _absent() -> bool:
        for external_id in external_ids:
            if (
                await graph_provider.get_record_by_external_id(
                    connector_id, external_id
                )
                is not None
            ):
                return False
        return True

    await _sync_until(
        pipeshub_client,
        graph_provider,
        connector_id,
        check=_absent,
        description=description,
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
        filters=_file_extensions_filters(folder_ids, extensions, operator=operator),
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
        return bool(belongs) is should_belong

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
    # The personal connector bumps version on every update; the builder emits 0.
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


class TestDriveIndividualEntitySync:
    """Entity graph validation on an unfiltered Drive personal connector.

    Uses ``drive_individual_entity_connector`` (torn down before the FF suite).
    """

    @pytest.mark.order(0)
    async def test_tc_drive_ind_sync_001_full_sync_graph_validation(
        self,
        drive_individual_entity_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-IND-SYNC-001: seeded records present; RG links; PARENT_CHILD; edges; app metadata."""
        connector_id = drive_individual_entity_connector["connector_id"]
        my_drive_root_id = drive_individual_entity_connector["my_drive_root_id"]
        seed_id = drive_individual_entity_connector["seed_folder_id"]
        nested_id = drive_individual_entity_connector["nested_folder_id"]
        child_id = drive_individual_entity_connector["child_file_id"]
        sample_files: list[dict[str, str]] = drive_individual_entity_connector["entity_sample_files"]

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
        await assert_graph_edges(
            graph_provider,
            build_record_edge_expectations(child_actual, connector_id),
        )

        graph_app = await graph_provider.get_app_metadata_by_connector_id(connector_id)
        assert graph_app is not None, f"apps document missing for connector {connector_id}"
        expected_app = DriveIndividualExpected.app_metadata_for_full_sync_baseline(
            drive_individual_entity_connector
        )
        app_skip = frozenset({
            "created_at_timestamp", "updated_at_timestamp", "auth_type", "is_active",
            "is_agent_active", "is_configured", "is_authenticated", "created_by",
            "updated_by", "status", "is_locked",
        })
        assert_graph_entity_matches(
            expected_app, graph_app, entity="app_metadata", skip_compare=app_skip,
        )
        logger.info("TC-DRIVE-IND-SYNC-001 passed: %d records", len(record_ids))

    @pytest.mark.order(1)
    async def test_tc_drive_ind_001_single_app_user(
        self,
        drive_individual_entity_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-IND-001: exactly one AppUser (the OAuth account) + its USER_APP_RELATION."""
        connector_id = drive_individual_entity_connector["connector_id"]
        source_user_id = drive_individual_entity_connector["test_user_source_id"]
        test_email = drive_individual_entity_connector["test_user_email"]
        assert source_user_id, "test_user_source_id (about.get permissionId) missing"

        # Exact, not a floor: the personal connector creates one AppUser from about.get.
        edges = await graph_provider.count_user_app_relation_edges(connector_id)
        assert edges == 1, (
            f"personal Drive connector must create exactly 1 USER_APP_RELATION, got {edges}"
        )
        await assert_user_app_edge(
            source_user_id, connector_id=connector_id, graph_provider=graph_provider,
        )
        graph_user = await graph_provider.graph_find_user_by_email(test_email)
        assert graph_user is not None, f"graph user missing for {test_email}"
        logger.info("TC-DRIVE-IND-001 passed: source_user_id=%s", source_user_id)

    @pytest.mark.order(2)
    async def test_tc_drive_ind_002_no_workspace_entities(
        self,
        drive_individual_entity_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-IND-002: no groups, no Shared-with-Me group, exactly one RecordGroup.

        Guards against the Workspace connector's Directory/Shared-Drive entity
        code leaking onto the personal path, which syncs none of it.
        """
        connector_id = drive_individual_entity_connector["connector_id"]
        email = drive_individual_entity_connector["test_user_email"]

        groups = await graph_provider.count_user_groups(connector_id)
        assert groups == 0, (
            f"personal Drive connector must not sync user groups, got {groups}"
        )

        record_groups = await graph_provider.count_record_groups(connector_id)
        assert record_groups == 1, (
            f"personal Drive connector creates exactly one My Drive RecordGroup, "
            f"got {record_groups}"
        )

        swm = await graph_provider.get_record_group_by_external_id(
            connector_id, f"0S:{email}"
        )
        assert swm is None, (
            f"personal Drive connector must not create a Shared-with-Me group, got {swm}"
        )
        logger.info("TC-DRIVE-IND-002 passed: 0 groups, 1 record group, no SWM")

    @pytest.mark.order(3)
    async def test_tc_drive_ind_003_personal_record_group(
        self,
        drive_individual_entity_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-DRIVE-IND-003: the single "Google Drive - {email}" RecordGroup + its edges."""
        connector_id = drive_individual_entity_connector["connector_id"]
        my_drive_root_id = drive_individual_entity_connector["my_drive_root_id"]
        seed_id = drive_individual_entity_connector["seed_folder_id"]
        child_id = drive_individual_entity_connector["child_file_id"]
        # The RG name comes from about.get emailAddress, which may differ in case
        # from GOOGLE_DRIVE_TEST_USER_EMAIL.
        drive_email = drive_individual_entity_connector["drive_account_email"]

        my_drive = await graph_provider.get_record_group_by_external_id(
            connector_id, my_drive_root_id
        )
        assert my_drive is not None, f"My Drive RG missing for {my_drive_root_id}"
        expected = DriveIndividualExpected.record_group_personal(
            email=drive_email,
            root_id=my_drive_root_id,
            connector_id=connector_id,
        )
        await assert_graph_entity_with_edges(
            expected, my_drive, entity="record_group",
            connector_id=connector_id, graph_provider=graph_provider,
            skip_compare=_RG_SKIP,
        )

        seed_rec = await connector_assertions.assert_record_exists(connector_id, seed_id)
        assert str(seed_rec.external_record_group_id) == str(my_drive_root_id)
        child_rec = await connector_assertions.assert_record_exists(connector_id, child_id)
        assert str(child_rec.external_record_group_id) == str(my_drive_root_id)
        logger.info("TC-DRIVE-IND-003 passed: RG %r", expected.name)

    @pytest.mark.order(4)
    async def test_tc_drive_ind_004_file_record_properties(
        self,
        drive_individual_entity_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRIVE-IND-004: seed folder + 2 sample files match live Drive metadata."""
        connector_id = drive_individual_entity_connector["connector_id"]
        my_drive_root_id = drive_individual_entity_connector["my_drive_root_id"]
        seed_id = drive_individual_entity_connector["seed_folder_id"]
        nested_id = drive_individual_entity_connector["nested_folder_id"]
        sample_files: list[dict[str, str]] = drive_individual_entity_connector["entity_sample_files"]
        assert len(sample_files) == 2, (
            f"expected 2 entity sample files, got {len(sample_files)}"
        )

        seed_meta = await drive_files_get_personal(drive_individual_datasource, seed_id)
        seed_actual = await graph_provider.get_typed_record_by_external_id(
            connector_id, seed_id
        )
        assert seed_actual is not None, f"typed FILE record missing for seed {seed_id}"
        seed_expected = DriveIndividualExpected.file_record_from_drive_meta(
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
            meta = await drive_files_get_personal(
                drive_individual_datasource, sample["id"]
            )
            actual = await graph_provider.get_typed_record_by_external_id(
                connector_id, sample["id"]
            )
            assert actual is not None, (
                f"typed FILE record missing for sample {sample['id']} ({sample['name']})"
            )
            expected = DriveIndividualExpected.file_record_from_drive_meta(
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
            "TC-DRIVE-IND-004 passed: seed folder + samples %s",
            [s["name"] for s in sample_files],
        )

    @pytest.mark.order(5)
    async def test_tc_drive_ind_sync_002_delta_add_update(
        self,
        drive_individual_entity_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """TC-DRIVE-IND-SYNC-002: modify existing content + add file; graph updates delta only."""
        connector_id = drive_individual_entity_connector["connector_id"]
        seed_id = drive_individual_entity_connector["seed_folder_id"]
        nested_id = drive_individual_entity_connector["nested_folder_id"]
        child_id = drive_individual_entity_connector["child_file_id"]
        sample_files: list[dict[str, str]] = drive_individual_entity_connector["entity_sample_files"]
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
            drive_individual_datasource, child_id, new_content
        )
        live_after_update = await drive_files_get_personal(
            drive_individual_datasource, child_id
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
            drive_individual_datasource,
            "delta-new.txt",
            parent_id=seed_id,
            content=f"delta-sync-002 new file {uuid.uuid4().hex}\n",
        )
        drive_individual_entity_connector["delta_new_file_id"] = delta_new_id

        async def _delta_synced() -> bool:
            new_rec = await graph_provider.get_record_by_external_id(
                connector_id, delta_new_id
            )
            if new_rec is None:
                return False
            rec = await graph_provider.get_typed_record_by_external_id(
                connector_id, child_id
            )
            return rec is not None and str(rec.external_revision_id) == expected_rev

        await _sync_until(
            pipeshub_client,
            graph_provider,
            connector_id,
            check=_delta_synced,
            description=(
                f"delta-new.txt ({delta_new_id}) present and "
                f"child.txt revision → {expected_rev}"
            ),
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
            "TC-DRIVE-IND-SYNC-002 passed: count %d→%d, child rev %s→%s, added %s",
            before_count,
            after_count,
            child_rev_before,
            expected_rev,
            delta_new_id,
        )


class TestDriveIndividualFolderFilter:
    """Assert folder_ids filter syncs the seed subtree and handles scope transitions."""

    @pytest.mark.order(6)
    async def test_tc_ff_001_folder_ids_filter_syncs_seed_subtree(
        self,
        drive_individual_ff_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        nested_id = drive_individual_ff_connector["nested_folder_id"]
        child_id = drive_individual_ff_connector["child_file_id"]
        oos_folder_id = drive_individual_ff_connector["oos_folder_id"]
        oos_file_id = drive_individual_ff_connector["oos_file_id"]

        await connector_assertions.assert_record_exists(
            connector_id,
            seed_id,
            RecordAssertion(
                external_record_id=seed_id,
                record_name=drive_individual_ff_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_individual_ff_connector["nested_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            child_id,
            RecordAssertion(
                external_record_id=child_id,
                record_name=drive_individual_ff_connector["child_file_name"],
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

    @pytest.mark.order(7)
    async def test_tc_ff_002_new_file_inside_seed_syncs(
        self,
        drive_individual_ff_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]

        new_file_id = await create_drive_text_file(
            drive_individual_datasource,
            "new.txt",
            parent_id=seed_id,
            content="ff-002 new file in scope\n",
        )
        drive_individual_ff_connector["new_file_id"] = new_file_id
        drive_individual_ff_connector["new_file_name"] = "new.txt"

        await _sync_until_record_present(
            pipeshub_client,
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

    @pytest.mark.order(8)
    async def test_tc_ff_003_new_subfolder_expands_scope(
        self,
        drive_individual_ff_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]

        deeper_id = await create_drive_folder(
            drive_individual_datasource, "deeper", parent_id=seed_id
        )
        deeper_file_id = await create_drive_text_file(
            drive_individual_datasource,
            "file.txt",
            parent_id=deeper_id,
            content="ff-003 deeper file\n",
        )
        drive_individual_ff_connector["deeper_folder_id"] = deeper_id
        drive_individual_ff_connector["deeper_file_id"] = deeper_file_id

        await _sync_until_record_present(
            pipeshub_client,
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

    @pytest.mark.order(9)
    async def test_tc_ff_004_file_exits_scope_deleted(
        self,
        drive_individual_ff_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        oos_folder_id = drive_individual_ff_connector["oos_folder_id"]

        leave_file_id = await create_drive_text_file(
            drive_individual_datasource,
            "leave.txt",
            parent_id=seed_id,
            content="ff-004 leave then return\n",
        )
        drive_individual_ff_connector["leave_file_id"] = leave_file_id
        drive_individual_ff_connector["leave_file_name"] = "leave.txt"

        await _sync_until_record_present(
            pipeshub_client,
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
        drive_individual_ff_connector["leave_file_parent_id"] = oos_folder_id

        await _sync_until_record_absent(
            pipeshub_client,
            graph_provider,
            connector_id,
            leave_file_id,
            description=f"leave.txt ({leave_file_id}) deleted after scope exit",
        )

    @pytest.mark.order(10)
    async def test_tc_ff_005_file_reenters_scope_resyncs(
        self,
        drive_individual_ff_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        leave_file_id = drive_individual_ff_connector["leave_file_id"]
        oos_folder_id = drive_individual_ff_connector["oos_folder_id"]

        await move_drive_item(
            drive_individual_datasource,
            leave_file_id,
            new_parent_id=seed_id,
            old_parent_id=oos_folder_id,
        )
        drive_individual_ff_connector["leave_file_parent_id"] = seed_id

        await _sync_until_record_present(
            pipeshub_client,
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

    @pytest.mark.order(11)
    async def test_tc_ff_006_folder_exits_scope_cascade_deletes(
        self,
        drive_individual_ff_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        oos_folder_id = drive_individual_ff_connector["oos_folder_id"]

        movable_id = await create_drive_folder(
            drive_individual_datasource, "movable", parent_id=seed_id
        )
        inside_id = await create_drive_text_file(
            drive_individual_datasource,
            "inside.txt",
            parent_id=movable_id,
            content="ff-006/007 movable folder\n",
        )
        drive_individual_ff_connector["movable_folder_id"] = movable_id
        drive_individual_ff_connector["movable_inside_file_id"] = inside_id

        await _sync_until_record_present(
            pipeshub_client,
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
        drive_individual_ff_connector["movable_parent_id"] = oos_folder_id

        await _sync_until_records_absent(
            pipeshub_client,
            graph_provider,
            connector_id,
            [movable_id, inside_id],
            description=(
                f"movable ({movable_id}) + inside.txt ({inside_id}) "
                "deleted after folder scope exit"
            ),
        )

    @pytest.mark.order(12)
    async def test_tc_ff_007_folder_enters_scope_pulls_descendants(
        self,
        drive_individual_ff_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        oos_folder_id = drive_individual_ff_connector["oos_folder_id"]
        movable_id = drive_individual_ff_connector["movable_folder_id"]
        inside_id = drive_individual_ff_connector["movable_inside_file_id"]

        await move_drive_item(
            drive_individual_datasource,
            movable_id,
            new_parent_id=seed_id,
            old_parent_id=oos_folder_id,
        )
        drive_individual_ff_connector["movable_parent_id"] = seed_id

        await _sync_until_records_present(
            pipeshub_client,
            graph_provider,
            connector_id,
            [movable_id, inside_id],
            description=(
                f"movable ({movable_id}) + inside.txt ({inside_id}) "
                "re-synced after scope enter"
            ),
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

    @pytest.mark.order(13)
    async def test_tc_ff_008_oos_create_stays_out(
        self,
        drive_individual_ff_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        oos_folder_id = drive_individual_ff_connector["oos_folder_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        in_scope_id = (
            drive_individual_ff_connector.get("new_file_id")
            or drive_individual_ff_connector.get("leave_file_id")
        )
        assert in_scope_id, "Expected an in-scope file id from FF-002 or FF-005"

        ignored_id = await create_drive_text_file(
            drive_individual_datasource,
            "ignored.txt",
            parent_id=oos_folder_id,
            content="ff-008 out of scope\n",
        )
        drive_individual_ff_connector["oos_ignored_file_id"] = ignored_id

        await _settle_drive_changes()
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

    @pytest.mark.order(14)
    async def test_tc_ff_009_rename_in_scope_updates_name(
        self,
        drive_individual_ff_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        leave_file_id = drive_individual_ff_connector["leave_file_id"]
        new_name = "leave-renamed.txt"

        await rename_drive_item(drive_individual_datasource, leave_file_id, new_name)
        drive_individual_ff_connector["leave_file_name"] = new_name

        async def _renamed() -> bool:
            record = await graph_provider.get_record_by_external_id(
                connector_id, leave_file_id
            )
            return record is not None and record.record_name == new_name

        await _sync_until(
            pipeshub_client,
            graph_provider,
            connector_id,
            check=_renamed,
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

    @pytest.mark.order(15)
    async def test_tc_ff_010_placeholder_root_ancestor_present(
        self,
        drive_individual_ff_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        root_id = drive_individual_ff_connector["root_folder_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]

        await connector_assertions.assert_record_exists(
            connector_id,
            root_id,
            RecordAssertion(
                external_record_id=root_id,
                record_name=drive_individual_ff_connector["root_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            seed_id,
            RecordAssertion(
                external_record_id=seed_id,
                record_name=drive_individual_ff_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=root_id,
            ),
        )

    @pytest.mark.order(16)
    async def test_tc_ff_011_in_scope_parent_change_updates_parent(
        self,
        drive_individual_ff_connector: dict[str, Any],
        drive_individual_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        nested_id = drive_individual_ff_connector["nested_folder_id"]
        new_file_id = drive_individual_ff_connector["new_file_id"]

        await move_drive_item(
            drive_individual_datasource,
            new_file_id,
            new_parent_id=nested_id,
            old_parent_id=seed_id,
        )
        drive_individual_ff_connector["new_file_parent_id"] = nested_id

        async def _parent_updated() -> bool:
            record = await graph_provider.get_record_by_external_id(
                connector_id, new_file_id
            )
            return (
                record is not None
                and record.parent_external_record_id == nested_id
            )

        await _sync_until(
            pipeshub_client,
            graph_provider,
            connector_id,
            check=_parent_updated,
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

    @pytest.mark.order(17)
    async def test_tc_ff_012_multi_seed_syncs_oos_sibling(
        self,
        drive_individual_ff_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        nested_id = drive_individual_ff_connector["nested_folder_id"]
        child_id = drive_individual_ff_connector["child_file_id"]
        oos_folder_id = drive_individual_ff_connector["oos_folder_id"]
        oos_file_id = drive_individual_ff_connector["oos_file_id"]

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
        drive_individual_ff_connector["scoped_after_multi_seed"] = scoped_after

        await connector_assertions.assert_record_exists(
            connector_id,
            oos_folder_id,
            RecordAssertion(
                external_record_id=oos_folder_id,
                record_name=drive_individual_ff_connector["oos_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            oos_file_id,
            RecordAssertion(
                external_record_id=oos_file_id,
                record_name=drive_individual_ff_connector["oos_file_name"],
                parent_external_record_id=oos_folder_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_individual_ff_connector["nested_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            child_id,
            RecordAssertion(
                external_record_id=child_id,
                record_name=drive_individual_ff_connector["child_file_name"],
                parent_external_record_id=nested_id,
            ),
        )

    @pytest.mark.order(18)
    async def test_tc_ff_013_narrow_folder_ids_to_nested(
        self,
        drive_individual_ff_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        nested_id = drive_individual_ff_connector["nested_folder_id"]
        child_id = drive_individual_ff_connector["child_file_id"]
        new_file_id = drive_individual_ff_connector["new_file_id"]

        scoped_before = int(
            drive_individual_ff_connector.get("scoped_after_multi_seed")
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
        drive_individual_ff_connector["scoped_after_narrow"] = scoped_after

        await connector_assertions.assert_record_exists(
            connector_id,
            nested_id,
            RecordAssertion(
                external_record_id=nested_id,
                record_name=drive_individual_ff_connector["nested_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            child_id,
            RecordAssertion(
                external_record_id=child_id,
                record_name=drive_individual_ff_connector["child_file_name"],
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
                record_name=drive_individual_ff_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )

    @pytest.mark.order(19)
    async def test_tc_ff_014_widen_folder_ids_to_seed(
        self,
        drive_individual_ff_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        seed_id = drive_individual_ff_connector["seed_folder_id"]
        leave_file_id = drive_individual_ff_connector["leave_file_id"]
        leave_file_name = drive_individual_ff_connector["leave_file_name"]

        scoped_narrow = int(
            drive_individual_ff_connector.get("scoped_after_narrow")
            or await graph_provider.count_records(connector_id, scoped=True)
        )
        scoped_multi = int(
            drive_individual_ff_connector.get("scoped_after_multi_seed") or 10**9
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
        drive_individual_ff_connector["scoped_after_widen"] = scoped_after

        await connector_assertions.assert_record_exists(
            connector_id,
            leave_file_id,
            RecordAssertion(
                external_record_id=leave_file_id,
                record_name=leave_file_name,
                parent_external_record_id=seed_id,
            ),
        )

    @pytest.mark.order(20)
    async def test_tc_ff_015_clear_folder_ids_syncs_fixture_oos(
        self,
        drive_individual_ff_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        connector_id = drive_individual_ff_connector["connector_id"]
        oos_folder_id = drive_individual_ff_connector["oos_folder_id"]
        oos_file_id = drive_individual_ff_connector["oos_file_id"]

        scoped_multi = int(
            drive_individual_ff_connector.get("scoped_after_multi_seed")
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
                record_name=drive_individual_ff_connector["oos_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            oos_file_id,
            RecordAssertion(
                external_record_id=oos_file_id,
                record_name=drive_individual_ff_connector["oos_file_name"],
                parent_external_record_id=oos_folder_id,
            ),
        )


class TestDriveIndividualExtensionFilter:
    """file_extensions IN then NOT_IN on one dedicated connector (edge-only full sync)."""

    @pytest.mark.order(21)
    async def test_tc_ext_001_in_allows_docs_sheets_excludes_slides(
        self,
        drive_individual_ext_connector: dict[str, Any],
        connector_assertions: ConnectorAssertions,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        connector_id = drive_individual_ext_connector["connector_id"]
        seed_id = drive_individual_ext_connector["seed_folder_id"]
        doc_id = drive_individual_ext_connector["doc_file_id"]
        sheet_id = drive_individual_ext_connector["sheet_file_id"]
        slide_id = drive_individual_ext_connector["slide_file_id"]
        txt_id = drive_individual_ext_connector["txt_file_id"]

        await connector_assertions.assert_record_exists(
            connector_id,
            seed_id,
            RecordAssertion(
                external_record_id=seed_id,
                record_name=drive_individual_ext_connector["seed_folder_name"],
                mime_type=MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            doc_id,
            RecordAssertion(
                external_record_id=doc_id,
                record_name=drive_individual_ext_connector["doc_file_name"],
                mime_type=MimeTypes.GOOGLE_DOCS.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            sheet_id,
            RecordAssertion(
                external_record_id=sheet_id,
                record_name=drive_individual_ext_connector["sheet_file_name"],
                mime_type=MimeTypes.GOOGLE_SHEETS.value,
                parent_external_record_id=seed_id,
            ),
        )
        await connector_assertions.assert_record_exists(
            connector_id,
            txt_id,
            RecordAssertion(
                external_record_id=txt_id,
                record_name=drive_individual_ext_connector["txt_file_name"],
                parent_external_record_id=seed_id,
            ),
        )

        slide = await graph_provider.get_record_by_external_id(connector_id, slide_id)
        assert slide is None, (
            f"Google Slides {slide_id} must not sync under file_extensions IN "
            f"docs/sheets/txt"
        )

    @pytest.mark.order(22)
    async def test_tc_ext_002_not_in_docs_drops_doc_includes_slide(
        self,
        drive_individual_ext_connector: dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
        connector_assertions: ConnectorAssertions,
    ) -> None:
        """Switch IN → NOT_IN on the same connector; assert via live BELONGS_TO.

        Full sync after filter change deletes edges only — Doc may keep its Record
        node while losing BELONGS_TO; Slide (never synced under IN) gains one.
        """
        connector_id = drive_individual_ext_connector["connector_id"]
        seed_id = drive_individual_ext_connector["seed_folder_id"]
        doc_id = drive_individual_ext_connector["doc_file_id"]
        sheet_id = drive_individual_ext_connector["sheet_file_id"]
        slide_id = drive_individual_ext_connector["slide_file_id"]

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
                record_name=drive_individual_ext_connector["slide_file_name"],
                mime_type=MimeTypes.GOOGLE_SLIDES.value,
                parent_external_record_id=seed_id,
            ),
        )


_BLOCKS_CASES = [
    pytest.param(
        "newsletter_doc",
        "TC-DRIVE-IND-BLOCKS-001",
        marks=pytest.mark.order(23),
        id="TC-DRIVE-IND-BLOCKS-001",
    ),
    pytest.param(
        "science_fair_slides",
        "TC-DRIVE-IND-BLOCKS-002",
        marks=pytest.mark.order(24),
        id="TC-DRIVE-IND-BLOCKS-002",
    ),
    pytest.param(
        "gantt_chart_sheets",
        "TC-DRIVE-IND-BLOCKS-003",
        marks=pytest.mark.order(25),
        id="TC-DRIVE-IND-BLOCKS-003",
    ),
    pytest.param(
        "contributing_md",
        "TC-DRIVE-IND-BLOCKS-004",
        marks=pytest.mark.order(26),
        id="TC-DRIVE-IND-BLOCKS-004",
    ),
]

_INDEXING_CASES = [
    pytest.param(
        kind,
        f"TC-DRIVE-IND-IDX-{idx:03d}",
        marks=pytest.mark.order(order),
        id=f"TC-DRIVE-IND-IDX-{idx:03d}",
    )
    for idx, (kind, order) in enumerate(
        (
            ("newsletter_doc", 28),
            ("science_fair_slides", 29),
            ("gantt_chart_sheets", 30),
            ("owasp_pdf", 31),
            ("contributing_md", 32),
        ),
        start=1,
    )
]


def _kind_local_name(kind: str) -> str:
    for spec in DRIVE_BLOCKS_SAMPLE_SPECS:
        if spec["kind"] == kind:
            return spec["local_name"]
    return kind


class TestDriveIndividualBlocks:
    """Parser snapshots + live indexing over the same five synced samples.

    Both halves share one class-scoped connector; splitting them into separate
    classes would tear that connector down and re-upload the samples.
    """

    @pytest.mark.parametrize("kind,tc_id", _BLOCKS_CASES)
    async def test_tc_drive_ind_blocks_streamed_expected(
        self,
        kind: str,
        tc_id: str,
        drive_individual_blocks_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        drive_individual_blocks_stream_client: PipeshubClient,
    ) -> None:
        """Stream one synced sample, parse via production Processor, compare snapshot.

        Validates the personal Drive export/download → format-specific Processor
        path → typed blocks. Snapshots are the Workspace ``fixtures/`` files: same
        samples, same Processor, so a mismatch means the two connectors diverged on
        export rather than a fixture needing a fork. Streams as the OAuth account
        (the record ACL owner), not the org admin. Regenerate with
        ``GOOGLE_DRIVE_BLOCKS_BOOTSTRAP=1``.
        """
        connector_id = drive_individual_blocks_connector["connector_id"]
        by_kind: dict[str, dict[str, str]] = drive_individual_blocks_connector[
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

        resp = drive_individual_blocks_stream_client.stream_record(record.id)
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

    @pytest.mark.parametrize("kind,tc_id", _INDEXING_CASES)
    async def test_tc_drive_ind_idx_sample_indexing_completed(
        self,
        kind: str,
        tc_id: str,
        drive_individual_blocks_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """Assert the sample cleared the live indexing pipeline.

        The snapshot test above parses in-process with ``IndexingPipeline``
        patched out; this proves the record was published, consumed and embedded.
        A contentless parse lands on ``EMPTY`` rather than ``COMPLETED``, so this
        catches "parser silently returns nothing" for every sample mime without a
        snapshot to maintain.
        """
        connector_id = drive_individual_blocks_connector["connector_id"]
        by_kind: dict[str, dict[str, str]] = drive_individual_blocks_connector[
            "blocks_by_kind"
        ]
        sample = by_kind.get(kind)
        assert sample is not None, f"blocks fixture missing kind={kind!r}"
        external_id = sample["id"]

        rec = await wait_until_record_indexing_completed(
            graph_provider,
            connector_id,
            external_id,
            timeout=_INDEXING_WAIT_SEC,
            description=f"{tc_id} {kind} ({sample['name']})",
            pipeshub_client=pipeshub_client,
        )
        assert rec.indexing_status == ProgressStatus.COMPLETED.value
        assert rec.virtual_record_id, (
            f"{tc_id}: {kind} has no virtual_record_id after indexing COMPLETED — "
            f"nothing was embedded"
        )
        logger.info("%s passed (%s)", tc_id, kind)
