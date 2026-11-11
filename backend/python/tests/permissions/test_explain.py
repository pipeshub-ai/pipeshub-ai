"""`ExplainService` over Neo4j and ArangoDB, against the product oracle.

Every scenario's final world is loaded into each store; for every viewer x node pair the explanation's verdict must equal
`check_access` and its reason code must be the one the oracle's reason maps to (`REASON_MAP`, one table). Then the branches the
oracle cannot show: naming only what the asking admin can read, a mismatch, a pending rewrite, an unknown person or record.
The mapping's coverage test needs no store; the rest need the throwaway stores of `tests/integration/graph_permissions` (see `conftest.py`).
"""

from __future__ import annotations

import ast
import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.services.permissions import explain as ex
from app.services.permissions.explain import ExplainNotFound, ExplainService

from .graph import Namespace, compile_world
from .model import Node, World, WorldBuilder
from .runner import OracleLayer, targets
from .scenario import load_all

if TYPE_CHECKING:
    from .layers import GraphCheckLayer

NOT_FOUND = "not_found"
#: A hop that fails on the record itself is its own restriction; on an ancestor it is that container's.
ITEM_OR_CONTAINER = "item_or_container"
#: Oracle reason (a glob; first match wins) -> explanation code.
REASON_MAP: list[tuple[str, str]] = [
    ("R1:no-viewer", NOT_FOUND),
    ("R11:not-a-readable-node", NOT_FOUND),
    ("R11:other-org", NOT_FOUND),
    ("R11:deleted", ex.DELETED_AT_SOURCE),
    ("R11:placeholder", ex.SYNCING),
    ("R4:app", ex.GRANTED),
    ("R12:*", ex.GRANTED),
    ("R8:declared", ex.GRANTED),
    ("R7:walk", ex.GRANTED),
    ("R9:seed", ex.GRANTED),
    ("R10:below-seed", ex.GRANTED),
    ("R13:unattached-grant", ex.GRANTED),
    ("R13:unattached-no-grant", ex.NO_GRANT),
    ("R4:not-gated", ex.NOT_LINKED),
    ("R6:deleted@*", ex.PARENT_NOT_READABLE),
    ("R6:unknown-rule@*", ex.SCHEME_UNREADABLE),
    ("R6:*-no-*@*", ITEM_OR_CONTAINER),
    ("R6:RESTRICTED-*@*", ITEM_OR_CONTAINER),
    ("R7:depth-cap", ex.UNEXPLAINED),
    ("R7:rgl-parent-skipped*", ex.RESTRICTED_CONTAINER),
    ("no-arm", ex.NO_GRANT),
]


def expected_code(reason: str, node: str) -> str:
    for pattern, code in REASON_MAP:
        if fnmatch.fnmatchcase(reason, pattern):
            if code == ITEM_OR_CONTAINER:
                return ex.ITEM_RESTRICTED if reason.endswith(f"@{node}") else ex.RESTRICTED_CONTAINER
            return code
    raise AssertionError(f"oracle reason {reason!r} has no explanation code")


def _oracle_reasons() -> set[str]:
    """Every reason string `oracle.py` can produce (a format field shows as `X`)."""
    tree = ast.parse((Path(__file__).parent / "oracle.py").read_text())
    found: set[str] = set()
    pieces = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
    for n in ast.walk(tree):
        if id(n) in pieces:
            continue
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            text = n.value
        elif isinstance(n, ast.JoinedStr):
            text = "".join(p.value if isinstance(p, ast.Constant) else "X" for p in n.values)
        else:
            continue
        if text == "no-arm" or (text[:1] == "R" and text[1:2].isdigit() and ":" in text and not text.endswith(":") and " " not in text):
            found.add(text)
    return found


def test_every_oracle_reason_is_mapped_to_a_code_the_service_can_return() -> None:
    reasons = _oracle_reasons()
    assert {"R7:depth-cap", "R4:not-gated", "no-arm"} <= reasons
    for reason in reasons:
        code = expected_code(reason, "none")
        assert code in ex.CODES or code == NOT_FOUND, reason
    # no pattern is dead: each one matches something the oracle says
    for pattern, _ in REASON_MAP:
        assert any(fnmatch.fnmatchcase(r, pattern) for r in reasons), pattern


