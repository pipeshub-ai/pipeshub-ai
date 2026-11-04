"""An incremental build must leave the graph a full rebuild would (R-3, R-7, T-3).

Each seed grows a small Python repo, then applies random edits -- definitions
added, removed and moved, calls retargeted, imports changed, files added and
deleted -- as a sync would. After every edit the changed files are re-projected,
deleted ones dropped, and an incremental build runs on exactly what production
would hand it: the records updated since the last build, and the names each
deleted file defined, kept just before it was deleted (a deleted record is gone
from the graph, so it is never among the updated ones). A step applies one to
three edits at once, as a sync or a branch switch does. The result is compared
with a fresh full build over the same files.
"""
import asyncio
import os
import random

import pytest

from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    write_code_file_blocks_to_graph,
)
from app.modules.code_graph.deleted_files import defined_names
from app.modules.code_graph.edge_builder import build_code_graph_edges
from app.modules.parsers.code_parser import CodeFileParser

from .conftest import GROUP_ID, ORG_ID, FakeGraphProvider

NAMES = [f"f{i}" for i in range(6)]
# CODE_GRAPH_EQ_SEEDS / _STEPS widen the search when changing the builder.
SEEDS = range(int(os.environ.get("CODE_GRAPH_EQ_SEEDS", "40")))
STEPS = int(os.environ.get("CODE_GRAPH_EQ_STEPS", "12"))
MAX_EDITS = int(os.environ.get("CODE_GRAPH_EQ_MAX_EDITS", "3"))


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
        changed: set[str] = set()
        deleted: set[str] = set()
        for _ in range(rng.randint(1, MAX_EDITS)):
            more_changed, more_deleted = repo.edit()
            changed |= more_changed
            deleted |= more_deleted
        # What the step left: a file dropped after an edit is deleted, and one
        # dropped and added again under its name is edited.
        touched_mods = changed | deleted
        changed = touched_mods & set(repo.modules)
        deleted = touched_mods - set(repo.modules)
        history.append(f"step {step}: changed {sorted(changed)} deleted {sorted(deleted)}")
        deleted_names: set[str] = set()
        for mod in deleted:
            deleted_names |= await defined_names(graph, ORG_ID, rid(mod))
            await graph.delete_blocks_for_records([rid(mod)])
            graph.code_files.pop(rid(mod), None)
            graph.blobs.pop(rid(mod), None)
            graph.referenced_names.pop(rid(mod), None)
        for mod in changed:
            await project(graph, parser, repo, mod)
        # As the runner decides: the records updated since the last build and
        # the deleted files' names; nothing to do when there are neither.
        touched = {rid(m) for m in changed}
        if touched or deleted:
            await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID,
                                         record_group_id=GROUP_ID, touched_record_ids=touched,
                                         deleted_names=deleted_names, facts_source=graph)

        expected = await full_graph(parser, repo)
        got, want = code_edges(graph), code_edges(expected)
        assert got == want, (
            f"seed {seed}, {history[-1]}\n"
            + "\n".join(history[:-1]) + "\n"
            + "missing: " + "; ".join(describe(expected, want - got)) + "\n"
            + "extra: " + "; ".join(describe(graph, got - want))
        )


async def test_a_value_imported_through_a_package_resolves_the_same_incrementally() -> None:
    """`from pkg import app_registry`, with pkg/__init__.py re-exporting `app_registry = Apps()`: an
    incremental build that re-resolves only the caller must follow the `__init__` as a
    full build does, though the `__init__` itself was not re-resolved."""
    # `app_registry` spells no class and `get_model` is defined twice, so only the import chain types it.
    files = {
        "pkg/__init__.py": "from pkg.registry import app_registry\n",
        "pkg/registry.py": "class Apps:\n    def get_model(self, name):\n        return name\n\napp_registry = Apps()\n",
        "pkg/other.py": "class Cache:\n    def get_model(self, name):\n        return None\n",
        "pkg/use.py": "from pkg import app_registry\n\n\ndef run():\n    return app_registry.get_model('a')\n",
    }
    parser = CodeFileParser()

    async def put(graph: Graph, path: str, source: str) -> None:
        record = f"rec-{path}"
        container = parser.parse_to_blocks(source.encode(), path.rsplit("/", 1)[-1], path, "python")
        graph.code_files[record] = path
        graph.blobs[record] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=ORG_ID, record_id=record, record_group_id=GROUP_ID,
                                           connector_id="conn1", language="python", file_path=path),
            block_containers=container,
        )

    async def full() -> Graph:
        graph = Graph()
        for path, source in files.items():
            await put(graph, path, source)
        await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                     facts_source=graph)
        return graph

    graph = await full()
    files["pkg/use.py"] += "\n\ndef other():\n    return app_registry.get_model('b')\n"
    await put(graph, "pkg/use.py", files["pkg/use.py"])
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"rec-pkg/use.py"}, facts_source=graph)
    expected = await full()
    want = code_edges(expected)
    assert any("get_model" in line for line in describe(expected, want)), "the full build links the call"
    assert code_edges(graph) == want, "\n".join(describe(graph, code_edges(graph) ^ want))


