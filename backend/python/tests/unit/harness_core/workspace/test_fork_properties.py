"""P6.4 PR-1..PR-4: properties of fork merges on the in-memory store (Hypothesis).

Hypothesis draws the examples and the store runs them (a coroutine cannot run
inside ``given``); a failure names its example.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.workspace import file
from harness_core.workspace import ChangeSet, ObjectScope, WorkspaceFile
from harness_core.workspace.memory import InMemoryWorkspaceStore

_SCOPE = ObjectScope("t", "ws:s")
_PATHS = st.sampled_from(["a", "b", "c/d", "e.txt", "src/x.py", "é"])
_TEXTS = st.sampled_from(["1", "2", "3", ""])
_FILES = st.dictionaries(_PATHS, _TEXTS, max_size=5)
_EXAMPLES = 60

Files = dict[str, str]
Change = tuple[Files, frozenset[str]]


def _draw[T](strategy: st.SearchStrategy[T]) -> list[T]:
    drawn: list[T] = []

    @settings(max_examples=_EXAMPLES, deadline=None, database=None)
    @given(strategy)
    def collect(example: T) -> None:
        drawn.append(example)

    collect()
    return drawn


@st.composite
def _change(draw: st.DrawFn, base: Files, paths: st.SearchStrategy[str] = _PATHS) -> Change:
    writes = draw(st.dictionaries(paths, _TEXTS, max_size=4))
    deletable = sorted(set(base) - set(writes))
    deletes = draw(st.frozensets(st.sampled_from(deletable))) if deletable else frozenset[str]()
    return writes, deletes


def _cs(change: Change) -> ChangeSet:
    writes, deletes = change
    return ChangeSet.of(*(file(p, t) for p, t in writes.items()), deletes=deletes)


def _hashes(files: dict[str, WorkspaceFile] | object) -> dict[str, str]:
    return {p: e.content.content_hash for p, e in dict(files).items()}  # type: ignore[call-overload]


async def _setup(base: Files) -> tuple[InMemoryWorkspaceStore, str]:
    store = InMemoryWorkspaceStore(clock=ManualClock(datetime(2026, 10, 3, tzinfo=UTC)))
    if base:
        _ = await store.commit(
            _SCOPE, "ws:s", base_revision=0, changes=ChangeSet.of(*(file(p, t) for p, t in base.items())), reason="seed"
        )
    return store, "ws:s"


async def _commit(store: InMemoryWorkspaceStore, workspace: str, change: Change) -> None:
    head = await store.head(_SCOPE, workspace)
    _ = await store.commit(_SCOPE, workspace, base_revision=head.revision, changes=_cs(change), reason="autosave")


async def _fork(store: InMemoryWorkspaceStore, name: str) -> str:
    head = await store.head(_SCOPE, "ws:s")
    fork_id = f"ws:s:fork:{name}"
    _ = await store.fork(_SCOPE, "ws:s", at_revision=head.revision, fork_id=fork_id)
    return fork_id


@st.composite
def _base_and_change(draw: st.DrawFn) -> tuple[Files, Change]:
    base = draw(_FILES)
    return base, draw(_change(base))


async def test_pr1_merging_a_fork_with_no_changes_is_a_no_op() -> None:
    for base, target_change in _draw(_base_and_change()):
        store, parent = await _setup(base)
        fork = await _fork(store, "idle")
        await _commit(store, parent, target_change)
        before = await store.head(_SCOPE, parent)
        for policy in ("refuse", "keep_target"):
            result = await store.merge(_SCOPE, fork_id=fork, into=parent, on_conflict=policy)
            assert (result.status, result.merged) == ("up_to_date", ()), (base, target_change)
            assert await store.head(_SCOPE, parent) == before == result.manifest


@st.composite
def _two_sided(draw: st.DrawFn) -> tuple[Files, Change, Change]:
    base = draw(_FILES)
    return base, draw(_change(base)), draw(_change(base))


async def test_pr2_a_keyed_merge_is_idempotent() -> None:
    for base, ours, theirs in _draw(_two_sided()):
        store, parent = await _setup(base)
        fork = await _fork(store, "c")
        await _commit(store, parent, ours)
        await _commit(store, fork, theirs)
        first = await store.merge(_SCOPE, fork_id=fork, into=parent, key="c", on_conflict="keep_target")
        head = await store.head(_SCOPE, parent)
        second = await store.merge(_SCOPE, fork_id=fork, into=parent, key="c", on_conflict="keep_target")
        assert second == first, (base, ours, theirs)
        assert await store.head(_SCOPE, parent) == head
        unkeyed = await store.merge(_SCOPE, fork_id=fork, into=parent, on_conflict="keep_target")
        assert unkeyed.status == "up_to_date" and await store.head(_SCOPE, parent) == head


@st.composite
def _disjoint(draw: st.DrawFn) -> tuple[Files, Change, Change]:
    base = draw(_FILES)
    left_paths = st.sampled_from(["a", "c/d", "src/x.py"])
    right_paths = st.sampled_from(["b", "e.txt", "é"])
    left = draw(_change({p: t for p, t in base.items() if p in {"a", "c/d", "src/x.py"}}, left_paths))
    right = draw(_change({p: t for p, t in base.items() if p in {"b", "e.txt", "é"}}, right_paths))
    return base, left, right


async def test_pr3_non_overlapping_changes_commute() -> None:
    for base, left, right in _draw(_disjoint()):
        outcomes: list[dict[str, str]] = []
        for order in (("l", "r"), ("r", "l")):
            store, parent = await _setup(base)
            forks = {"l": await _fork(store, "l"), "r": await _fork(store, "r")}
            await _commit(store, forks["l"], left)
            await _commit(store, forks["r"], right)
            for name in order:
                result = await store.merge(_SCOPE, fork_id=forks[name], into=parent, on_conflict="keep_target")
                assert result.status in ("merged", "up_to_date") and not result.conflicts, (base, left, right)
            outcomes.append(_hashes((await store.head(_SCOPE, parent)).files))
        assert outcomes[0] == outcomes[1], (base, left, right)


async def test_pr4_keep_target_never_loses_the_targets_version() -> None:
    for base, ours, theirs in _draw(_two_sided()):
        store, parent = await _setup(base)
        fork = await _fork(store, "c")
        await _commit(store, parent, ours)
        await _commit(store, fork, theirs)
        target = _hashes((await store.head(_SCOPE, parent)).files)
        forked = _hashes((await store.head(_SCOPE, fork)).files)
        result = await store.merge(_SCOPE, fork_id=fork, into=parent, on_conflict="keep_target")
        merged = _hashes(result.manifest.files)
        example = (base, ours, theirs)
        for conflict in result.conflicts:
            assert merged.get(conflict.path) == target.get(conflict.path), example
        conflicting = {c.path for c in result.conflicts}
        base_hashes = {p: file(p, t).content.content_hash for p, t in base.items()}
        for path in set(forked) | set(base_hashes):
            if path in conflicting or forked.get(path) == base_hashes.get(path):
                continue
            assert merged.get(path) == forked.get(path), example
