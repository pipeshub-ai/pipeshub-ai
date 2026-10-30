"""A row's arrow is true exactly when expanding it lists something for the viewer.

The arrow reads the rule the expansion reads, so every shape that rule has is
covered: seeds below a gap (OPEN only), the declared scope, deleted and
other-org children, and a linked record with many hidden children before a
readable one. Record groups, folders and Apps answer with a constant (finding out
reads every hidden child, and a group's expansion also lists chain-tops), so
they are not compared; ``test_a_container_does_not_say_what_it_holds`` pins that.
"""

from __future__ import annotations

from typing import Any

import pytest

from .fixture_graph import (
    DRIVE,
    GROUP_G,
    ORG,
    USER_U,
    _principals,
    app,
    bt,
    ip,
    nr,
    perm,
    rec,
    rg,
    user_app,
)
from .loaders import arango_shape, load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

RGL = "RECORD_GROUP_LEVEL"
FOLDER = "application/vnd.folder"
HIDDEN_BEFORE_READABLE = 600  # more than one page of child ids


def _g(a: str, **k: Any) -> dict:  # noqa: ANN401
    return {"group_type": "DRIVE", "connector": DRIVE, "connectorId": a, **k}


def _r(a: str, **k: Any) -> dict:  # noqa: ANN401
    return {"connector_id": a, **k}


