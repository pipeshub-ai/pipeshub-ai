"""PF-1: random trees and random engine edits survive materialize → collect → commit → a fresh sandbox."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from harness_core.testing.contracts.workspace_sync import (
    WORKSPACE,
    acquire,
    commit,
    tree,
)
from harness_core.workspace import EXECUTABLE_MODE, FILE_MODE, SYMLINK_MODE
from harness_core.workspace.sync import TAR, VOLUME, SyncStrategy
from tests.unit.harness_core.workspace.sync.support import FakeSyncHarness

EXAMPLES = 40
_SEGMENTS = st.sampled_from(["a", "b", "src", "out", "é", "x y", "data.bin"])
_PATHS = st.lists(_SEGMENTS, min_size=1, max_size=3).map("/".join)
_MODES = st.sampled_from([FILE_MODE, EXECUTABLE_MODE, 0o100600])


@dataclass(frozen=True)
class Example:
    files: dict[str, tuple[bytes, int]]
    links: dict[str, str]
    writes: dict[str, tuple[bytes, int]]
    removes: frozenset[str]


def _leaves(paths: set[str]) -> set[str]:
    """Drop any path that another path needs as a folder: a tree has files at its leaves only."""
    return {p for p in paths if not any(o.startswith(p + "/") for o in paths)}


def _relative(link: str, target: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(link) or ".")


def _draw_examples() -> list[Example]:
    examples: list[Example] = []

    @settings(max_examples=EXAMPLES, deadline=None, database=None, suppress_health_check=list(HealthCheck))
    @given(data=st.data())
    def draw(data: st.DataObject) -> None:
        paths = _leaves(data.draw(st.sets(_PATHS, min_size=1, max_size=8)))
        files = {p: (data.draw(st.binary(max_size=200)), data.draw(_MODES)) for p in sorted(paths)}
        names = sorted(files)
        link_paths = sorted(_leaves(set(data.draw(st.sets(st.sampled_from(["lnk", "a/lnk2"]), max_size=2)))) - paths)
        links = {
            link: _relative(link, data.draw(st.sampled_from(names)))
            for link in link_paths
            if not any(p.startswith(link + "/") or link.startswith(p + "/") for p in paths)
        }
        removes = data.draw(st.frozensets(st.sampled_from(names), max_size=len(names)))
        new_paths = data.draw(st.sets(_PATHS, max_size=4))
        occupied = (set(files) - removes) | set(links)
        writes = {
            p: (data.draw(st.binary(max_size=100)), data.draw(_MODES))
            for p in sorted(new_paths)
            if not any(o.startswith(p + "/") or p.startswith(o + "/") for o in occupied | (new_paths - {p}))
        }
        examples.append(Example(files, links, writes, removes - set(writes)))

    draw()
    return examples


@pytest.mark.parametrize("strategy", [TAR, VOLUME], ids=["tar", "volume"])
async def test_pf1_round_trip(strategy: SyncStrategy) -> None:
    for index, example in enumerate(_draw_examples()):
        harness = FakeSyncHarness(strategy)
        manifest = await commit(
            harness,
            {p: data for p, (data, _) in example.files.items()},
            modes={p: mode for p, (_, mode) in example.files.items()},
            links=example.links,
        )
        sandbox = await acquire(harness)
        root = harness.root
        state = await harness.sync.materialize(sandbox, manifest, root=root)
        model = {p: (mode, data) for p, (data, mode) in example.files.items()}
        model.update({p: (SYMLINK_MODE, target.encode()) for p, target in example.links.items()})
        assert await tree(sandbox, root) == model, (index, example)
        assert (await harness.sync.collect(sandbox, state)).changes.is_empty, (index, example)
        for path in sorted(example.removes):
            await harness.hands.remove(sandbox, f"{root}/{path}")
            del model[path]
        for path, (data, mode) in example.writes.items():
            await harness.hands.write(sandbox, f"{root}/{path}", data, mode & 0o777)
            model[path] = (mode, data)
        collected = await harness.sync.collect(sandbox, state)
        head = await harness.store.commit(
            harness.scope, WORKSPACE, base_revision=state.revision, changes=collected.changes, reason="autosave"
        )
        await harness.broker.release(sandbox)
        fresh = await acquire(harness)
        await harness.sync.materialize(fresh, head, root=root)
        assert await tree(fresh, root) == model, (index, example)
        await harness.broker.release(fresh)
