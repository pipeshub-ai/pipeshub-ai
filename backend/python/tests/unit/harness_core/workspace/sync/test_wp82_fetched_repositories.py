"""WP82-SYNC-1..5: a repository ``ph git clone`` checked out stays out of the workspace store, and no collect proposes a
revision past the store's file cap.

In a long research run, the two approved clones put 20,977 files into the child's
workspace folder; every save from then on sent all of them, Node refused each commit (its file cap is 20,000) and
the turn's last save crashed the attempt. These tests fail on the code before WP-82.
"""

from __future__ import annotations

import pytest

from harness_core.testing.contracts.workspace_sync import acquire, commit, tree
from harness_core.workspace import MAX_WORKSPACE_FILES
from harness_core.workspace.sync import TAR, VOLUME, SyncLimits, WorkspaceSaver
from harness_core.workspace.sync.rules import fetched_checkout_of, marks_fetched
from harness_core.workspace.sync.types import (
    ENGINE_CONFIG,
    REFERENCE_COPY,
    WORKSPACE_FULL,
)
from tests.unit.harness_core.workspace.sync.support import FakeSyncHarness

# What ``ph git clone`` leaves in a checkout's git config (``checkout_clone``: ``git config pipeshub.fetched true``).
PH_CONFIG = (
    b"[core]\n\trepositoryformatversion = 0\n\tbare = false\n"
    b'[remote "origin"]\n\turl = https://github.com/onyx-dot-app/onyx\n'
    b"[pipeshub]\n\tfetched = true\n"
)
OWN_CONFIG = b'[core]\n\tbare = false\n[remote "origin"]\n\turl = https://github.com/acme/app\n'


@pytest.mark.parametrize(
    ("config", "fetched"),
    [
        (PH_CONFIG, True),
        (b"[pipeshub]\nfetched=TRUE\n", True),
        (b"[PipesHub]\n\tFetched = true ; ph\n", True),
        (OWN_CONFIG, False),
        (b"[pipeshub]\n\tfetched = false\n", False),
        (b"[core]\n\tfetched = true\n", False),
        (b"# [pipeshub]\n# fetched = true\n", False),
        (b"\xff\xfe[pipeshub]", False),
    ],
)
def test_wp82_sync_1_the_fetched_mark(config: bytes, fetched: bool) -> None:
    assert marks_fetched(config) is fetched


@pytest.mark.parametrize(
    ("path", "folder"),
    [
        ("onyx/.git/config", "onyx"),
        ("repos/onyx/.git/config", "repos/onyx"),
        (".git/config", None),
        ("onyx/.git/config.lock", None),
        (".codex/x/.git/config", None),
    ],
)
def test_wp82_sync_1_the_checkout_a_config_belongs_to(path: str, folder: str | None) -> None:
    assert fetched_checkout_of(path) == folder


async def test_wp82_sync_2_a_fetched_checkout_is_never_collected_or_uploaded() -> None:
    harness = FakeSyncHarness(TAR)
    manifest = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    root = harness.root
    for path, data in {
        "repos/onyx/.git/config": PH_CONFIG,
        "repos/onyx/.git/HEAD": b"ref: refs/heads/main\n",
        "repos/onyx/README.md": b"# Onyx\n",
        "repos/onyx/backend/app.py": b"print('onyx')\n",
        "repos/onyx/.claude/settings.json": b"{}",
        "own/.git/config": OWN_CONFIG,
        "own/main.py": b"mine\n",
        "out/report.md": b"# Report\n",
    }.items():
        await harness.hands.write(sandbox, f"{root}/{path}", data)
    puts = harness.objects.puts
    collected = await harness.sync.collect(sandbox, state)
    assert set(collected.changes.upserts) == {"own/.git/config", "own/main.py", "out/report.md"}
    assert {p: why for p, why in collected.skipped.items() if p.startswith("repos/")} == {
        "repos/onyx/.git/config": REFERENCE_COPY,
        "repos/onyx/.git/HEAD": REFERENCE_COPY,
        "repos/onyx/README.md": REFERENCE_COPY,
        "repos/onyx/backend/app.py": REFERENCE_COPY,
        "repos/onyx/.claude/settings.json": ENGINE_CONFIG,
    }
    assert harness.objects.puts - puts == 3
    assert not collected.changes.deletes


async def test_wp82_sync_3_a_refresh_mid_turn_leaves_the_checkout_where_the_engine_works() -> None:
    harness = FakeSyncHarness(TAR)
    manifest = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.write(sandbox, f"{harness.root}/onyx/.git/config", PH_CONFIG)
    await harness.hands.write(sandbox, f"{harness.root}/onyx/README.md", b"# Onyx\n")
    await harness.hands.write(sandbox, f"{harness.root}/notes.md", b"notes")
    # Another writer moves the head (the person uploads a file): the save re-materializes the sandbox.
    _ = await commit(harness, {"upload.csv": b"a,b"})
    saver = WorkspaceSaver(sync=harness.sync, store=harness.store, scope=harness.scope)
    saved = await saver.save(sandbox, state, reason="before_effect")
    assert saved.manifest is not None
    assert set(saved.manifest.files) == {"task.md", "upload.csv", "notes.md"}
    files = await tree(sandbox, harness.root)
    assert files["onyx/README.md"][1] == b"# Onyx\n"
    assert files["upload.csv"][1] == b"a,b"


async def test_wp82_sync_4_a_persistent_folder_keeps_the_checkout_but_never_its_engine_config() -> None:
    harness = FakeSyncHarness(VOLUME)
    manifest = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    _ = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.write(sandbox, f"{harness.root}/onyx/.git/config", PH_CONFIG)
    await harness.hands.write(sandbox, f"{harness.root}/onyx/README.md", b"# Onyx\n")
    await harness.hands.write(sandbox, f"{harness.root}/onyx/.codex/config.toml", b"x")
    await harness.hands.write(sandbox, f"{harness.root}/stray.txt", b"not saved")
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    files = await tree(sandbox, harness.root)
    assert set(files) == {"task.md", "onyx/.git/config", "onyx/README.md"}
    assert (await harness.sync.collect(sandbox, state)).changes.is_empty


async def test_wp82_sync_5_no_collect_proposes_more_files_than_the_store_holds() -> None:
    assert MAX_WORKSPACE_FILES == 20_000
    harness = FakeSyncHarness(TAR, limits=SyncLimits(max_workspace_files=6))
    manifest = await commit(harness, {"task.md": b"task", "old/a.txt": b"a", "gone.txt": b"g"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.remove(sandbox, f"{harness.root}/gone.txt")
    await harness.hands.write(sandbox, f"{harness.root}/old/a.txt", b"changed")
    for index in range(5):
        await harness.hands.write(sandbox, f"{harness.root}/node_modules/pkg/lib/f{index}.js", b"x")
    await harness.hands.write(sandbox, f"{harness.root}/out/report.md", b"# Report\n")
    await harness.hands.write(sandbox, f"{harness.root}/notes.md", b"notes")
    collected = await harness.sync.collect(sandbox, state)
    after = len(manifest.files) - len(collected.changes.deletes) + len(
        set(collected.changes.upserts) - set(manifest.files)
    )
    assert after == 6
    assert {"out/report.md", "notes.md", "old/a.txt"} <= set(collected.changes.upserts)
    assert collected.changes.deletes == frozenset({"gone.txt"})
    full = sorted(p for p, why in collected.skipped.items() if why == WORKSPACE_FULL)
    assert full == [f"node_modules/pkg/lib/f{index}.js" for index in range(2, 5)]