def _graph() -> tuple[list, list]:
    nodes, edges = _principals()
    nodes, edges = list(nodes), list(edges)

    # seed: a granted node below a gap. Below a seed only OPEN children open.
    a = "ae-seed"
    nodes += [app(a, "Seed", connector=DRIVE, app_group="G"), rec("ae-gap", "gap", **_r(a))]
    edges += [user_app(USER_U, a), nr(a, "ae-gap")]
    seeds = {  # seed -> (child, child rule, child inherits, child granted)
        "ae-s-strict": ("ae-k-strict", "STRICT", True, False),
        "ae-s-grantedstrict": ("ae-k-grantedstrict", "STRICT", True, True),
        "ae-s-open": ("ae-k-open", "OPEN", True, False),
        "ae-s-noinherit": ("ae-k-noinherit", "OPEN", False, False),
        "ae-s-restricted": ("ae-k-restricted", "RESTRICTED", True, False),
    }
    for seed, (kid, rule, inherits, granted) in seeds.items():
        nodes += [rec(seed, seed, **_r(a)), rec(kid, kid, rule=rule, **_r(a))]
        edges += [nr("ae-gap", seed), perm(USER_U, seed), nr(seed, kid)]
        edges += [ip(kid, seed)] if inherits else []
        edges += [perm(USER_U, kid)] if granted else []

    # declared scope: a granted RECORD_GROUP_LEVEL group; its nested group and the records
    # belonging to it open without inheritance. A Jira-like issue with a sub-task.
    a = "ae-decl"
    nodes += [
        app(a, "Decl", connector=DRIVE, app_group="G"),
        rg("ae-dec", "dec", permissionModel=RGL, **_g(a)), rg("ae-n1", "n1", **_g(a)),
        rec("ae-in", "in", **_r(a)), rec("ae-out", "out", **_r(a)),
        rec("ae-issue", "issue", **_r(a)), rec("ae-sub", "sub", **_r(a)),
    ]
    edges += [
        user_app(USER_U, a), nr(a, "ae-dec"), perm(GROUP_G, "ae-dec", grant_type="GROUP"),
        nr("ae-dec", "ae-n1"), nr("ae-n1", "ae-in"), bt("ae-in", "ae-n1"), nr("ae-n1", "ae-out"),
        bt("ae-issue", "ae-dec"), bt("ae-sub", "ae-dec"), nr("ae-issue", "ae-sub"),
    ]

    # mixed children under an inheriting tree: deleted, hidden, other org, placeholder, readable.
    a = "ae-mix"
    nodes += [app(a, "Mix", connector=DRIVE, app_group="G"), rg("ae-mg", "g", **_g(a))]
    edges += [user_app(USER_U, a), nr(a, "ae-mg"), ip("ae-mg", a)]
    shapes = {
        "alldeleted": [("d1", {"isDeleted": True}, True)],
        "deletedhidden": [("d2", {"isDeleted": True}, True), ("h2", {}, False)],
        "deletedreadable": [("d3", {"isDeleted": True}, True), ("r3", {}, True)],
        "otherorg": [("x4", {"orgId": "org-2"}, True)],
        "placeholder": [("p5", {"isPlaceholder": True}, True)],
        "restricted": [("q6", {"rule": "RESTRICTED"}, True)],
        "leaf": [],
    }
    for name, kids in shapes.items():
        folder = f"ae-f-{name}"
        nodes.append(rec(folder, name, **_r(a)))
        edges += [nr("ae-mg", folder), ip(folder, "ae-mg")]
        for kid, props, inherits in kids:
            nodes.append(rec(f"ae-{name}-{kid}", kid, **props, **_r(a)))
            edges += [nr(folder, f"ae-{name}-{kid}")] + ([ip(f"ae-{name}-{kid}", folder)] if inherits else [])

    # linked records: many hidden children sort before the few readable ones.
    a = "ae-link"
    nodes += [app(a, "Link", connector=DRIVE, app_group="G"), rg("ae-lg", "g", **_g(a)), rec("ae-src", "src", **_r(a))]
    edges += [user_app(USER_U, a), nr(a, "ae-lg"), ip("ae-lg", a), nr("ae-lg", "ae-src"), ip("ae-src", "ae-lg")]
    for tag, hidden, readable in (("late", HIDDEN_BEFORE_READABLE, 2), ("none", HIDDEN_BEFORE_READABLE, 0)):
        target = f"ae-t-{tag}"
        nodes.append(rec(target, target, **_r(a)))
        edges += [nr("ae-lg", target), ip(target, "ae-lg")]
        for i in range(hidden):
            nodes.append(rec(f"{target}-a{i:04d}", "h", **_r(a)))
            edges.append(nr(target, f"{target}-a{i:04d}"))
        for i in range(readable):
            nodes.append(rec(f"{target}-z{i}", "v", **_r(a)))
            edges += [nr(target, f"{target}-z{i}"), ip(f"{target}-z{i}", target)]

    # folders: the arrow is a constant, so it cannot depend on what is hidden inside.
    a = "ae-big"
    nodes += [app(a, "Big", connector=DRIVE, app_group="G"), rg("ae-bg", "g", **_g(a))]
    edges += [user_app(USER_U, a), nr(a, "ae-bg"), ip("ae-bg", a)]
    for name, readable, hidden in (("hidden", 0, 3), ("empty", 0, 0), ("open", 1, 0)):
        folder = f"ae-fo-{name}"
        nodes.append(rec(folder, name, mimeType=FOLDER, **_r(a)))
        edges += [nr("ae-bg", folder), ip(folder, "ae-bg")]
        for i in range(hidden):
            nodes.append(rec(f"{folder}-h{i}", "h", **_r(a)))
            edges.append(nr(folder, f"{folder}-h{i}"))
        for i in range(readable):
            nodes.append(rec(f"{folder}-v{i}", "v", **_r(a)))
            edges += [nr(folder, f"{folder}-v{i}"), ip(f"{folder}-v{i}", folder)]
    return nodes, edges


APPS = ("ae-seed", "ae-decl", "ae-mix", "ae-link", "ae-big")


@pytest.fixture(scope="module")
async def arrows_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> None:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))
    for target in ("ae-t-late", "ae-t-none"):
        await neo4j_provider.client.execute_query(
            "MATCH (a:Record {id: 'ae-src'}), (b:Record {id: $b}) "
            "CREATE (a)-[:RECORD_LINK {relationshipType: 'LINKED_TO'}]->(b)", parameters={"b": target},
        )
        await arango_provider.http_client.execute_aql(
            "INSERT {_from: 'records/ae-src', _to: CONCAT('records/', @b), relationshipType: 'LINKED_TO'} "
            "INTO recordLinks", {"b": target},
        )


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def _browse(provider: Any, app_id: str, start_id: str, start_type: str, **kw: Any) -> list[dict]:  # noqa: ANN401
    access = await provider.get_knowledge_hub_access_v3(user_key=USER_U, org_id=ORG)
    page = await provider.get_knowledge_hub_connector_page_v3(
        app_id=app_id, org_id=ORG, grantee_ids=access["grantee_ids"], gated_app_ids=access["gated_app_ids"],
        grants_by_connector=access["by_connector"], limit=500, flatten=kw.pop("flatten", False),
        sort_field="name", sort_dir="ASC", include_total=True, start_id=start_id, start_type=start_type, **kw,
    )
    return page["rows"]