async def test_an_inherited_method_resolves_the_same_incrementally() -> None:
    """`Service().ping()` where Service, in a file this build does not re-resolve,
    inherits the `Base` it imports and another file defines a `Base` too."""
    files = {
        "pkg/__init__.py": "",
        "pkg/a/__init__.py": "",
        "pkg/a/base.py": "class Base:\n    def ping(self):\n        pass\n",
        "pkg/b/__init__.py": "",
        "pkg/b/base.py": "class Base:\n    def ping(self):\n        pass\n",
        "pkg/svc.py": "from pkg.a.base import Base\n\n\nclass Service(Base):\n    pass\n",
        "pkg/use.py": "from pkg.svc import Service\n\n\ndef run():\n    return Service().ping()\n",
    }
    parser = CodeFileParser()

    async def put(graph: Graph, path: str, source: str) -> None:
        record = f"rec-{path}"
        container = parser.parse_to_blocks(source.encode(), path.rsplit("/", 1)[-1], path, "python")
        graph.code_files[record] = path
        graph.blobs[record] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=ORG_ID, record_id=record, record_group_id=GROUP_ID,
                                           connector_id="conn1", language="python", file_path=path),
            block_containers=container,
        )

    async def full() -> Graph:
        graph = Graph()
        for path, source in files.items():
            await put(graph, path, source)
        await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                     facts_source=graph)
        return graph

    graph = await full()
    files["pkg/use.py"] += "\n\ndef other():\n    return Service().ping()\n"
    await put(graph, "pkg/use.py", files["pkg/use.py"])
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"rec-pkg/use.py"}, facts_source=graph)
    expected = await full()
    want = code_edges(expected)
    assert any("a/base.py::method:Base.ping" in line for line in describe(expected, want)), "the full build links the call"
    assert code_edges(graph) == want, "\n".join(describe(graph, code_edges(graph) ^ want))


async def test_a_caller_keeps_its_edges_when_a_dependency_cannot_be_read() -> None:
    """The retry re-resolves files whose bindings a chain read. When one cannot be
    read, the build still finishes, and the caller that needed it keeps the edges it
    had rather than being rewritten without them."""
    files = {
        "pkg/__init__.py": "",
        "pkg/a/__init__.py": "",
        "pkg/a/base.py": "class Base:\n    def ping(self):\n        pass\n",
        "pkg/b/__init__.py": "",
        "pkg/b/base.py": "class Base:\n    def ping(self):\n        pass\n",
        "pkg/svc.py": "from pkg.a.base import Base\n\n\nclass Service(Base):\n    pass\n",
        "pkg/use.py": "from pkg.svc import Service\n\n\ndef run():\n    return Service().ping()\n",
    }
    parser = CodeFileParser()

    async def put(graph: Graph, path: str, source: str) -> None:
        record = f"rec-{path}"
        container = parser.parse_to_blocks(source.encode(), path.rsplit("/", 1)[-1], path, "python")
        graph.code_files[record] = path
        graph.blobs[record] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=ORG_ID, record_id=record, record_group_id=GROUP_ID,
                                           connector_id="conn1", language="python", file_path=path),
            block_containers=container,
        )

    async def full() -> Graph:
        graph = Graph()
        for path, source in files.items():
            await put(graph, path, source)
        await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                     facts_source=graph)
        return graph

    graph = await full()
    before = {line for line in describe(graph, code_edges(graph)) if "use.py::function:run -CALLS->" in line}
    assert "pkg/use.py::function:run -CALLS-> pkg/a/base.py::method:Base.ping" in before
    del graph.blobs["rec-pkg/svc.py"]
    await put(graph, "pkg/use.py", files["pkg/use.py"])
    await asyncio.wait_for(build_code_graph_edges(
        graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
        touched_record_ids={"rec-pkg/use.py"}, facts_source=graph), timeout=20)
    after = {line for line in describe(graph, code_edges(graph)) if "use.py::function:run -CALLS->" in line}
    assert after == before



