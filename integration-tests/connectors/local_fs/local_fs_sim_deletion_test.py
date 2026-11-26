# pyright: ignore-file

"""
Local FS on a headless desktop – deletions, moves and scope
===========================================================

Everything that leaves the folder or the sync's scope must leave PipesHub, and nothing else may: a folder
that cannot be read, or a file that is locked, is not gone. The finding each test proves is in brackets.

  TC-LFSSIM-DEL-001  Deleting a folder removes everything under it, nothing else
  TC-LFSSIM-DEL-002  Moving a folder with subfolders keeps every record, leaves nothing behind, and the
                     owner still opens every file [F-SUBFOLDERS-LEFT, F-MOVE-ACCESS]
  TC-LFSSIM-DEL-003  An unreadable subfolder survives a full sync [F-UNREADABLE-PRUNED]
  TC-LFSSIM-DEL-004  An unreadable file survives a full sync [F-UNREADABLE-PRUNED]
  TC-LFSSIM-DEL-005  A file renamed to an excluded extension leaves PipesHub [F-LEAVES-SCOPE]
  TC-LFSSIM-DEL-006  The sync folder moved away: nothing is removed, the run fails; moved back, it resumes
  TC-LFSSIM-DEL-007  Pointing the connector at another folder replaces the old folder's records
                     [F-NEW-FOLDER-STALE]
  TC-LFSSIM-DEL-008  Renaming a folder with subfolders in place: same checks as DEL-002 [F-MOVE-ACCESS]
  TC-LFSSIM-DEL-009  A folder with subfolders moved while the app is closed: the next launch's reconcile
                     keeps every record and the owner still opens every file [F-MOVE-ACCESS]
  TC-LFSSIM-DEL-010  The same folder renamed in place while the app is closed [F-MOVE-ACCESS]
"""

import logging
import os

import pytest
from connectors.local_fs.local_fs_sim_world import TREE, LocalFsWorld, succeeded
from connectors.local_fs.local_fs_source_helper import external_record_id

logger = logging.getLogger("local-fs-sim-deletion-test")


async def _ready(world: LocalFsWorld) -> None:
    for rel, content in TREE.items():
        world.write(rel, content)
    await world.create_connector()
    assert succeeded(await world.enable())


async def _assert_folder_move_keeps_records(w: LocalFsWorld, dst: str, *, app_closed: bool) -> None:
    await _ready(w)
    before = {r["recordName"]: r["id"] for r in await w.records()}
    if app_closed:
        w.desktop.quit()
    w.move("docs", dst)
    if app_closed:
        w.launch_desktop()
    assert succeeded(await w.sync())
    await w.assert_matches(f"after moving docs/ to {dst}/")
    after = {r["recordName"]: r["id"] for r in await w.records()}
    assert all(before[n] == after.get(n) for n in ("plan.md", "api.txt", "three.txt")), \
        "a moved file lost its record"