def _start_type(row: dict) -> str:
    return {"recordGroup": "recordGroup", "folder": "folder"}.get(row["nodeType"], "record")


async def _tree(provider: Any, app_id: str) -> dict[str, tuple[dict, list[str]]]:  # noqa: ANN401
    """Every node reachable by browsing the App -> (its row, the ids expanding it lists)."""
    seen: dict[str, tuple[dict, list[str]]] = {}
    queue: list[tuple[dict | None, str, str]] = [(None, app_id, "app")]
    while queue:
        row, node_id, start_type = queue.pop(0)
        if node_id in seen:
            continue
        children = await _browse(provider, app_id, node_id, start_type)
        seen[node_id] = (row, [c["id"] for c in children])
        queue += [(c, c["id"], _start_type(c)) for c in children]
    return seen


@pytest.mark.parametrize("app_id", APPS)
async def test_the_arrow_is_the_expansion(arrows_graph, provider, app_id) -> None:
    tree = await _tree(provider, app_id)
    wrong = {
        node_id: (row["hasChildren"], bool(listed))
        for node_id, (row, listed) in tree.items()
        if row is not None and row["nodeType"] == "record" and row["hasChildren"] != bool(listed)
    }

    assert wrong == {}


@pytest.mark.parametrize("app_id", APPS)
async def test_the_sidebar_keeps_containers_and_records_with_children(arrows_graph, provider, app_id) -> None:
    tree = await _tree(provider, app_id)
    for parent_id, (_, kids) in tree.items():
        start_type = "app" if parent_id == app_id else _start_type(tree[parent_id][0])
        kept = {r["id"] for r in await _browse(provider, app_id, parent_id, start_type, filters={"only_containers": True})}
        want = {k for k in kids if tree[k][0]["nodeType"] in ("recordGroup", "folder") or tree[k][1]}

        assert kept == want, parent_id


async def test_the_flatten_never_shows_an_arrow_for_nothing(arrows_graph, provider) -> None:
    for app_id in APPS:
        tree = await _tree(provider, app_id)
        for row in await _browse(provider, app_id, app_id, "app", flatten=True):
            if row["nodeType"] == "record" and row["hasChildren"]:
                assert tree[row["id"]][1], row["id"]


async def test_a_connector_root_does_not_say_whether_it_has_groups(arrows_graph, provider) -> None:
    access = await provider.get_knowledge_hub_access_v3(user_key=USER_U, org_id=ORG)
    envelope = await provider.get_knowledge_hub_root_nodes_v2(
        user_key=USER_U, org_id=ORG, user_app_ids=access["gated_app_ids"], limit=100,
    )
    rows = envelope["partitions"][0]["rows"] if "partitions" in envelope else envelope["rows"]
    arrows = {r["id"]: r["hasChildren"] for r in rows if r["id"] in APPS}

    assert set(arrows.values()) == {True}


async def test_linked_records_look_past_hidden_children(arrows_graph, provider) -> None:
    linked = await provider.get_linked_records("ae-src", ORG, USER_U, ["LINKED_TO"])

    assert {i["id"]: i["hasChildren"] for i in linked} == {"ae-t-late": True, "ae-t-none": False}


async def test_a_container_does_not_say_what_it_holds(arrows_graph, provider) -> None:
    rows = {r["id"]: r for r in await _browse(provider, "ae-big", "ae-bg", "recordGroup")}

    # One folder holds only hidden children, one holds nothing, one a readable child.
    assert {rows[f]["hasChildren"] for f in ("ae-fo-hidden", "ae-fo-empty", "ae-fo-open")} == {True}