async def test_a_long_chain_of_imported_bases_resolves_the_same_incrementally() -> None:
    """Each base class is in its own file and imports the next, and every name is
    defined twice: a build has to follow five files of import bindings, one more per
    round, before `L0().ping()` resolves."""
    files = {"pkg/__init__.py": ""}
    for i in range(6):
        files[f"pkg/a{i}/__init__.py"] = files[f"pkg/b{i}/__init__.py"] = ""
        files[f"pkg/b{i}/m.py"] = f"class L{i}:\n    def ping(self):\n        pass\n"
        files[f"pkg/a{i}/m.py"] = (
            f"from pkg.a{i + 1}.m import L{i + 1}\n\n\nclass L{i}(L{i + 1}):\n    pass\n" if i < 5
            else "class L5:\n    def ping(self):\n        pass\n")
    files["pkg/use.py"] = "from pkg.a0.m import L0\n\n\ndef run():\n    return L0().ping()\n"
    parser = CodeFileParser()

    async def put(graph: Graph, path: str, source: str) -> None:
        record = f"rec-{path}"
        container = parser.parse_to_blocks(source.encode(), path.rsplit("/", 1)[-1], path, "python")
        graph.code_files[record] = path
        graph.blobs[record] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=ORG_ID, record_id=record, record_group_id=GROUP_ID,
                                           connector_id="conn1", language="python", file_path=path),
            block_containers=container,
        )

    async def full() -> Graph:
        graph = Graph()
        for path, source in files.items():
            await put(graph, path, source)
        await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                     facts_source=graph)
        return graph

    graph = await full()
    files["pkg/use.py"] += "\n\ndef other():\n    return L0().ping()\n"
    await put(graph, "pkg/use.py", files["pkg/use.py"])
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
                                 touched_record_ids={"rec-pkg/use.py"}, facts_source=graph)
    expected = await full()
    want = code_edges(expected)
    assert any("a5/m.py::method:L5.ping" in line for line in describe(expected, want)), "the full build links the call"
    assert code_edges(graph) == want, "\n".join(describe(graph, code_edges(graph) ^ want))


async def _keeps_edges_without(files: dict[str, str], unreadable: str, touched: str, marker: str) -> None:
    """Build in full, make `unreadable`'s facts unreadable, rebuild `touched` alone: the
    edges from the records that read the unreadable file stay as they were."""
    parser = CodeFileParser()

    async def put(graph: Graph, path: str, source: str) -> None:
        record = f"rec-{path}"
        container = parser.parse_to_blocks(source.encode(), path.rsplit("/", 1)[-1], path, "python")
        graph.code_files[record] = path
        graph.blobs[record] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=ORG_ID, record_id=record, record_group_id=GROUP_ID,
                                           connector_id="conn1", language="python", file_path=path),
            block_containers=container,
        )

    graph = Graph()
    for path, source in files.items():
        await put(graph, path, source)
    await build_code_graph_edges(graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID, facts_source=graph)
    before = set(describe(graph, code_edges(graph)))
    assert marker in before, "the full build makes the edge"
    del graph.blobs[f"rec-{unreadable}"]
    await put(graph, touched, files[touched])
    await asyncio.wait_for(build_code_graph_edges(
        graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
        touched_record_ids={f"rec-{touched}"}, facts_source=graph), timeout=20)
    after = set(describe(graph, code_edges(graph)))
    assert before == after, "\n".join(sorted(before ^ after))


async def test_a_file_reresolved_in_a_later_round_keeps_its_edges_when_a_type_table_cannot_be_read() -> None:
    """`use.py` reads `svc.py`'s bindings, so a second round re-resolves `svc.py`, which
    reads the type table of `x.py` that cannot be read. `svc.py` keeps its edges."""
    await _keeps_edges_without({
        "pkg/__init__.py": "",
        "pkg/base.py": "class Base:\n    def ping(self):\n        pass\n",
        "pkg/x.py": "class Y:\n    def go(self):\n        pass\n\n\nthing = Y()\n",
        # A second `go`, so only x.py's type table says which one `thing.go()` is.
        "pkg/z.py": "class Z:\n    def go(self):\n        pass\n",
        "pkg/svc.py": ("from pkg.base import Base\nfrom pkg.x import thing\n\n\nclass Service(Base):\n"
                       "    def run(self):\n        return thing.go()\n"),
        "pkg/use.py": ("from pkg.svc import Service\nfrom pkg.x import thing\n\n\ndef run():\n"
                       "    Service().ping()\n    return thing.go()\n"),
    }, unreadable="pkg/x.py", touched="pkg/use.py",
        marker="pkg/svc.py::method:Service.run -CALLS-> pkg/x.py::method:Y.go")


async def test_a_class_keeps_its_overrides_when_its_base_file_cannot_be_read() -> None:
    """`C.run` overrides `A.run` through `B`, whose file cannot be read: the override is
    found by walking `B`'s bases, so `c.py` keeps the edge rather than losing it."""
    await _keeps_edges_without({
        "pkg/__init__.py": "",
        "pkg/a.py": "class A:\n    def run(self):\n        pass\n",
        # A second `A`, so only b.py's import says which base `B` has.
        "pkg/other.py": "class A:\n    def run(self):\n        pass\n",
        "pkg/b.py": "from pkg.a import A\n\n\nclass B(A):\n    pass\n",
        "pkg/c.py": "from pkg.b import B\n\n\nclass C(B):\n    def run(self):\n        pass\n",
    }, unreadable="pkg/b.py", touched="pkg/c.py",
        marker="pkg/c.py::method:C.run -OVERRIDES-> pkg/a.py::method:A.run")
