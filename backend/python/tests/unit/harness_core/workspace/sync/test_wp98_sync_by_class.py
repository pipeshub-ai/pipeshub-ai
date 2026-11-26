"""WP98-SYNC-1..7: the sync saves the person's files only, records references by address, and keeps scratch for the run.

A long research run put two clones (20,977 files) into the saved workspace; the engine's raw tool dumps (notes/web/*.json) went
into it as well and were shown as the person's files. ADR-RUN-01 D2: a reference is recorded as address and version
and materialised again later, scratch stays in the sandbox, and only durable files reach the store.
"""

from __future__ import annotations

import dataclasses

from harness_core.testing.contracts.workspace_sync import acquire, commit, tree
from harness_core.workspace.sync import TAR, VOLUME, SyncLimits, WorkspaceSaver
from harness_core.workspace.sync.storage_classes import (
    STORAGE_RECORD_PATH,
    Reference,
    decode_record,
    reference_identity,
)
from harness_core.workspace.sync.types import (
    REFERENCE_COPY,
    SCRATCH_FILE,
    STORAGE_RECORD,
    WORKSPACE_FULL,
)
from tests.unit.harness_core.workspace.sync.support import FakeSyncHarness

ADDRESS = "https://github.com/onyx-dot-app/onyx"
PH_CONFIG = (
    b'[remote "origin"]\n\turl = https://github.com/onyx-dot-app/onyx\n'
    b"[pipeshub]\n\tfetched = true\n\tcommit = 57cb7b8e0f\n"
)
ONYX = Reference("repos/onyx", ADDRESS, "57cb7b8e0f")


async def _record(harness: FakeSyncHarness, files: dict[str, object]) -> tuple[Reference, ...]:
    entry = files[STORAGE_RECORD_PATH]
    return decode_record(await harness.objects.get(harness.object_scope, entry.content))  # type: ignore[attr-defined]


async def _checkout(harness: FakeSyncHarness, sandbox: object) -> None:
    await harness.hands.write(sandbox, f"{harness.root}/repos/onyx/.git/config", PH_CONFIG)  # type: ignore[arg-type]
    await harness.hands.write(sandbox, f"{harness.root}/repos/onyx/README.md", b"# Onyx\n")  # type: ignore[arg-type]


async def test_wp98_sync_1_a_checkout_the_worker_fetched_is_recorded_by_address_and_version() -> None:
    harness = FakeSyncHarness(TAR)
    manifest = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await _checkout(harness, sandbox)
    await harness.hands.write(sandbox, f"{harness.root}/out/report.md", b"# Report\n")
    vouched = dataclasses.replace(state, vouched=frozenset({reference_identity(ADDRESS)}))
    saved = await WorkspaceSaver(sync=harness.sync, store=harness.store, scope=harness.scope).save(
        sandbox, vouched, reason="turn_end"
    )
    assert saved.state.manifest is not None
    files = saved.state.manifest.files
    assert set(files) == {"task.md", "out/report.md", STORAGE_RECORD_PATH}
    assert await _record(harness, dict(files)) == (ONYX,)
    assert saved.state.references == (ONYX,)


async def test_wp98_sync_2_an_engine_s_own_mark_keeps_its_folder_out_but_is_never_recorded() -> None:
    harness = FakeSyncHarness(TAR)
    manifest = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await _checkout(harness, sandbox)
    collected = await harness.sync.collect(sandbox, state)
    assert collected.changes.is_empty
    assert collected.skipped["repos/onyx/README.md"] == REFERENCE_COPY
    assert collected.references == ()


