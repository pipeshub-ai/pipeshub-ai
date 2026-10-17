# pyright: ignore-file

"""Google Drive Workspace — folder-filter edge cases against a real tenant.

GD_Testing folder-filter cases 17–22, each on its own connector
(``drive_workspace_connector_factory``) because the subject is the connector's own
filter history:

  TC-DRV-FFE-001  unfiltered → folder_ids: out-of-scope items leave, the seed subtree
                  and its ancestors stay
  TC-DRV-FFE-002  full resync under folder_ids keeps the seed's ancestors (the case
                  "full sync fixes for folder filter" targets)
  TC-DRV-FFE-003  a seed trashed, then deleted for good: its subtree leaves, the other
                  seed keeps syncing, nothing crashes
  TC-DRV-FFE-004  the test user loses access to the seed: with no synced user left, it leaves
  TC-DRV-FFE-005  the seed is a shortcut to a folder: the target's subtree syncs
  TC-DRV-FFE-006  the seed's parent appears once the user can open it (Drive hides it
                  until then)
  TC-DRV-FFE-007  that ancestor leaves once the seed below it is gone (strict xfail: B9)
"""

from __future__ import annotations

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

from app.sources.external.google.drive.drive import (  # type: ignore[import-not-found]  # noqa: E402
    GoogleDriveDataSource,
)
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # noqa: E402
    ENV_ADMIN_EMAIL,
    ENV_SA_JSON,
    ENV_SECOND_USER,
    build_drive_datasource,
    connector_filters,
    create_drive_folder,
    create_drive_shortcut,
    create_drive_text_file,
    create_folder_filter_fixtures,
    delete_drive_folder,
    delete_drive_item_permanently,
    ensure_pipeshub_user_inactive,
    record_absent,
    record_present,
    share_drive_item_with_user,
    sync_until,
    trash_drive_item,
    unshare_drive_item_from_user,
)
from helper.clients.users_client import UsersClient  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    apply_filter_full_sync,
    async_poll_until,
    wait_for_sync_completion,
)
from pipeshub_client import PipeshubClient  # noqa: E402

logger = logging.getLogger("drive-workspace-ff-edges-test")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_workspace,
    pytest.mark.asyncio(loop_scope="session"),
]

_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_SYNC_TIMEOUT", "300"))
_USER_GRAPH_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_WORKSPACE_USER_GRAPH_TIMEOUT", "120"))

ConnectorFactory = Callable[..., Awaitable[str]]


def _folder_ids(*ids: str) -> dict[str, Any]:
    return {"folder_ids": {"operator": "in", "type": "list", "value": list(ids)}}


def _checks(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    *,
    present: list[str] = (),
    absent: list[str] = (),
) -> Callable[[], Awaitable[bool]]:
    probes = [record_present(graph_provider, connector_id, e) for e in present] + [
        record_absent(graph_provider, connector_id, e) for e in absent
    ]

    async def _all() -> bool:
        for probe in probes:
            if not await probe():
                return False
        return True

    return _all


async def _missing_or_extra(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    *,
    present: dict[str, str],
    absent: dict[str, str],
) -> str:
    """Name what is wrong when a wait times out, so the failure explains itself."""
    wrong: list[str] = []
    for label, eid in present.items():
        if await graph_provider.get_record_by_external_id(connector_id, eid) is None:
            wrong.append(f"{label} missing")
    for label, eid in absent.items():
        if await graph_provider.get_record_by_external_id(connector_id, eid) is not None:
            wrong.append(f"{label} still present")
    return ", ".join(wrong) or "(state now correct; it was late)"


async def _assert_state(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    *,
    present: dict[str, str],
    absent: dict[str, str],
    description: str,
) -> None:
    try:
        await sync_until(
            pipeshub_client, graph_provider, connector_id,
            _checks(graph_provider, connector_id, present=list(present.values()), absent=list(absent.values())),
            description=description,
        )
    except TimeoutError:
        detail = await _missing_or_extra(graph_provider, connector_id, present=present, absent=absent)
        raise AssertionError(f"{description}: {detail}") from None