@pytest.mark.integration
@pytest.mark.timeout(3000)
async def test_the_verdict_equals_check_access_and_the_code_maps_from_the_oracle_reason(graph_layers: list[GraphCheckLayer]) -> None:
    scenarios = load_all()
    worlds = list(dict.fromkeys(s.final_world() for s in scenarios))
    oracle = OracleLayer()
    for layer in graph_layers:
        service = ExplainService(layer._provider)  # type: ignore[arg-type]
        checked = 0
        for start in range(0, len(worlds), 25):
            chunk = worlds[start:start + 25]
            await layer.preload(chunk)
            for world in chunk:
                ns = await layer._ns(world)
                if len(layer.shape(compile_world(world, ns.prefix))["nodes"]) != \
                        len(compile_world(world, ns.prefix)["nodes"]):
                    continue  # the store cannot hold a node of this world (an unknown rule on Arango)
                nodes = targets(world)
                for viewer in world.users():
                    truth = oracle.explain(world, viewer, nodes)
                    admitted = await layer.admitted(world, viewer, nodes)
                    for node in nodes:
                        want = expected_code(truth[node].reason, node)
                        try:
                            got = await service.explain(ns.org, ns.id(viewer), ns.id(node), asker_key=ns.id(viewer))
                        except ExplainNotFound:
                            assert want == NOT_FOUND, (layer.name, viewer, node, truth[node].reason)
                            continue
                        where = (layer.name, viewer, node, truth[node].reason, got.code)
                        assert got.admitted == (node in admitted), where
                        assert got.code == want, where
                        checked += 1
            await layer.release(chunk)
        assert checked > 2000, (layer.name, checked)
        # The only disagreement is an admit through a node of another organisation, which the walk (one organisation's graph) cannot see.
        assert all(m.graph_admitted and not m.walk_admitted for m in service.mismatches), (layer.name, service.export_mismatches()[:5])


def _world() -> World:
    """`hr` is limited to its own reader; `pay` sits under it. `dana` reads neither, `boss` reads `hr` and not `pay`'s sibling."""
    b = WorldBuilder()
    for u in ("dana", "boss", "hrer"):
        b.principal(u)
    b.node(Node("A", "app", app_type="DRIVE"))
    b.node(Node("hr", "group", connector="A", rule="RESTRICTED", parents=()))
    b.node(Node("pub", "group", connector="A"))
    b.node(Node("doc", "record", connector="A", belongs=("hr",)))
    b.node(Node("openrec", "record", connector="A", belongs=("pub",)))
    b.parent("A", "hr")
    b.parent("A", "pub")
    b.parent("hr", "doc")
    b.parent("pub", "openrec")
    b.inherit("hr", "A")
    b.inherit("doc", "hr")
    b.inherit("pub", "A")
    b.inherit("openrec", "pub")
    for u in ("dana", "boss", "hrer"):
        b.gate(u, "A")
    b.grant("hrer", "hr")
    b.grant("boss", "hr")
    return b.build()


async def _load(layer: GraphCheckLayer) -> tuple[World, Namespace]:
    w = _world()
    await layer.preload([w])
    return w, await layer._ns(w)


@pytest.mark.integration
@pytest.mark.timeout(3000)
async def test_a_container_the_asking_admin_cannot_read_is_a_restricted_area(graph_layers: list[GraphCheckLayer], monkeypatch) -> None:
    for layer in graph_layers:
        _, ns = await _load(layer)
        service = ExplainService(layer._provider)  # type: ignore[arg-type]
        # the asker holds the container: it is named; the asker does not: it is not
        for asker, named in (("boss", True), ("dana", False)):
            got = await service.explain(ns.org, ns.id("dana"), ns.id("doc"), asker_key=ns.id(asker))
            assert (got.admitted, got.code) == (False, ex.RESTRICTED_CONTAINER), layer.name
            assert got.container is not None and got.container.id in (ns.id("hr"), None)
            assert got.container.restricted is (not named), (layer.name, asker)
            assert all(c.name is None or named or c.id != ns.id("hr") for c in got.path)
            assert (got.container.name is None) is (not named)


