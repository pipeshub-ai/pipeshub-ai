"""The explanation walk's own behaviour over hand-built nodes, with no store."""

from __future__ import annotations

import resource
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from app.services.permissions import explain as ex
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.ports import StampNode


def _facts(nodes: list[StampNode]) -> ex._Facts:
    return ex._Facts({n.id: n for n in nodes}, frozenset(), frozenset({"A"}), "A", False)


def _n(i: str, kind: str, parents: tuple[str, ...] = (), inherits: tuple[str, ...] = (), **kw: object) -> StampNode:
    return StampNode(i, kind, "A", parents, frozenset(inherits), **kw)  # type: ignore[arg-type]


#: Two ancestors at the same depth that fail differently: `a` is restricted, `b` is deleted.
TWO_FAILURES = [
    _n("A", "app"),
    _n("r", "record", ("a", "b"), ("a", "b")),
    _n("a", "group", ("A",), rule="OPEN"),
    _n("b", "group", ("A",), ("A",), deleted=True),
]
#: `a` (the lowest id) dies at a declared-readers group; `b` fails its own hop, which is the reason named.
TWO_PATHS = [
    _n("A", "app"),
    _n("r", "record", ("a", "b"), ("a", "b")),
    _n("a", "group", ("g",), ("g",)),
    _n("g", "group", ("A",), ("A",), group_level=True),
    _n("b", "group", ("A",), rule="OPEN"),
]


def test_the_code_does_not_depend_on_the_hash_order_of_the_walk() -> None:
    script = textwrap.dedent("""
        from tests.permissions.test_explain_walk import TWO_FAILURES, _facts
        from app.services.permissions import explain as ex
        f = _facts(TWO_FAILURES)
        w = ex._walk(f, f.nodes["r"], f.nodes["A"])
        print(w.code, w.node_id)
    """)
    seen = {
        subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True,
                       env={"PYTHONHASHSEED": str(seed), "PYTHONPATH": ":".join(sys.path)}).stdout.strip()
        for seed in range(12)
    }
    assert seen == {f"{ex.RESTRICTED_CONTAINER} a"}


def test_the_path_runs_through_the_container_the_reason_names() -> None:
    f = _facts(TWO_PATHS)
    w = ex._walk(f, f.nodes["r"], f.nodes["A"])
    assert (w.code, w.node_id) == (ex.RESTRICTED_CONTAINER, "b")
    assert "b" not in ex.ExplainService._chain(f, f.nodes["r"])
    assert ex.ExplainService._chain(f, f.nodes["r"], "b") == ["A", "b"]
    assert ex.ExplainService._chain(f, f.nodes["r"], None) == ["A", "g", "a"]


class _Counting(dict):  # type: ignore[type-arg]
    reads = 0

    def __getitem__(self, key: str) -> StampNode:
        _Counting.reads += 1
        return super().__getitem__(key)


def test_a_wide_hierarchy_with_shared_ancestors_is_read_once_per_node() -> None:
    layers, width = 25, 2
    nodes = [_n("A", "app")]
    for layer in range(layers):
        above = ("A",) if layer == 0 else tuple(f"g{layer - 1}_{w}" for w in range(width))
        nodes += [_n(f"g{layer}_{w}", "group", above, above) for w in range(width)]
    top = tuple(f"g{layers - 1}_{w}" for w in range(width))
    nodes.append(_n("r", "record", top, top))
    f = ex._Facts(_Counting({n.id: n for n in nodes}), frozenset(), frozenset({"A"}), "A", False)
    _Counting.reads = 0

    chain = ex.ExplainService._chain(f, f.nodes["r"], "g0_1")

    assert "g0_1" in chain and chain[0] == "A"
    assert _Counting.reads <= 4 * len(nodes), _Counting.reads


def test_shared_children_are_queued_once_per_layer() -> None:
    """Thirty layers of two nodes that each have both nodes above them: queued twice per layer the frontier doubles each layer
    (a billion entries), so the walk runs in a process whose memory is capped and must finish."""
    script = textwrap.dedent("""
        from tests.permissions.test_explain_walk import _n, ex
        layers = 30
        nodes = [_n("A", "app")]
        for k in range(layers):
            above = ("A",) if k == 0 else (f"g{k-1}_0", f"g{k-1}_1")
            nodes += [_n(f"g{k}_{w}", "group", above, above) for w in range(2)]
        nodes.append(_n("r", "record", (f"g{layers-1}_0", f"g{layers-1}_1")))
        f = ex._Facts({n.id: n for n in nodes}, frozenset(), frozenset({"A"}), "A", False)
        print(len(ex.ExplainService._reaching(f, f.nodes["r"], "g0_1")))
    """)
    cap = 2 * 1024**3
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60,
        env={"PYTHONPATH": ":".join(sys.path)},
        preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_AS, (cap, cap)),
    )
    assert done.returncode == 0, done.stderr[-300:]
    assert done.stdout.strip() == "60"  # g0_1, the 58 nodes of the layers below it, and the record


class _Provider:
    def __init__(self, document: dict | None, users: dict, fail: str | None = None) -> None:
        self.document, self.users, self.fail, self.kwargs = document, users, fail, []

    async def get_document(self, key: str, collection: str, **kw: object) -> dict | None:
        self.kwargs.append(("get_document", kw))
        if self.fail == "document":
            raise ConnectionError("down")
        return self.document

    async def get_users_by_emails(self, emails: list[str], **kw: object) -> dict:
        self.kwargs.append(("get_users_by_emails", kw))
        if self.fail == "users":
            raise ConnectionError("down")
        return self.users


def _service(provider: _Provider) -> ex.ExplainService:
    service = ex.ExplainService.__new__(ex.ExplainService)
    service._provider = provider  # type: ignore[assignment]
    return service


@pytest.mark.parametrize("fail", ["document", "users"])
async def test_a_failed_read_of_the_person_is_unavailable_not_a_person_of_another_organisation(fail: str) -> None:
    provider = _Provider({"email": "d@x"}, {}, fail)
    with pytest.raises(ViewerScopesUnavailable):
        await _service(provider)._in_org("org", "k-dana")


async def test_a_successful_read_decides_membership_and_asks_for_failures_to_raise() -> None:
    member = _Provider({"email": "d@x"}, {"d@x": SimpleNamespace(id="k-dana")})
    stranger = _Provider({"email": "d@x"}, {})
    unknown = _Provider(None, {})
    assert await _service(member)._in_org("org", "k-dana") is True
    assert await _service(stranger)._in_org("org", "k-dana") is False
    assert await _service(unknown)._in_org("org", "k-dana") is False
    assert member.kwargs == [("get_document", {"raise_on_error": True}), ("get_users_by_emails", {"org_id": "org", "raise_on_error": True})]


async def test_nothing_to_name_asks_nothing_about_the_asking_admin() -> None:
    def refuse(*_: object) -> None:
        raise AssertionError("the asking admin was looked up")

    service = ex.ExplainService.__new__(ex.ExplainService)
    service._principals = SimpleNamespace(principal=refuse, readable=refuse)  # type: ignore[assignment]
    assert await service._names("org", "admin", _facts([]), []) == {}


@pytest.mark.parametrize("ids", [["a"]])
async def test_something_to_name_does_ask(ids: list[str]) -> None:
    async def principal(*_: object) -> str:
        raise RuntimeError("asked")

    service = ex.ExplainService.__new__(ex.ExplainService)
    service._principals = SimpleNamespace(principal=principal)  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="asked"):
        await service._names("org", "admin", _facts(TWO_PATHS), ids)
