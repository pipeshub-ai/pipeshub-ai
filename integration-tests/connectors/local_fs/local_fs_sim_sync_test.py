# pyright: ignore-file

"""
Local FS on a headless desktop – syncing
========================================

The folder is the oracle (see local_fs_sim_world.py). The finding each test proves is in brackets; those
fail on upstream main and pass with the audit's fixes.

  TC-LFSSIM-SYNC-001  First enable from the desktop claims the device and syncs the whole tree; only the
                      owner opens the records
  TC-LFSSIM-SYNC-002  Add, edit, delete between syncs land exactly
  TC-LFSSIM-SYNC-003  An unchanged sync pulls one empty page
  TC-LFSSIM-SYNC-004  An empty file is indexed and streams as zero bytes [F-EMPTY-FILE]
  TC-LFSSIM-SYNC-005  Changes made while the app was closed arrive at the next launch
  TC-LFSSIM-SYNC-006  Changes saved just before the app quits are not lost [F-QUIT-LOSES-CHANGES]
  TC-LFSSIM-SYNC-007  Moving a folder with 3,000 files syncs; no page is too big for the socket [F-BIG-PAGE]
  TC-LFSSIM-SYNC-008  Full sync after a lost watcher snapshot removes what was deleted [F-LOST-STATE]
"""

import asyncio
import logging

import pytest
from connectors.local_fs.local_fs_sim_world import TREE, LocalFsWorld, succeeded

logger = logging.getLogger("local-fs-sim-sync-test")


async def _ready(world: LocalFsWorld, files=TREE) -> None:
    for rel, content in files.items():
        world.write(rel, content)
    await world.create_connector()
    assert succeeded(await world.enable()), "the first sync after enable did not succeed"


@pytest.mark.integration
@pytest.mark.local_fs_sim
@pytest.mark.asyncio(loop_scope="session")
class TestLocalFsSimSync:
    async def test_first_sync_matches_the_folder(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-SYNC-001"""
        await _ready(local_fs_world)
        assert local_fs_world.connector().get("ownerDeviceId") == local_fs_world.device_id
        await local_fs_world.assert_matches("first sync")

    async def test_add_edit_delete(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-SYNC-002"""
        w = local_fs_world
        await _ready(w)
        w.write("docs/new.txt", "brand new")
        w.write("docs/plan.md", "# plan v2")
        w.remove("photos/cat.png")
        assert succeeded(await w.sync())
        await w.assert_matches("after add, edit and delete")

    async def test_unchanged_sync_is_one_empty_pull(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-SYNC-003"""
        w = local_fs_world
        await _ready(w)
        w.desktop.ctl("reset-log")
        assert succeeded(await w.sync())
        pulls = w.desktop.ctl("log")["pulls"]
        assert [p["events"] for p in pulls] == [0], pulls

    async def test_empty_file_is_indexed(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-SYNC-004 [F-EMPTY-FILE]"""
        w = local_fs_world
        await _ready(w, {"notes/empty.txt": "", "notes/full.txt": "x"})
        await w.assert_matches("first sync")
        record = next(r for r in await w.records() if r.get("recordName") == "empty.txt")
        response = w.stream(record["id"])
        assert response.status_code == 200, f"an empty file did not stream: {response.status_code} {response.text[:200]}"
        assert response.content == b""

    async def test_changes_while_the_app_was_closed(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-SYNC-005"""
        w = local_fs_world
        await _ready(w)
        w.desktop.quit()
        w.write("offline-new.txt", "new")
        w.write("docs/plan.md", "# edited offline")
        w.remove("photos/cat.png")
        w.move("docs/specs/api.txt", "docs/api-renamed.txt")
        w.launch_desktop()
        assert succeeded(await w.sync())
        await w.assert_matches("after relaunch")

    async def test_changes_just_before_quit(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-SYNC-006 [F-QUIT-LOSES-CHANGES]: chokidar holds a write back 1.5 s until the file is
        stable, so a quit 0.3 s after a save is the realistic case."""
        w = local_fs_world
        await _ready(w)
        w.settle()
        w.write("docs/plan.md", "# saved right before quit")
        w.write("docs/new-before-quit.txt", "new")
        w.move("reports/q1.pdf", "reports/q1-renamed.pdf")
        await asyncio.sleep(0.3)
        w.desktop.quit()
        w.launch_desktop()
        assert succeeded(await w.sync())
        await w.assert_matches("after quit and relaunch")

    @pytest.mark.slow
    async def test_big_folder_move(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-SYNC-007 [F-BIG-PAGE]: one journal batch of 3,000 moves is over 1 MB as JSON."""
        w = local_fs_world
        name = "a-folder-with-a-rather-long-descriptive-name-for-quarterly-planning-documents"
        files = {f"{name}/sub-{i // 500:02}/document-{i:05}-with-a-long-descriptive-file-name.txt": f"doc {i}"
                 for i in range(3000)}
        await _ready(w, files)
        w.move(name, "moved-" + name)
        assert succeeded(await w.sync()), "the sync after a big folder move did not succeed"
        await w.assert_matches("after the big move")
        assert succeeded(await w.sync())

    async def test_lost_snapshot_removes_deletions(self, local_fs_world: LocalFsWorld) -> None:
        """TC-LFSSIM-SYNC-008 [F-LOST-STATE]"""
        w = local_fs_world
        await _ready(w)
        w.desktop.quit()
        for p in w.work.glob(f"userdata-{w.device_id}/local-sync-journal/watcher_state.*.json"):
            p.write_text("{not json")
        w.remove("reports/q2.pdf")
        w.launch_desktop()
        assert succeeded(await w.sync())
        await w.assert_matches("after relaunch with a lost snapshot")
