"""WP98-FU-1 (ADR-RUN-02): reference checkouts live on their own mount, never in the archive.

``ph git clone`` checks out under ``/pipeshub/references`` and leaves a link where the engine asked. The save archive
then holds one link entry per reference: no checkout is streamed, none counts toward the reader's 50,000-entry bound,
and the registry's link rule records the reference by address and version from the checkout's own git config. The
mount follows the workspace's strategy: scratch beside a scratch workspace (a warm sandbox holds it), the session's
sibling folder beside a persistent one.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator  # noqa: TC003 - the helpers' return type
from pathlib import Path
from typing import Any

from harness_core.engines.controller import adopt_references, references_mount_for
from harness_core.logs import HarnessLog
from harness_core.repos.fetch import FetchConfig, references_mount
from harness_core.sandbox.pool import shape_of
from harness_core.sandbox.types import MountSpec
from harness_core.testing.contracts.workspace_sync import acquire, commit, tree
from harness_core.workspace.sync import TAR, VOLUME, SyncLimits, WorkspaceSaver
from harness_core.workspace.sync.storage_classes import (
    GIT_CLONE_LINK,
    REFERENCES_DIR,
    STORAGE_RECORD_PATH,
    Reference,
    StorageClassRegistry,
    reference_identity,
)
from harness_core.workspace.sync.types import REFERENCE_COPY, SYMLINK_ESCAPES
from harness_engines.pipeshub import ph
from tests.unit.harness_core.workspace.sync.support import FakeSyncHarness

ADDRESS = "https://github.com/onyx-dot-app/onyx"
CHECKOUT = f"{REFERENCES_DIR}/workspace_repos_onyx-0123456789"
CONFIG = b'[remote "origin"]\n\turl = https://github.com/onyx-dot-app/onyx\n[pipeshub]\n\tfetched = true\n\tcommit = 57cb7b8e0f\n'
ONYX = Reference("repos/onyx", ADDRESS, "57cb7b8e0f")


async def _linked_checkout(harness: FakeSyncHarness, sandbox: Any, files: int = 3) -> None:  # noqa: ANN401
    await harness.hands.write(sandbox, f"{CHECKOUT}/.git/config", CONFIG)
    for n in range(files):
        await harness.hands.write(sandbox, f"{CHECKOUT}/src/f{n}.py", b"x = 1\n")
    await harness.hands.symlink(sandbox, f"{harness.root}/repos/onyx", CHECKOUT)


def test_wp98_fu1_1_one_name_and_the_mount_follows_the_workspace() -> None:
    assert ph.REFERENCES_DIR == REFERENCES_DIR == GIT_CLONE_LINK.mount
    target, size = references_mount(FetchConfig(cache_dir=Path("/nonexistent")))
    assert target == REFERENCES_DIR and size > 2 * 500
    scratch = references_mount_for(MountSpec("/workspace"), REFERENCES_DIR, 1016)
    assert scratch == MountSpec(REFERENCES_DIR, size_mb=1016)
    persistent = references_mount_for(MountSpec("/workspace", source="t/s/workspace"), REFERENCES_DIR, 1016)
    assert persistent == MountSpec(REFERENCES_DIR, source="t/s/workspace-references")
    # Only a checkout folder directly under the mount is claimed; anything else stays a link the sync refuses.
    assert GIT_CLONE_LINK.checkout_of(CHECKOUT) == CHECKOUT
    assert GIT_CLONE_LINK.checkout_of(f"{REFERENCES_DIR}/a/../b") == f"{REFERENCES_DIR}/b"
    for other in (REFERENCES_DIR, f"{REFERENCES_DIR}/a/b", "/etc", "repos/x", f"{REFERENCES_DIR}/../etc"):
        assert GIT_CLONE_LINK.checkout_of(other) is None, other


async def test_wp98_fu1_2_a_25000_file_reference_is_neither_streamed_nor_counted_and_the_person_s_files_are_saved() -> None:
    harness = FakeSyncHarness(TAR, limits=SyncLimits(max_entries=1_000), references=True)
    manifest = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await _linked_checkout(harness, sandbox, files=25_000)
    await harness.hands.write(sandbox, f"{harness.root}/out/report.md", b"# Report\n")
    vouched = dataclasses.replace(state, vouched=frozenset({reference_identity(ADDRESS)}))
    collected = await harness.sync.collect(sandbox, vouched)
    assert collected.skipped == {"repos/onyx": REFERENCE_COPY}
    assert collected.references == (ONYX,)
    saved = await WorkspaceSaver(sync=harness.sync, store=harness.store, scope=harness.scope).save(
        sandbox, vouched, reason="turn_end"
    )
    assert saved.state.manifest is not None
    assert set(saved.state.manifest.files) == {"task.md", "out/report.md", STORAGE_RECORD_PATH}
    assert saved.state.references == (ONYX,)
    assert harness.objects.puts < 10  # the person's two files and the record, never the checkout


async def test_wp98_fu1_3_a_link_is_kept_on_a_refresh_and_at_the_next_start_and_an_unvouched_one_is_never_recorded() -> None:
    for strategy in (TAR, VOLUME):
        harness = FakeSyncHarness(strategy, references=True)
        manifest = await commit(harness, {"task.md": b"task"})
        sandbox = await acquire(harness)
        state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
        await _linked_checkout(harness, sandbox)
        collected = await harness.sync.collect(sandbox, state)
        assert collected.skipped == {"repos/onyx": REFERENCE_COPY} and collected.references == (), strategy.name
        await harness.sync.materialize(sandbox, manifest, root=harness.root, keep_scratch=True)
        await harness.sync.materialize(sandbox, manifest, root=harness.root)
        assert "repos/onyx" in await tree(sandbox, harness.root), strategy.name


async def test_wp98_fu1_4_an_unreadable_checkout_keeps_its_link_and_records_nothing_and_a_foreign_link_still_escapes() -> None:
    harness = FakeSyncHarness(TAR, references=True)
    manifest = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.symlink(sandbox, f"{harness.root}/repos/gone", f"{REFERENCES_DIR}/gone-0000000000")
    await harness.hands.symlink(sandbox, f"{harness.root}/etc-link", "/etc")
    vouched = dataclasses.replace(state, vouched=frozenset({reference_identity(ADDRESS)}))
    collected = await harness.sync.collect(sandbox, vouched)
    assert collected.skipped == {"repos/gone": REFERENCE_COPY, "etc-link": SYMLINK_ESCAPES}
    assert collected.references == ()


async def test_wp98_fu1_5_the_controller_moves_an_old_in_workspace_checkout_with_ph_git_adopt() -> None:
    calls: list[list[str]] = []

    class Process:
        def __init__(self, code: int) -> None:
            self.stdout, self.stderr, self._code = _empty(), _empty(), code

        async def wait(self) -> int:
            return self._code

    class Sandbox:
        async def exec(self, argv: list[str], env: dict[str, str]) -> Process:
            calls.append(argv)
            return Process(0 if argv[-1].endswith("onyx") else 1)

    refs = (ONYX, Reference("repos/docs", "https://github.com/onyx-dot-app/docs", "1234567"))
    await adopt_references(Sandbox(), "/workspace", refs, HarnessLog.named("test"))  # type: ignore[arg-type]
    assert calls == [
        ["/usr/local/bin/ph", "git", "adopt", "/workspace/repos/onyx"],
        ["/usr/local/bin/ph", "git", "adopt", "/workspace/repos/docs"],
    ]


def test_wp98_fu1_6_the_registry_declares_the_link_and_a_scratch_references_mount_keeps_the_warm_shape() -> None:
    assert GIT_CLONE_LINK in StorageClassRegistry.default().links
    build = dataclasses.replace(StorageClassRegistry.default().rules[0], folder="build")
    assert StorageClassRegistry.default().declare(build).links == (GIT_CLONE_LINK,)
    from harness_core.engines.controller import (
        EngineControllerConfig,
        engine_sandbox_spec,
    )
    from harness_core.sandbox.types import RuntimeMode

    config = EngineControllerConfig(images={}, references_mount=(REFERENCES_DIR, 1016))
    spec = engine_sandbox_spec(
        config, image="img@sha256:" + "0" * 64, mode=RuntimeMode.DOCKER_HARDENED, workspace=TAR.mount("/workspace"),
        labels={}, placeholders=("pxh-m", "pxh-t"), tools_upstream="http://tool-host/x",
    )
    assert MountSpec(REFERENCES_DIR, size_mb=1016) in spec.mounts
    assert shape_of(spec) is not None, "a warm sandbox can hold the scratch references mount"


async def _nothing() -> AsyncIterator[bytes]:
    for chunk in ():
        yield chunk


def _empty() -> AsyncIterator[bytes]:
    return _nothing()
