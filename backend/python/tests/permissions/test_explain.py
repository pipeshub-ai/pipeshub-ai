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


@pytest.mark.integration
@pytest.mark.timeout(3000)
async def test_a_person_of_another_organisation_is_not_found_while_one_of_this_organisation_is_explained(graph_layers: list[GraphCheckLayer]) -> None:
    other = WorldBuilder()
    other.principal("eve")
    other.node(Node("B", "app", app_type="DRIVE"))
    other.gate("eve", "B")
    for layer in graph_layers:
        _, ns = await _load(layer)
        foreign = other.build()
        await layer.preload([foreign])
        theirs = await layer._ns(foreign)
        assert theirs.org != ns.org
        service = ExplainService(layer._provider)  # type: ignore[arg-type]
        with pytest.raises(ExplainNotFound):
            await service.explain(ns.org, theirs.id("eve"), ns.id("openrec"), asker_key=ns.id("boss"))
        assert (await service.explain(ns.org, ns.id("dana"), ns.id("openrec"), asker_key=ns.id("boss"))).admitted


def _vault_world() -> World:
    """`salary-sheet` is under the `vault` folder, which only `hrer` may open. `admin` is gated on the connector and holds no
    grant on the vault: it cannot read the folder or the record. `payroll-secret` is what a leak would show."""
    b = WorldBuilder()
    for u in ("admin", "hrer"):
        b.principal(u)
        b.gate(u, "A")
    b.node(Node("A", "app", app_type="DRIVE"))
    b.node(Node("vault", "group", connector="A", rule="RESTRICTED"))
    b.node(Node("salary-sheet", "record", connector="A", belongs=("vault",), vrid="payroll-secret"))
    b.parent("A", "vault")
    b.parent("vault", "salary-sheet")
    b.inherit("vault", "A")
    b.inherit("salary-sheet", "vault")
    b.grant("hrer", "vault")
    return b.build()


@pytest.mark.integration
@pytest.mark.timeout(3000)
async def test_an_admin_who_cannot_read_a_record_learns_the_verdict_and_nothing_of_it_and_gains_no_access(graph_layers: list[GraphCheckLayer]) -> None:
    import json

    world = _vault_world()
    for layer in graph_layers:
        await layer.preload([world])
        ns = await layer._ns(world)
        provider = layer._provider
        titles = []
        for key, collection in ((ns.id("vault"), "recordGroups"), (ns.id("salary-sheet"), "records")):
            doc = await provider.get_document(key, collection)  # type: ignore[attr-defined]
            titles += [str(v) for k, v in doc.items() if k in ("groupName", "recordName", "name", "virtualRecordId", "externalRecordId")]
        assert {"vault", "salary-sheet"} <= set(titles)
        before = await layer.admitted(world, "admin", ["vault", "salary-sheet"])
        assert before == set(), layer.name  # the admin cannot read either

        service = ExplainService(provider)  # type: ignore[arg-type]
        got = await service.explain(ns.org, ns.id("hrer"), ns.id("salary-sheet"), asker_key=ns.id("admin"))
        wire = json.dumps(got.as_dict())

        assert got.admitted is True  # the person can read it: the verdict is given
        assert json.loads(wire)["admitted"] is True
        assert set(json.loads(wire)) == {"admitted", "code", "container", "path", "reference"}
        for title in (*titles, ns.id("vault"), ns.id("salary-sheet"), ns.prefix):
            assert title not in wire, (layer.name, title)  # no title, key, virtual record id, or any id of the record or its folder
        assert not any(w in wire.lower() for w in ("http", "url", "link", "content"))
        assert [c["restricted"] for c in json.loads(wire)["path"]] == [False, True]  # the connector is named, the vault is "a restricted area"
        assert json.loads(wire)["path"][1]["name"] is None
        # asking about it granted nothing
        assert await layer.admitted(world, "admin", ["vault", "salary-sheet"]) == before, layer.name

        denied = await service.explain(ns.org, ns.id("admin"), ns.id("salary-sheet"), asker_key=ns.id("admin"))
        wire = json.dumps(denied.as_dict())
        assert denied.admitted is False and "vault" not in wire and "salary-sheet" not in wire
        assert json.loads(wire)["container"] == {"name": None, "restricted": True}