async def _second_user_drive(users_client: UsersClient, graph_provider: GraphProviderProtocol) -> tuple[str, GoogleDriveDataSource]:
    """User B's Drive, with B inactive in PipesHub so only the test user's access counts.

    The Workspace connector syncs every active user; if B were active, B's own view of
    a folder B owns would keep it in the graph whatever the test user can see.
    """
    second = os.getenv(ENV_SECOND_USER, "").strip()
    if not second:
        pytest.skip(f"{ENV_SECOND_USER} not set")
    ensure_pipeshub_user_inactive(users_client, second)

    async def _inactive() -> bool | None:
        user = await graph_provider.graph_find_user_by_email(second)
        return True if user is None or user.get("isActive") is False else None

    await async_poll_until(
        _inactive, timeout=_USER_GRAPH_TIMEOUT_SEC, interval=2,
        description=f"{second} inactive in graph",
    )
    drive = await build_drive_datasource(
        os.environ[ENV_SA_JSON].strip(), os.environ[ENV_ADMIN_EMAIL].strip(), second
    )
    return second, drive


class TestDriveWorkspaceFolderFilterEdges:

    async def test_tc_drv_ffe_001_unfiltered_to_folder_filter(
        self,
        drive_workspace_connector_factory: ConnectorFactory,
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-FFE-001 (GD FF17): no filter → folder_ids=[seed] drops OOS, keeps seed tree and ancestors."""
        tree = await create_folder_filter_fixtures(drive_workspace_datasource)
        try:
            ids = {k: tree[k] for k in ("root_folder_id", "seed_folder_id", "nested_folder_id", "child_file_id", "oos_folder_id", "oos_file_id")}
            connector_id = await drive_workspace_connector_factory(
                name_prefix="drive-ws-ffe-unfiltered", listed_ids=list(ids.values()),
            )
            await _assert_state(
                pipeshub_client, graph_provider, connector_id,
                present={k: v for k, v in ids.items()}, absent={},
                description="unfiltered sync has the whole fixture tree",
            )

            await apply_filter_full_sync(
                pipeshub_client, graph_provider, connector_id,
                connector_filters(sync_values=_folder_ids(tree["seed_folder_id"])),
                timeout=_SYNC_TIMEOUT_SEC,
            )
            await _assert_state(
                pipeshub_client, graph_provider, connector_id,
                present={
                    "root (ancestor)": tree["root_folder_id"],
                    "seed": tree["seed_folder_id"],
                    "nested": tree["nested_folder_id"],
                    "child.txt": tree["child_file_id"],
                },
                absent={"out_of_scope": tree["oos_folder_id"], "sibling.txt": tree["oos_file_id"]},
                description="after narrowing an unfiltered connector to folder_ids=[seed]",
            )
        finally:
            await delete_drive_folder(drive_workspace_datasource, tree["root_folder_id"])

    async def test_tc_drv_ffe_002_full_resync_keeps_seed_ancestors(
        self,
        drive_workspace_connector_factory: ConnectorFactory,
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-FFE-002 (GD FF18): full resyncs under folder_ids keep the seed's ancestors."""
        tree = await create_folder_filter_fixtures(drive_workspace_datasource)
        try:
            connector_id = await drive_workspace_connector_factory(
                name_prefix="drive-ws-ffe-fullsync",
                sync_values=_folder_ids(tree["seed_folder_id"]),
                listed_ids=[tree["root_folder_id"], tree["seed_folder_id"], tree["nested_folder_id"], tree["child_file_id"]],
            )
            expected_present = {
                "root (ancestor)": tree["root_folder_id"],
                "seed": tree["seed_folder_id"],
                "nested": tree["nested_folder_id"],
                "child.txt": tree["child_file_id"],
            }
            expected_absent = {"out_of_scope": tree["oos_folder_id"], "sibling.txt": tree["oos_file_id"]}
            await _assert_state(
                pipeshub_client, graph_provider, connector_id,
                present=expected_present, absent=expected_absent,
                description="first sync under folder_ids=[seed]",
            )
            for attempt in (1, 2):
                pipeshub_client.resync_connector(connector_id, full_sync=True)
                await wait_for_sync_completion(
                    pipeshub_client, graph_provider, connector_id, timeout=_SYNC_TIMEOUT_SEC,
                )
                detail = await _missing_or_extra(
                    graph_provider, connector_id, present=expected_present, absent=expected_absent,
                )
                assert detail.startswith("(state now correct"), (
                    f"after full resync #{attempt} under folder_ids=[seed]: {detail}"
                )
        finally:
            await delete_drive_folder(drive_workspace_datasource, tree["root_folder_id"])

    async def test_tc_drv_ffe_003_trashed_then_deleted_seed(
        self,
        drive_workspace_connector_factory: ConnectorFactory,
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-FFE-003 (GD FF19): a seed trashed then deleted leaves; the other seed keeps syncing."""
        tree = await create_folder_filter_fixtures(drive_workspace_datasource)
        try:
            seed, other = tree["seed_folder_id"], tree["oos_folder_id"]
            connector_id = await drive_workspace_connector_factory(
                name_prefix="drive-ws-ffe-trashseed",
                sync_values=_folder_ids(seed, other),
                listed_ids=[seed, tree["nested_folder_id"], tree["child_file_id"], other, tree["oos_file_id"]],
            )
            await _assert_state(
                pipeshub_client, graph_provider, connector_id,
                present={"seed": seed, "child.txt": tree["child_file_id"], "other seed": other, "sibling.txt": tree["oos_file_id"]},
                absent={}, description="both seeds synced",
            )

            await trash_drive_item(drive_workspace_datasource, seed)
            after_trash = await create_drive_text_file(
                drive_workspace_datasource, "after-trash.txt", parent_id=other, content="after trash\n"
            )
            await _assert_state(
                pipeshub_client, graph_provider, connector_id,
                present={"other seed": other, "after-trash.txt": after_trash},
                absent={"trashed seed": seed, "nested": tree["nested_folder_id"], "child.txt": tree["child_file_id"]},
                description="seed trashed: its subtree leaves, the other seed still syncs",
            )

            await delete_drive_item_permanently(drive_workspace_datasource, seed)
            after_delete = await create_drive_text_file(
                drive_workspace_datasource, "after-delete.txt", parent_id=other, content="after delete\n"
            )
            await _assert_state(
                pipeshub_client, graph_provider, connector_id,
                present={"other seed": other, "after-delete.txt": after_delete},
                absent={"deleted seed": seed},
                description="seed deleted for good: sync keeps working",
            )
        finally:
            await delete_drive_folder(drive_workspace_datasource, tree["root_folder_id"])

    async def test_tc_drv_ffe_004_access_to_seed_removed(
        self,
        drive_workspace_connector_factory: ConnectorFactory,
        users_client: UsersClient,
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-FFE-004 (GD FF20): once no synced user can open the seed, its subtree leaves the graph."""
        second, b_drive = await _second_user_drive(users_client, graph_provider)
        a_email = os.environ["GOOGLE_DRIVE_WORKSPACE_TEST_USER_EMAIL"].strip()
        seed = await create_drive_folder(b_drive, f"pipeshub-it-ffe-access-{uuid.uuid4().hex[:6]}")
        try:
            child = await create_drive_text_file(b_drive, "b-child.txt", parent_id=seed, content="b child\n")
            share_drive_item_with_user(b_drive, seed, a_email)
            connector_id = await drive_workspace_connector_factory(
                name_prefix="drive-ws-ffe-access",
                sync_values=_folder_ids(seed),
                listed_ids=[seed, child],
            )
            await _assert_state(
                pipeshub_client, graph_provider, connector_id,
                present={"B's shared seed": seed, "b-child.txt": child}, absent={},
                description="seed shared by B synced for the test user",
            )

            await unshare_drive_item_from_user(b_drive, seed, a_email)
            # B still has the folder, but B is not synced, so nobody in PipesHub can open it.
            await _assert_state(
                pipeshub_client, graph_provider, connector_id,
                present={}, absent={"seed": seed, "b-child.txt": child},
                description="after B unshares the seed, no synced user can open it",
            )
        finally:
            await delete_drive_folder(b_drive, seed)

    async def test_tc_drv_ffe_005_seed_is_a_shortcut(
        self,
        drive_workspace_connector_factory: ConnectorFactory,
        drive_workspace_datasource: GoogleDriveDataSource,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-FFE-005 (GD FF21): folder_ids=[a shortcut to a folder] syncs the target's subtree."""
        suffix = uuid.uuid4().hex[:6]
        root = await create_drive_folder(drive_workspace_datasource, f"pipeshub-it-ffe-shortcut-{suffix}")
        try:
            target = await create_drive_folder(drive_workspace_datasource, "target", parent_id=root)
            target_child = await create_drive_text_file(
                drive_workspace_datasource, "target-child.txt", parent_id=target, content="via shortcut\n"
            )
            shortcut = await create_drive_shortcut(drive_workspace_datasource, target, root, "target-shortcut")
            connector_id = await drive_workspace_connector_factory(
                name_prefix="drive-ws-ffe-shortcut",
                sync_values=_folder_ids(shortcut),
                listed_ids=[root, target, target_child, shortcut],
            )
            await _assert_state(
                pipeshub_client, graph_provider, connector_id,
                present={"target folder": target, "target-child.txt": target_child}, absent={},
                description="folder_ids=[shortcut] syncs the shortcut's target subtree",
            )
        finally:
            await delete_drive_folder(drive_workspace_datasource, root)

    async def test_tc_drv_ffe_006_ancestor_appears_once_shared(
        self,
        drive_workspace_connector_factory: ConnectorFactory,
        users_client: UsersClient,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-FFE-006 (GD FF22): the seed's parent appears once the user can open it.

        Drive omits ``parents`` while the user cannot open the parent (checked by hand
        against the tenant), so there is nothing to keep a placeholder for until the
        parent is shared; from then on the parent is the seed's ancestor.
        """
        tree = await _seed_with_late_shared_parent(
            drive_workspace_connector_factory, users_client, pipeshub_client, graph_provider
        )
        await delete_drive_folder(tree["b_drive"], tree["parent"])

    @pytest.mark.xfail(
        strict=True,
        reason="B9: an ancestor kept only for a selected folder is never removed once that folder is gone; "
        "the placeholder sweep only adds, and the Workspace out-of-scope cleanup runs only after a filter change.",
    )
    async def test_tc_drv_ffe_007_ancestor_leaves_with_the_seed(
        self,
        drive_workspace_connector_factory: ConnectorFactory,
        users_client: UsersClient,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DRV-FFE-007 (GD FF22): once the seed is deleted, the ancestor kept only for it leaves too."""
        tree = await _seed_with_late_shared_parent(
            drive_workspace_connector_factory, users_client, pipeshub_client, graph_provider
        )
        try:
            await delete_drive_item_permanently(tree["b_drive"], tree["seed"])
            await _assert_state(
                pipeshub_client, graph_provider, tree["connector_id"],
                present={},
                absent={"seed": tree["seed"], "p-child.txt": tree["child"], "parent (no in-scope descendants)": tree["parent"]},
                description="seed deleted: the ancestor kept only for it leaves too",
            )
        finally:
            await delete_drive_folder(tree["b_drive"], tree["parent"])


async def _seed_with_late_shared_parent(
    connector_factory: ConnectorFactory,
    users_client: UsersClient,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> dict[str, Any]:
    """B's P/shared-seed/p-child.txt: seed shared alone and synced, then P shared and linked.

    Returns the connector and Drive ids; the caller deletes ``parent`` from B's Drive.
    """
    second, b_drive = await _second_user_drive(users_client, graph_provider)
    a_email = os.environ["GOOGLE_DRIVE_WORKSPACE_TEST_USER_EMAIL"].strip()
    parent_name = f"pipeshub-it-ffe-parent-{uuid.uuid4().hex[:6]}"
    parent = await create_drive_folder(b_drive, parent_name)
    try:
        seed = await create_drive_folder(b_drive, "shared-seed", parent_id=parent)
        child = await create_drive_text_file(b_drive, "p-child.txt", parent_id=seed, content="p child\n")
        share_drive_item_with_user(b_drive, seed, a_email)
        connector_id = await connector_factory(
            name_prefix="drive-ws-ffe-ancestor",
            sync_values=_folder_ids(seed),
            listed_ids=[seed, child],
        )
        await _assert_state(
            pipeshub_client, graph_provider, connector_id,
            present={"seed": seed, "p-child.txt": child}, absent={"unreadable parent": parent},
            description="seed synced on its own while its parent is unreadable",
        )

        share_drive_item_with_user(b_drive, parent, a_email)

        async def _parent_linked() -> bool:
            record = await graph_provider.get_record_by_external_id(connector_id, parent)
            if record is None or record.is_placeholder or record.record_name != parent_name:
                return False
            return await graph_provider.get_record_parent_external_id(connector_id, seed) == parent

        try:
            await sync_until(
                pipeshub_client, graph_provider, connector_id, _parent_linked,
                description=f"parent {parent_name!r} synced as the seed's ancestor once shared",
            )
        except TimeoutError:
            record = await graph_provider.get_record_by_external_id(connector_id, parent)
            seed_parent = await graph_provider.get_record_parent_external_id(connector_id, seed)
            raise AssertionError(
                "after sharing the seed's parent with the test user: "
                f"parent record={'missing' if record is None else (record.record_name, record.is_placeholder)}, "
                f"seed's parent edge={seed_parent!r} (expected {parent!r})"
            ) from None
    except BaseException:
        await delete_drive_folder(b_drive, parent)
        raise
    return {"connector_id": connector_id, "b_drive": b_drive, "parent": parent, "seed": seed, "child": child}
