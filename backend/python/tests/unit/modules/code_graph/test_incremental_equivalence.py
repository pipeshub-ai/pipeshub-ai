"""An incremental build must leave the graph a full rebuild would (R-3, R-7, T-3).

Each seed grows a small Python repo, then applies random edits -- definitions
added, removed and moved, calls retargeted, imports changed, files added and
deleted -- as a sync would. After every edit the changed files are re-projected,
deleted ones dropped, and an incremental build runs on exactly what production
would hand it: the records updated since the last build. A deleted record is
gone from the graph, so it is never among them; a deletion asks for a full
build instead. The result is compared with a fresh full build over the same
files.
"""
import os
import random

import pytest

from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    write_code_file_blocks_to_graph,
)
from app.modules.code_graph.edge_builder import build_code_graph_edges
from app.modules.parsers.code_parser import CodeFileParser

from .conftest import GROUP_ID, ORG_ID, FakeGraphProvider

NAMES = [f"f{i}" for i in range(6)]
# CODE_GRAPH_EQ_SEEDS / _STEPS widen the search when changing the builder.
SEEDS = range(int(os.environ.get("CODE_GRAPH_EQ_SEEDS", "40")))
STEPS = int(os.environ.get("CODE_GRAPH_EQ_STEPS", "12"))


Graph = FakeGraphProvider


class Repo:
    """modules -> {defined function -> [(called name, import module or None)]}."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.modules: dict[str, dict[str, list[tuple[str, str | None]]]] = {}
        for i in range(4):
            self.add_module(f"m{i}")
        for mod in list(self.modules):
            for fn in list(self.modules[mod]):
                for _ in range(rng.randint(0, 2)):
                    self.add_call(mod, fn)

    def add_module(self, mod: str) -> None:
        self.modules[mod] = {n: [] for n in self.rng.sample(NAMES, self.rng.randint(1, 3))}

    def add_call(self, mod: str, fn: str) -> None:
        callee = self.rng.choice(NAMES)
        others = [m for m in self.modules if m != mod]
        source = self.rng.choice([None, *others]) if others else None
        self.modules[mod][fn].append((callee, source))

    def source(self, mod: str) -> str:
        imports = sorted({(src, name) for calls in self.modules[mod].values()
                          for name, src in calls if src})
        lines = [f"from pkg.{src} import {name}" for src, name in imports]
        for fn, calls in self.modules[mod].items():
            lines.append(f"\n\ndef {fn}():")
            lines += [f"    {name}()" for name, _ in calls] or ["    return None"]
        return "\n".join(lines) + "\n"

    def edit(self) -> tuple[set[str], set[str]]:
        """Apply one random edit; return (changed modules, deleted modules)."""
        rng, mods = self.rng, list(self.modules)
        mod = rng.choice(mods)
        kind = rng.choice(["add_def", "drop_def", "move_def", "retarget", "add_file", "drop_file"])
        if kind == "add_def":
            free = [n for n in NAMES if n not in self.modules[mod]]
            if free:
                self.modules[mod][rng.choice(free)] = []
        elif kind == "drop_def" and len(self.modules[mod]) > 1:
            self.modules[mod].pop(rng.choice(list(self.modules[mod])))
        elif kind == "move_def" and len(mods) > 1:
            dst = rng.choice([m for m in mods if m != mod])
            name = rng.choice(list(self.modules[mod]))
            if name not in self.modules[dst] and len(self.modules[mod]) > 1:
                self.modules[dst][name] = self.modules[mod].pop(name)
                return {mod, dst}, set()
        elif kind == "retarget":
            fn = rng.choice(list(self.modules[mod]))
            if self.modules[mod][fn]:
                self.modules[mod][fn].pop(rng.randrange(len(self.modules[mod][fn])))
            self.add_call(mod, fn)
        elif kind == "add_file":
            new = f"m{len(mods) + rng.randint(0, 99)}"
            if new not in self.modules:
                self.add_module(new)
                for fn in self.modules[new]:
                    self.add_call(new, fn)
                return {new}, set()
        elif kind == "drop_file" and len(mods) > 2:
            self.modules.pop(mod)
            return set(), {mod}
        return {mod}, set()


def rid(mod: str) -> str:
    return f"rec-{mod}"


async def project(graph: Graph, parser: CodeFileParser, repo: Repo, mod: str) -> None:
    path = f"pkg/{mod}.py"
    container = parser.parse_to_blocks(repo.source(mod).encode(), f"{mod}.py", path, "python")
    graph.code_files[rid(mod)] = path
    graph.blobs[rid(mod)] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
    await write_code_file_blocks_to_graph(
        graph_provider=graph,
        context=BlockProjectionContext(org_id=ORG_ID, record_id=rid(mod), record_group_id=GROUP_ID,
                                       connector_id="conn1", language="python", file_path=path),
        block_containers=container,
    )


async def full_graph(parser: CodeFileParser, repo: Repo) -> Graph:
    graph = Graph()
    for mod in repo.modules:
        await project(graph, parser, repo, mod)
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 facts_source=graph)
    return graph


def code_edges(graph: Graph) -> set[tuple[str, str, str, str]]:
    return {(e["_from"], e["_to"], e["relationshipType"], str(e.get("resolution")))
            for e in graph.code_edges()}


def describe(graph: Graph, edges: set) -> list[str]:
    def name(node: str) -> str:
        collection, _, key = node.partition("/")
        block = graph.blocks.get(key)
        if block:
            return f"{graph.code_files.get(block['recordId'], '?')}::{block.get('qualifiedName')}"
        return graph.code_files.get(key, node)
    return sorted(f"{name(a)} -{rel}-> {name(b)}" for a, b, rel, _ in edges)


@pytest.mark.parametrize("seed", SEEDS)
async def test_incremental_build_matches_full_build(seed: int) -> None:
    rng = random.Random(seed)
    parser = CodeFileParser()
    repo = Repo(rng)
    graph = await full_graph(parser, repo)
    history: list[str] = []

    for step in range(STEPS):
        changed, deleted = repo.edit()
        history.append(f"step {step}: changed {sorted(changed)} deleted {sorted(deleted)}")
        for mod in deleted:
            await graph.delete_blocks_for_records([rid(mod)])
            graph.code_files.pop(rid(mod), None)
            graph.blobs.pop(rid(mod), None)
            graph.referenced_names.pop(rid(mod), None)
        for mod in changed:
            await project(graph, parser, repo, mod)
        # As the runner decides: a deletion asks for a full build; otherwise
        # the records updated since the last one, if any.
        touched = None if deleted else {rid(m) for m in changed}
        if touched is None or touched:
            await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID,
                                         record_group_id=GROUP_ID, touched_record_ids=touched,
                                         facts_source=graph)

        expected = await full_graph(parser, repo)
        got, want = code_edges(graph), code_edges(expected)
        assert got == want, (
            f"seed {seed}, {history[-1]}\n"
            + "\n".join(history[:-1]) + "\n"
            + "missing: " + "; ".join(describe(expected, want - got)) + "\n"
            + "extra: " + "; ".join(describe(graph, got - want))
        )