async def test_wp98_sync_3_scratch_stays_out_of_the_store_unless_a_deliverable_cites_it() -> None:
    harness = FakeSyncHarness(TAR)
    manifest = await commit(harness, {"task.md": b"task", "notes/tool-results/old-1.json": b"{}"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    for path, data in {
        "notes/tool-results/fetch_url-1.json": b'{"text": "raw"}',
        "notes/tool-results/fetch_url-2.json": b'{"text": "kept"}',
        "notes/tool-results/old-1.json": b'{"changed": true}',
        # WP98-FU-4: a JSON file of the person's (or a model's own dump) outside ph's folder is saved.
        "notes/plan.json": b'{"steps": 3}',
        "notes/web/onyx-docs.json": b'{"text": "raw"}',
        "notes/summary.md": b"what I found",
        "out/report.md": b"# Report\nThe raw page is notes/tool-results/fetch_url-2.json.\n",
    }.items():
        await harness.hands.write(sandbox, f"{harness.root}/{path}", data)
    collected = await harness.sync.collect(sandbox, state)
    assert set(collected.changes.upserts) == {
        "notes/tool-results/fetch_url-2.json",
        "notes/tool-results/old-1.json",
        "notes/plan.json",
        "notes/web/onyx-docs.json",
        "notes/summary.md",
        "out/report.md",
    }
    assert collected.skipped == {"notes/tool-results/fetch_url-1.json": SCRATCH_FILE}
    assert not collected.changes.deletes


async def test_wp98_sync_4_the_record_never_reaches_the_sandbox_and_the_engine_cannot_write_it() -> None:
    harness = FakeSyncHarness(TAR)
    first = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, first, root=harness.root)
    await _checkout(harness, sandbox)
    vouched = dataclasses.replace(state, vouched=frozenset({reference_identity(ADDRESS)}))
    saver = WorkspaceSaver(sync=harness.sync, store=harness.store, scope=harness.scope)
    saved = await saver.save(sandbox, vouched, reason="autosave")
    later = await acquire(harness)
    head = await harness.store.head(harness.scope, saved.state.workspace_id)
    fresh = await harness.sync.materialize(later, head, root=harness.root)
    assert set(await tree(later, harness.root)) == {"task.md"}
    assert fresh.hidden == {STORAGE_RECORD_PATH: STORAGE_RECORD}
    forged = b'{"schema":1,"references":[{"path":"x","address":"https://evil.example/x"}]}'
    await harness.hands.write(later, f"{harness.root}/{STORAGE_RECORD_PATH}", forged)
    collected = await harness.sync.collect(later, fresh)
    assert collected.changes.is_empty
    assert collected.skipped == {STORAGE_RECORD_PATH: STORAGE_RECORD}


async def test_wp98_sync_5_a_later_turn_knows_the_reference_its_sandbox_does_not_hold_yet() -> None:
    harness = FakeSyncHarness(TAR)
    first = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, first, root=harness.root)
    await _checkout(harness, sandbox)
    vouched = dataclasses.replace(state, vouched=frozenset({reference_identity(ADDRESS)}))
    saver = WorkspaceSaver(sync=harness.sync, store=harness.store, scope=harness.scope)
    saved = await saver.save(sandbox, vouched, reason="turn_end")
    # The next turn's sandbox starts without the checkout: the record stands, nothing is deleted.
    later = await acquire(harness)
    head = await harness.store.head(harness.scope, saved.state.workspace_id)
    fresh = await harness.sync.materialize(later, head, root=harness.root)
    assert fresh.references == (ONYX,)
    await harness.hands.write(later, f"{harness.root}/notes.md", b"turn 2")
    again = await saver.save(later, fresh, reason="turn_end")
    assert again.state.manifest is not None
    assert set(again.state.manifest.files) == {"task.md", "notes.md", STORAGE_RECORD_PATH}
    assert again.state.references == (ONYX,)


async def test_wp98_sync_6_scratch_lives_for_the_run_kept_on_a_refresh_and_dropped_at_the_next_start() -> None:
    harness = FakeSyncHarness(VOLUME)
    manifest = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.write(sandbox, f"{harness.root}/notes/tool-results/page-1.json", b"{}")
    await harness.hands.write(sandbox, f"{harness.root}/notes.md", b"notes")
    # Another writer moves the head mid-run: the refresh keeps the engine's scratch.
    _ = await commit(harness, {"upload.csv": b"a,b"})
    saved = await WorkspaceSaver(sync=harness.sync, store=harness.store, scope=harness.scope).save(
        sandbox, state, reason="before_effect"
    )
    assert "notes/tool-results/page-1.json" in await tree(sandbox, harness.root)
    assert saved.state.manifest is not None
    assert "notes/tool-results/page-1.json" not in saved.state.manifest.files
    # The next run's start in the same persistent folder: scratch is gone.
    _ = await harness.sync.materialize(sandbox, saved.state.manifest, root=harness.root)
    assert "notes/tool-results/page-1.json" not in await tree(sandbox, harness.root)


async def test_wp98_sync_7_the_record_counts_against_the_store_s_file_cap() -> None:
    harness = FakeSyncHarness(TAR, limits=SyncLimits(max_workspace_files=3))
    manifest = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await _checkout(harness, sandbox)
    await harness.hands.write(sandbox, f"{harness.root}/a.md", b"a")
    await harness.hands.write(sandbox, f"{harness.root}/b.md", b"b")
    vouched = dataclasses.replace(state, vouched=frozenset({reference_identity(ADDRESS)}))
    collected = await harness.sync.collect(sandbox, vouched)
    assert set(collected.changes.upserts) == {"a.md", STORAGE_RECORD_PATH}
    assert collected.skipped["b.md"] == WORKSPACE_FULL