@pytest.mark.integration
@pytest.mark.timeout(3000)
async def test_a_record_open_to_the_person_is_granted_and_an_unknown_person_or_record_is_not_found(graph_layers: list[GraphCheckLayer]) -> None:
    for layer in graph_layers:
        _, ns = await _load(layer)
        service = ExplainService(layer._provider)  # type: ignore[arg-type]
        got = await service.explain(ns.org, ns.id("dana"), ns.id("openrec"), asker_key=ns.id("boss"))
        assert (got.admitted, got.code) == (True, ex.GRANTED)
        assert len(got.reference) == 8
        for viewer, record in (("nobody", "openrec"), ("dana", "nothing")):
            with pytest.raises(ExplainNotFound):
                await service.explain(ns.org, ns.id(viewer), ns.id(record), asker_key=ns.id("boss"))
        with pytest.raises(ExplainNotFound):
            await service.explain(ns.org, "", ns.id("openrec"), asker_key=ns.id("boss"))
        with pytest.raises(ExplainNotFound):  # a record of another connector than the route's
            await service.explain(ns.org, ns.id("dana"), ns.id("openrec"), asker_key=ns.id("boss"), connector_id="other")
        assert (await service.explain(ns.org, ns.id("dana"), ns.id("openrec"), asker_key=ns.id("boss"), connector_id=ns.id("A"))).admitted


@pytest.mark.integration
@pytest.mark.timeout(3000)
async def test_a_walk_that_disagrees_with_the_graph_keeps_the_graphs_verdict_and_is_counted(graph_layers: list[GraphCheckLayer], monkeypatch) -> None:
    real = ex._walk
    for layer in graph_layers:
        _, ns = await _load(layer)
        service = ExplainService(layer._provider)  # type: ignore[arg-type]
        monkeypatch.setattr(ex, "_walk", lambda f, t, a: ex._Walk(True, ex.GRANTED))
        got = await service.explain(ns.org, ns.id("dana"), ns.id("doc"), asker_key=ns.id("boss"))
        monkeypatch.setattr(ex, "_walk", real)
        assert (got.admitted, got.code, got.container) == (False, ex.UNEXPLAINED, None), layer.name
        (m,) = service.export_mismatches()
        monkeypatch.setattr(ex, "_walk", lambda f, t, a: ex._Walk(False, ex.NO_GRANT))
        up = await service.explain(ns.org, ns.id("dana"), ns.id("openrec"), asker_key=ns.id("boss"))  # graph admits, walk denies
        monkeypatch.setattr(ex, "_walk", real)
        assert (up.admitted, up.code) == (True, ex.GRANTED)
        assert len(service.export_mismatches()) == 2
        assert (m.record_id, m.viewer_key, m.graph_admitted, m.walk_admitted, m.reference) == (ns.id("doc"), ns.id("dana"), False, True, got.reference)


@pytest.mark.integration
@pytest.mark.timeout(3000)
async def test_a_denial_under_a_pending_root_says_the_subtree_is_being_updated(graph_layers: list[GraphCheckLayer], monkeypatch) -> None:
    for layer in graph_layers:
        _, ns = await _load(layer)
        provider = layer._provider
        service = ExplainService(provider)  # type: ignore[arg-type]
        view = provider.permission_hierarchy_view()  # type: ignore[attr-defined]

        roots = {ns.id("A"): frozenset({ns.id("hr")})}

        async def pending(org_id: str, roots: dict = roots) -> dict:
            return roots

        monkeypatch.setattr(provider, "permission_hierarchy_view", lambda: type("V", (), {"pending_scopes": staticmethod(pending)})())
        got = await service.explain(ns.org, ns.id("dana"), ns.id("doc"), asker_key=ns.id("boss"))
        assert (got.admitted, got.code) == (False, ex.PENDING_REWRITE), layer.name
        other = await service.explain(ns.org, ns.id("dana"), ns.id("openrec"), asker_key=ns.id("boss"))
        assert other.code == ex.GRANTED  # a pending root never turns an admit into a denial
        monkeypatch.undo()
        assert view is not None