@pytest.mark.integration
@pytest.mark.local_fs_sim
@pytest.mark.asyncio(loop_scope="session")
class TestLocalFsSimDeletion:
    async def test_folder_delete(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-DEL-001"""
        w = local_fs_world
        await _ready(w)
        w.remove("docs")
        assert succeeded(await w.sync())
        await w.assert_matches("after deleting docs/")

    async def test_folder_move(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-DEL-002 [F-SUBFOLDERS-LEFT, F-MOVE-ACCESS]"""
        w = local_fs_world
        await _ready(w)
        before = {r["recordName"]: r["id"] for r in await w.records()}
        w.move("docs", "archive/docs-2024")
        assert succeeded(await w.sync())
        await w.assert_matches("after moving docs/")
        after = {r["recordName"]: r["id"] for r in await w.records()}
        assert all(before[n] == after.get(n) for n in ("plan.md", "api.txt", "three.txt")), \
            "a moved file lost its record"

    async def test_folder_rename_in_place(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-DEL-008 [F-MOVE-ACCESS]"""
        await _assert_folder_move_keeps_records(local_fs_world, "docs-renamed", app_closed=False)

    async def test_folder_move_while_app_closed(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-DEL-009 [F-MOVE-ACCESS]: the reconcile page carries every subfolder's DIR_MOVED, but after
        the files, so the files land under placeholder folders first."""
        await _assert_folder_move_keeps_records(local_fs_world, "archive/docs-2024", app_closed=True)

    async def test_folder_rename_while_app_closed(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-DEL-010 [F-MOVE-ACCESS]"""
        await _assert_folder_move_keeps_records(local_fs_world, "docs-renamed", app_closed=True)

    async def test_unreadable_folder_survives_full_sync(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-DEL-003 [F-UNREADABLE-PRUNED]"""
        w = local_fs_world
        await _ready(w)
        os.chmod(w.path("docs/specs"), 0)
        try:
            await w.sync(full=True)
            live = {r["externalRecordId"] for r in await w.records()}
            for rel in ("docs/specs/api.txt", "docs/specs/deep/one/two/three.txt"):
                assert external_record_id(w.connector_id, rel) in live, f"{rel} was removed while unreadable"
        finally:
            os.chmod(w.path("docs/specs"), 0o755)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches("readable again")

    async def test_unreadable_file_survives_full_sync(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-DEL-004 [F-UNREADABLE-PRUNED]"""
        w = local_fs_world
        await _ready(w)
        os.chmod(w.path("reports/q1.pdf"), 0)
        try:
            await w.sync(full=True)
            live = {r["externalRecordId"] for r in await w.records()}
            assert external_record_id(w.connector_id, "reports/q1.pdf") in live
        finally:
            os.chmod(w.path("reports/q1.pdf"), 0o644)

    async def test_renamed_to_an_excluded_extension(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-DEL-005 [F-LEAVES-SCOPE]"""
        w = local_fs_world
        for rel, content in TREE.items():
            w.write(rel, content)
        w.filters = {"sync": {"values": {"file_extensions": {"operator": "in", "value": ["pdf"],
                                                               "type": "multiselect"}}}}
        await w.create_connector()
        assert succeeded(await w.enable())
        w.move("reports/q1.pdf", "reports/q1.txt")
        assert succeeded(await w.sync())
        live = {r["externalRecordId"] for r in await w.records()}
        assert external_record_id(w.connector_id, "reports/q1.pdf") not in live
        assert external_record_id(w.connector_id, "reports/q1.txt") not in live

    async def test_sync_folder_moved_away(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-DEL-006"""
        w = local_fs_world
        await _ready(w)
        before = {r["id"] for r in await w.records()}
        moved = w.work / "moved-away"
        os.rename(w.folder, moved)
        try:
            assert not succeeded(await w.sync()), "a sync of a missing folder reported success"
            assert {r["id"] for r in await w.records()} == before, "records were removed"
        finally:
            os.rename(moved, w.folder)
        assert succeeded(await w.sync())
        await w.assert_matches("folder back")

    async def test_folder_path_changed(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-DEL-007 [F-NEW-FOLDER-STALE]: the renderer restarts the watcher with the new path."""
        w = local_fs_world
        await _ready(w)
        other = w.work / "other-folder"
        (other / "docs").mkdir(parents=True)
        (other / "other.txt").write_text("in the other folder")
        (other / "docs" / "plan.md").write_text("# a different plan")
        # The filters-sync route needs the connector off; the UI turns it off, saves and turns it back on.
        assert w._owner("POST", f"/api/v1/connectors/{w.connector_id}/toggle", json={"type": "sync"}).ok
        resp = w._owner("PUT", f"/api/v1/connectors/{w.connector_id}/config/filters-sync",
                        json={"sync": {"sync_root_path": str(other), "include_subfolders": True}})
        assert resp.status_code < 300, f"saving the new folder was refused: {resp.status_code} {resp.text[:200]}"
        w.folder = other
        w.start_watcher()
        assert succeeded(await w.enable())
        await w.assert_matches("after changing the folder")
