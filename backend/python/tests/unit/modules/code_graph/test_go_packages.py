"""Go calls resolve through package imports and receiver types (B1 client-go)."""
from collections.abc import Awaitable, Callable

import pytest

from app.config.constants.arangodb import RecordRelations

from .test_import_binding import _build, _targets

CALLS = RecordRelations.CALLS.value
MOD = b"module example.com/app\n"


@pytest.fixture
def go_repo(graph, index_file) -> Callable[[dict[str, bytes]], Awaitable[None]]:
    async def _repo(files: dict[str, bytes]) -> None:
        graph.code_files["gomod"] = "go.mod"
        for i, (path, source) in enumerate(files.items()):
            await index_file(f"r{i}", path, source, "go")
    return _repo


@pytest.mark.asyncio
async def test_a_package_qualified_call_resolves_through_the_import(graph, go_repo) -> None:
    await go_repo({
        "cache/memory/a.go": b"package memory\n\nfunc New() int { return 1 }\n",
        "cache/disk/b.go": b'package disk\n\nimport "example.com/app/cache/memory"\n\n'
                           b"func Open() int { return memory.New() }\n",
        # Same name elsewhere: without the import it would be ambiguous.
        "other/c.go": b"package other\n\nfunc New() int { return 2 }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "cache/disk/b.go") == {"cache/memory/a.go::function:New"}


@pytest.mark.asyncio
async def test_an_external_import_never_lands_on_a_repo_folder_with_its_tail(graph, go_repo) -> None:
    await go_repo({
        "typed/core/v1/pod.go": b"package v1\n\nfunc NewPod() int { return 1 }\n",
        "typed/core/v1/svc.go": b"package v1\n\nfunc NewSvc() int { return 1 }\n",
        "apps/a.go": b'package apps\n\nimport (\n\t"example.com/app/typed/core/v1"\n)\n\n'
                     b"func A() int { return v1.NewSvc() }\n",
        "apps/b.go": b'package apps\n\nimport (\n\tcorev1 "k8s.io/api/core/v1"\n)\n\n'
                     b"func B() int { return corev1.NewPod() }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "apps/a.go") == {"typed/core/v1/svc.go::function:NewSvc"}
    assert _targets(graph, CALLS, "apps/b.go") == set()


@pytest.mark.asyncio
async def test_a_bare_call_is_never_a_method_or_a_builtin(graph, go_repo) -> None:
    await go_repo({
        "jp/node.go": b"package jp\n\ntype List struct{}\n\nfunc (l *List) append(n int) {}\n",
        "app/main.go": b"package app\n\nfunc Run(xs []int) []int { return append(xs, 1) }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "app/main.go") == set()


@pytest.mark.asyncio
async def test_a_receiver_is_typed_by_its_own_method_not_the_file(graph, go_repo) -> None:
    # One file reuses `c` for two receiver types; the file-wide table drops it.
    await go_repo({
        "rest/c.go": b"package rest\n\ntype Client struct{}\ntype Config struct{}\n\n"
                     b"func (c *Client) Do() int { return 1 }\n"
                     b"func (c *Client) Get() int { return c.Do() }\n"
                     b"func (c *Config) Load() int { return c.Read() }\n"
                     b"func (c *Config) Read() int { return 2 }\n",
        # Another package's `Client`: an unqualified receiver type is this package's.
        "other/c.go": b"package other\n\ntype Client struct{}\n\nfunc (c *Client) Do() int { return 3 }\n",
    })
    await _build(graph)
    assert _targets(graph, CALLS, "rest/c.go") == {
        "rest/c.go::method:Client.Do", "rest/c.go::method:Config.Read",
    }
