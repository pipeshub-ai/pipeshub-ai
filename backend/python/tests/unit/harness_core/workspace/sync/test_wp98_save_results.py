"""WP98-SAVE-1..6: a save answers ``Saved``, ``PartialSave`` or ``Refused`` and never raises a refusal the store gives as
sent (ADR-RUN-01 D2, D6).

In a long research run, a refused save raised out of the turn as an ``ExceptionGroup`` and 36 minutes of work were thrown away;
WP-82 caught it in the controller. The saver now answers with a value, the controller matches on it.
"""

from __future__ import annotations

from typing import Any, override

import pytest

from harness_core.engines.outcome import faults
from harness_core.testing.contracts.workspace_sync import acquire, commit
from harness_core.workspace import MAX_WORKSPACE_FILES
from harness_core.workspace.memory import InMemoryWorkspaceStore
from harness_core.workspace.sync import TAR, SyncLimits, WorkspaceSaver
from harness_core.workspace.sync.saver import SAVE_REFUSED, PartialSave, Refused, Saved
from harness_core.workspace.sync.types import WORKSPACE_FULL
from harness_core.workspace.types import (
    SAVE_REFUSALS,
    InvalidChangeSet,
    WorkspaceRequestRefused,
    WorkspaceStoreUnavailable,
)
from tests.unit.harness_core.workspace.sync.support import FakeSyncHarness


class OutputsOnly(InMemoryWorkspaceStore):
    """Refuses, as Node refused run 6's saves, any commit that writes outside out/."""

    def __init__(self, inner: InMemoryWorkspaceStore, error: type[Exception] = InvalidChangeSet) -> None:
        super().__init__()
        self.inner = inner
        self.error = error

    @override
    async def commit(self, *args: Any, **kwargs: Any) -> Any:
        if any(not path.startswith("out/") for path in kwargs["changes"].paths):
            raise self.error("WORKSPACE_TOO_LARGE")
        return await self.inner.commit(*args, **kwargs)

    @override
    async def head(self, *args: Any, **kwargs: Any) -> Any:
        return await self.inner.head(*args, **kwargs)


class Refusing(InMemoryWorkspaceStore):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    @override
    async def commit(self, *args: Any, **kwargs: Any) -> Any:
        raise self.error


async def _world(limits: SyncLimits | None = None) -> tuple[FakeSyncHarness, Any, Any]:
    harness = FakeSyncHarness(TAR, limits=limits)
    manifest = await commit(harness, {"task.md": b"task"})
    sandbox = await acquire(harness)
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    await harness.hands.write(sandbox, f"{harness.root}/notes.md", b"notes")
    await harness.hands.write(sandbox, f"{harness.root}/out/report.md", b"# Report")
    return harness, sandbox, state


def _outputs(path: str) -> bool:
    return path.startswith("out/")


async def test_wp98_save_1_a_save_the_store_takes_is_saved_with_its_revision() -> None:
    harness, sandbox, state = await _world()
    result = await WorkspaceSaver(sync=harness.sync, store=harness.store, scope=harness.scope).save(
        sandbox, state, reason="turn_end", fallback=_outputs
    )
    assert isinstance(result, Saved)
    assert result.revision == state.revision + 1


async def test_wp98_save_2_files_past_the_cap_are_a_partial_save_decided_before_the_request() -> None:
    assert MAX_WORKSPACE_FILES == SyncLimits().max_workspace_files
    harness, sandbox, state = await _world(SyncLimits(max_workspace_files=2))
    result = await WorkspaceSaver(sync=harness.sync, store=harness.store, scope=harness.scope).save(
        sandbox, state, reason="turn_end"
    )
    assert isinstance(result, PartialSave)
    assert (result.reason, dict(result.skipped)) == (WORKSPACE_FULL, {"out/report.md": WORKSPACE_FULL})
    assert result.saved.manifest is not None
    assert set(result.saved.manifest.files) == {"task.md", "notes.md"}


@pytest.mark.parametrize("error", [InvalidChangeSet, WorkspaceRequestRefused])
async def test_wp98_save_3_a_refused_change_set_saves_the_deliverables_on_their_own(error: type[Exception]) -> None:
    harness, sandbox, state = await _world()
    store = OutputsOnly(harness.store, error)
    result = await WorkspaceSaver(sync=harness.sync, store=store, scope=harness.scope).save(
        sandbox, state, reason="turn_end", fallback=_outputs
    )
    assert isinstance(result, PartialSave)
    assert result.reason == SAVE_REFUSED
    assert dict(result.skipped) == {"notes.md": SAVE_REFUSED}
    assert result.refusal is not None and error.__name__ in result.refusal
    assert result.saved.manifest is not None
    assert set(result.saved.manifest.files) == {"task.md", "out/report.md"}


async def test_wp98_save_4_a_save_refused_whole_is_a_value_never_an_exception() -> None:
    harness, sandbox, state = await _world()
    saver = WorkspaceSaver(sync=harness.sync, store=Refusing(InvalidChangeSet("WORKSPACE_TOO_LARGE")), scope=harness.scope)
    with_fallback = await saver.save(sandbox, state, reason="turn_end", fallback=_outputs)
    assert isinstance(with_fallback, Refused)
    assert (with_fallback.attempted_fallback, with_fallback.error, with_fallback.state) == (True, "InvalidChangeSet", state)
    plain = await saver.save(sandbox, state, reason="autosave")
    assert isinstance(plain, Refused) and not plain.attempted_fallback


async def test_wp98_save_5_an_outage_is_not_a_refusal_and_still_raises() -> None:
    harness, sandbox, state = await _world()
    saver = WorkspaceSaver(sync=harness.sync, store=Refusing(WorkspaceStoreUnavailable("down")), scope=harness.scope)
    with pytest.raises(WorkspaceStoreUnavailable):
        _ = await saver.save(sandbox, state, reason="turn_end", fallback=_outputs)


def test_wp98_save_6_one_tuple_names_the_refusals_the_saver_and_the_degrade_table_share() -> None:
    assert faults.SAVES_REFUSED is SAVE_REFUSALS
    assert faults.DEGRADE["save_refused"] == "keep_last_revision"
