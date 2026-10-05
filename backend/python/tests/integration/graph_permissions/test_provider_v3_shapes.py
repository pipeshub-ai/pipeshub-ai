"""Shapes and requests the other v3 suites do not cover, on both backends.

The graph is the scope-arms fixture plus one App per shape; every node carries
``connectorId`` = its App's id, as production does, so the declared and seed arms
run. Assertions are against known values, and Arango is held to Neo4j wherever
both should agree.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.localKB.handlers.kh_merge import key_for

from .fixture_graph import (
    DRIVE,
    KB,
    ORG,
    TS,
    USER_U,
    USER_V,
    _node,
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
from .test_provider_v3_scope_arms import APP, KB_APP, OTHER_ORG_NODE, PRUNE_GAP
from .test_provider_v3_scope_arms import _graph as _scope_arms_graph

pytestmark = pytest.mark.integration

BACKENDS = ["neo4j", "arango"]
RGL = "RECORD_GROUP_LEVEL"
FOLDER_MIME = "application/vnd.folder"
USER_W = "user-w"                      # gated into one App, holds no grant and no membership
PARENT_FIELDS = ("parentId", "parentName", "parentType", "parentIsInternal")
# Scope-arms groups that name another connector or none: fixture-only, the check
# fails them closed while the listing shows them (test_provider_v3_access_check).
NAMES_NO_GATED_APP = {"v3-foreign-dec", "v3-noconn-dec"}

ED, SW, HD, DP, HM, SO, KN, RS = (
    "ed-app", "sw-app", "hd-app", "dp-app", "hm-app", "so-app", "kn-kb", "rs-app",
)


def _group(app_id: str, **props: Any) -> dict:  # noqa: ANN401
    return {"group_type": "DRIVE", "connector": DRIVE, "connectorId": app_id, **props}


def _item(app_id: str, **props: Any) -> dict:  # noqa: ANN401
    return {"connector_id": app_id, **props}


def untyped(parent: str, child: str) -> dict:
    """A hierarchy-storage edge with no relationshipType: what moving links to their
    own edge leaves in place (``test_the_migration_moves_legacy_links_only``)."""
    return {"type": "NODE_RELATION", "from": parent, "to": child, "props": {"createdAtTimestamp": TS}}


# Written straight into the hierarchy storage after loading, as a link waiting
# for the split: the loader would route it to RECORD_LINK / recordLinks.
UNMIGRATED_LINKS = [("ed-anchor", "ed-linked", "BLOCKS")]


def legacy_edges() -> tuple[list, list]:
    """An untyped edge and an unmigrated link, each from a visible node
    to a child that inherits through it; and an untyped edge from a hidden
    channel onto a seed, which must not count as "beneath" the channel."""
    nodes = [
        app(ED, "Legacy edges", connector=DRIVE, app_group="Google Workspace"),
        rg("ed-g", "Legacy open group", **_group(ED)),
        rec("ed-anchor", "Legacy anchor", **_item(ED)),
        rec("ed-untyped", "Legacy below an untyped edge", **_item(ED)),
        rec("ed-untyped-child", "Legacy below the untyped child", **_item(ED)),
        rec("ed-linked", "Legacy behind an unmigrated link", **_item(ED)),
        rg("ed-hidden", "Legacy hidden channel", **_group(ED, hideChildren=True)),
        rec("ed-gap", "Legacy gap folder", **_item(ED)),
        rec("ed-seed", "Legacy seed", **_item(ED)),
    ]
    edges = [
        user_app(USER_U, ED),
        nr(ED, "ed-g"), ip("ed-g", ED),
        nr("ed-g", "ed-anchor"), ip("ed-anchor", "ed-g"), bt("ed-anchor", "ed-g"),
        untyped("ed-g", "ed-untyped"), ip("ed-untyped", "ed-g"), bt("ed-untyped", "ed-g"),
        nr("ed-untyped", "ed-untyped-child"), ip("ed-untyped-child", "ed-untyped"),
        bt("ed-untyped-child", "ed-g"),
        ip("ed-linked", "ed-anchor"), bt("ed-linked", "ed-g"),
        nr(ED, "ed-hidden"), ip("ed-hidden", ED),
        nr("ed-g", "ed-gap"), bt("ed-gap", "ed-g"),
        nr("ed-gap", "ed-seed"), perm(USER_U, "ed-seed"), bt("ed-seed", "ed-g"),
        untyped("ed-hidden", "ed-seed"),
    ]
    return nodes, edges


def shared_with_me() -> tuple[list, list]:
    """Two hierarchy parents, with production connector ids: sw-x is reachable through
    its drive folder (inherits) and through Shared with Me (granted); sw-y only
    through Shared with Me, its drive folder being closed."""
    nodes = [
        app(SW, "Drive, two parents", connector=DRIVE, app_group="Google Workspace"),
        rg("sw-drive", "Shared drive", **_group(SW)),
        rg("sw-inbox", "Shared with me", **_group(SW, isInternal=True)),
        rec("sw-folder", "Folder", mimeType=FOLDER_MIME, **_item(SW)),
        rec("sw-gapf", "Folder the user cannot open", mimeType=FOLDER_MIME, **_item(SW)),
        rec("sw-x", "File in both places", **_item(SW)),
        rec("sw-y", "File shared out of a closed folder", **_item(SW)),
    ]
    edges = [
        user_app(USER_U, SW),
        nr(SW, "sw-drive"), ip("sw-drive", SW),
        nr(SW, "sw-inbox"), perm(USER_U, "sw-inbox"),
        nr("sw-drive", "sw-folder"), ip("sw-folder", "sw-drive"), bt("sw-folder", "sw-drive"),
        nr("sw-drive", "sw-gapf"), bt("sw-gapf", "sw-drive"),
        nr("sw-folder", "sw-x"), ip("sw-x", "sw-folder"), nr("sw-inbox", "sw-x"),
        perm(USER_U, "sw-x"), bt("sw-x", "sw-drive"), bt("sw-x", "sw-inbox"),
        nr("sw-gapf", "sw-y"), nr("sw-inbox", "sw-y"), ip("sw-y", "sw-inbox"),
        perm(USER_U, "sw-y"), bt("sw-y", "sw-drive"), bt("sw-y", "sw-inbox"),
    ]
    return nodes, edges


HD_DEPTH = 25


def hidden_depth() -> tuple[list, list]:
    """A chain below a hideChildren channel, 25 deep, to catch a hidden-above
    probe bounded shorter than the 50-hop walks. hd-c20 and hd-c21 are granted:
    20 and 21 hops below the channel."""
    nodes = [
        app(HD, "Deep channel", connector=DRIVE, app_group="Google Workspace"),
        rg("hd-hidden", "Deep channel", **_group(HD, hideChildren=True)),
    ]
    edges = [user_app(USER_U, HD), nr(HD, "hd-hidden"), ip("hd-hidden", HD),
             perm(USER_U, "hd-c20"), perm(USER_U, "hd-c21")]
    parent = "hd-hidden"
    for k in range(1, HD_DEPTH + 1):
        node_id = f"hd-c{k}"
        nodes.append(rec(node_id, f"Reply {k:02d}", record_type="MESSAGE", **_item(HD)))
        edges += [nr(parent, node_id), ip(node_id, parent)]
        parent = node_id
    return nodes, edges


DP_CHAIN, DP_SEED_CHAIN = 50, 51


def depth_bound() -> tuple[list, list]:
    """The 50-hop bound: dp-k is k+1 hops below the App, so dp-49 is the deepest
    reachable; dp-s-k is k hops below the seed dp-s, so dp-s-50 is."""
    nodes = [
        app(DP, "Depth bound", connector=DRIVE, app_group="Google Workspace"),
        rg("dp-g", "Depth group", **_group(DP)),
        rec("dp-gap", "Depth gap", **_item(DP)),
        rec("dp-s", "Depth seed", **_item(DP)),
    ]
    edges = [user_app(USER_U, DP), nr(DP, "dp-g"), ip("dp-g", DP),
             nr("dp-g", "dp-gap"), nr("dp-gap", "dp-s"), perm(USER_U, "dp-s")]
    for prefix, top, length in (("dp", "dp-g", DP_CHAIN), ("dp-s", "dp-s", DP_SEED_CHAIN)):
        parent = top
        for k in range(1, length + 1):
            node_id = f"{prefix}-{k}"
            nodes.append(rec(node_id, f"{prefix} level {k:02d}", **_item(DP)))
            edges += [nr(parent, node_id), ip(node_id, parent)]
            parent = node_id
    return nodes, edges


def hidden_membership() -> tuple[list, list]:
    """hm-msg is granted and only BELONGS_TO a hidden channel (no hierarchy
    edge), so no hidden-above probe sees it. hm-both belongs to a hidden nested
    group and to the visible declaration above it; hm-only-hidden only to the
    hidden one."""
    nodes = [
        app(HM, "Hidden membership", connector=DRIVE, app_group="Google Workspace"),
        rg("hm-chan", "Hidden channel", **_group(HM, hideChildren=True)),
        rec("hm-msg", "Granted member of the hidden channel", record_type="MESSAGE", **_item(HM)),
        rec("hm-msg-nested", "Granted child of the hidden channel", record_type="MESSAGE", **_item(HM)),
        rg("hm-dec", "Declared, granted", **_group(HM, permissionModel=RGL)),
        rg("hm-nest-hidden", "Nested group that hides", **_group(HM, hideChildren=True)),
        rec("hm-both", "In the hidden group and the declaration", **_item(HM)),
        rec("hm-only-hidden", "Only in the hidden group", **_item(HM)),
    ]
    edges = [
        user_app(USER_U, HM),
        nr(HM, "hm-chan"), ip("hm-chan", HM),
        bt("hm-msg", "hm-chan"), perm(USER_U, "hm-msg"),
        nr("hm-chan", "hm-msg-nested"), bt("hm-msg-nested", "hm-chan"), perm(USER_U, "hm-msg-nested"),
        nr(HM, "hm-dec"), perm(USER_U, "hm-dec"),
        nr("hm-dec", "hm-nest-hidden"),
        nr("hm-nest-hidden", "hm-both"), bt("hm-both", "hm-nest-hidden"), bt("hm-both", "hm-dec"),
        nr("hm-nest-hidden", "hm-only-hidden"), bt("hm-only-hidden", "hm-nest-hidden"),
    ]
    return nodes, edges


# id -> (recordName, sizeInBytes). Three case variants tie on "alpha"; three
# sizes tie on 0 and two on 5; names that order differently by ICU collation,
# by code point and by UTF-16 unit (so-10 against so-11 and so-12).
SO_RECORDS = {
    "so-01": ("alpha", None), "so-02": ("Alpha", 0), "so-03": ("ALPHA", 0),
    "so-04": ("_under", 5), "so-05": ("9lives", 5), "so-06": ("éclair", 10**12),
    "so-07": ("Zebra", 2.5), "so-08": ("Eclair", None), "so-09": ("中文", 0),
    "so-10": ("ｚfull", None), "so-11": ("\U0001f600emoji", None),
    "so-12": ("\U00020000extb", None), "so-13": ("İstanbul", None),
    "so-14": ("ΟΔΟΣ", None), "so-15": ("a_b", None),
    "so-16": ("a%b", None), "so-17": ("a b", None),
}
# UTF-16 unit order of the lowercased names, ties by id: Cypher's order,
# which kh_merge.sort_order follows. By code point, so-10 would come last.
SO_NAME_ASC = ["so-05", "so-04", "so-17", "so-16", "so-15", "so-01", "so-02", "so-03", "so-08",
               "so-g", "so-13", "so-07", "so-06", "so-14", "so-09", "so-11", "so-12", "so-10"]
SO_SIZE_ASC = ["so-02", "so-03", "so-09", "so-07", "so-04", "so-05", "so-06",
               "so-01", "so-08", "so-10", "so-11", "so-12", "so-13", "so-14", "so-15", "so-16",
               "so-17", "so-g"]
SO_SIZED = {"so-02", "so-03", "so-09", "so-07", "so-04", "so-05", "so-06"}


def sort_shapes() -> tuple[list, list]:
    nodes = [app(SO, "Sort order", connector=DRIVE, app_group="Google Workspace"),
             rg("so-g", "Group", **_group(SO))]
    edges = [user_app(USER_U, SO), nr(SO, "so-g"), ip("so-g", SO)]
    for node_id, (name, size) in SO_RECORDS.items():
        nodes.append(rec(node_id, name, sizeInBytes=size, **_item(SO)))
        edges += [nr("so-g", node_id), ip(node_id, "so-g"), bt(node_id, "so-g")]
    return nodes, edges


KN_LIVE = {"kn-f1", "kn-f2", "kn-f5", "kn-d3", "kn-d4", "kn-d6", "kn-f7", "kn-d8"}


def nested_collection() -> tuple[list, list]:
    """A collection with nested folders as the writer stores it: every item
    BELONGS_TO the App, hierarchy edges App -> root items and folder -> child.
    kn-d6 and the folder kn-f7 live under a deleted folder."""
    folder = {"origin": "UPLOAD", "connector_id": KN, "mimeType": FOLDER_MIME}
    file = {"origin": "UPLOAD", "connector_id": KN}
    nodes = [
        app(KN, "Nested collection", connector=KB, app_group="Local Storage",
            scope="personal", hideConnector=True),
        rec("kn-f1", "Folder one", **folder), rec("kn-f2", "Folder two", **folder),
        rec("kn-f5", "Empty folder", **folder),
        rec("kn-del", "Deleted folder", isDeleted=True, **folder),
        rec("kn-d3", "Doc three", **file), rec("kn-d4", "Doc four", **file),
        rec("kn-d6", "Doc under the deleted folder", **file),
        rec("kn-f7", "Folder under the deleted folder", **folder),
        rec("kn-d8", "Doc in the folder under the deleted folder", **file),
    ]
    edges = [
        perm(USER_U, KN, role="WRITER"),
        *(bt(n, KN, "KB") for n in ("kn-f1", "kn-f2", "kn-f5", "kn-del", "kn-d3", "kn-d4", "kn-d6",
                                     "kn-f7", "kn-d8")),
        nr(KN, "kn-f1"), nr(KN, "kn-d4"), nr("kn-f1", "kn-f2"), nr("kn-f1", "kn-f5"),
        nr("kn-f2", "kn-d3"), nr("kn-f2", "kn-del"), nr("kn-del", "kn-d6"),
        nr("kn-del", "kn-f7"), nr("kn-f7", "kn-d8"),
    ]
    return nodes, edges


# What USER_U may see under RS.
RS_VISIBLE_U = {
    "rs-g1", "rs-p2", "rs-x",                                   # rs-x: STRICT, one full path is enough
    "rs-s", "rs-s-open", "rs-s3", "rs-s2", "rs-s2-child",       # seeds, a seed below a seed
    "rs-d1", "rs-d2", "rs-d3", "rs-d4", "rs-d2-r", "rs-d3-r",   # declared inside declared
    "rs-d6", "rs-d6-r", "rs-multi",
    "rs-c1", "rs-c2", "rs-v1",
}
# The check adds the App and what only hideChildren hides.
RS_CHECK_U = RS_VISIBLE_U | {"rs-d4-r", RS}
RS_VISIBLE_W = {"rs-g1", "rs-p2", "rs-x", "rs-c1", "rs-c2", "rs-v1"}


def rule_shapes() -> tuple[list, list]:
    """DAG with one passing and one failing parent; STRICT/RESTRICTED below a
    seed; seeds below seeds; a granted seed that also inherits; declared groups
    nested in declared groups (one hidden, one granted under an ungranted one); a
    record in several groups; a two-node cycle; one virtual record id in two orgs."""
    item = _item(RS)
    nodes = [
        app(RS, "Rule shapes", connector=DRIVE, app_group="Google Workspace"),
        _node("User", USER_W, email="w@example.com", fullName="W"),
        rg("rs-g1", "Open group", **_group(RS)),
        rec("rs-p1", "Gap parent", **item),
        rec("rs-p2", "Open parent", **item),
        rec("rs-x", "Strict, two parents", rule="STRICT", **item),
        rec("rs-y", "Inherits only from the gap", **item),
        rec("rs-s", "Seed", **item),
        rec("rs-s-open", "Open below the seed", **item),
        rec("rs-s-strict", "Strict below the seed", rule="STRICT", **item),
        rec("rs-s-restr", "Restricted below the seed", rule="RESTRICTED", **item),
        rec("rs-s-gap", "Gap below the seed", **item),
        rec("rs-s2", "Seed below a seed", **item),
        rec("rs-s2-child", "Below the second seed", **item),
        rec("rs-s3", "Granted and inheriting below the seed", **item),
        rg("rs-d1", "Declared, granted", **_group(RS, permissionModel=RGL)),
        rg("rs-d2", "Declared inside a declaration", **_group(RS, permissionModel=RGL)),
        rg("rs-d3", "Plain group inside", **_group(RS)),
        rec("rs-d2-r", "Record of the inner declaration", **item),
        rec("rs-d3-r", "Granted record in the scope", **item),
        rec("rs-d3-r-child", "Child of the granted scope record", **item),
        rg("rs-d4", "Hidden declaration inside", **_group(RS, permissionModel=RGL, hideChildren=True)),
        rec("rs-d4-r", "Record of the hidden declaration", **item),
        rg("rs-d5", "Declared, ungranted, closed", **_group(RS, permissionModel=RGL)),
        rg("rs-d6", "Granted declaration under a closed one", **_group(RS, permissionModel=RGL)),
        rec("rs-d6-r", "Record of the granted inner declaration", **item),
        rec("rs-multi", "Member of a declared and an open group", **item),
        rg("rs-u", "Closed group", **_group(RS)),
        rec("rs-multi2", "Member of undeclared groups only", **item),
        rec("rs-c1", "Cycle one", **item),
        rec("rs-c2", "Cycle two", **item),
        rec("rs-v1", "Copy in this org", virtualRecordId="vr-rs", indexingStatus="COMPLETED", **item),
        rec("rs-v0", "Copy in another org", orgId="org-2", virtualRecordId="vr-rs",
            indexingStatus="COMPLETED", **item),
    ]
    in_g1 = ("rs-p1", "rs-p2", "rs-x", "rs-y", "rs-s", "rs-s-open", "rs-s-strict", "rs-s-restr",
             "rs-s-gap", "rs-s2", "rs-s2-child", "rs-s3", "rs-c1", "rs-c2", "rs-v1", "rs-v0")
    edges = [
        user_app(USER_U, RS), user_app(USER_W, RS),
        nr(RS, "rs-g1"), ip("rs-g1", RS),
        nr("rs-g1", "rs-p1"), nr("rs-g1", "rs-p2"), ip("rs-p2", "rs-g1"),
        nr("rs-p1", "rs-x"), nr("rs-p2", "rs-x"), ip("rs-x", "rs-p1"), ip("rs-x", "rs-p2"),
        nr("rs-p1", "rs-y"), nr("rs-p2", "rs-y"), ip("rs-y", "rs-p1"),
        nr("rs-p1", "rs-s"), perm(USER_U, "rs-s"),
        nr("rs-s", "rs-s-open"), ip("rs-s-open", "rs-s"),
        nr("rs-s", "rs-s-strict"), ip("rs-s-strict", "rs-s"),
        nr("rs-s", "rs-s-restr"), ip("rs-s-restr", "rs-s"), perm(USER_U, "rs-s-restr"),
        nr("rs-s", "rs-s-gap"),
        nr("rs-s-gap", "rs-s2"), perm(USER_U, "rs-s2"),
        nr("rs-s2", "rs-s2-child"), ip("rs-s2-child", "rs-s2"),
        nr("rs-s", "rs-s3"), ip("rs-s3", "rs-s"), perm(USER_U, "rs-s3"),
        *(bt(n, "rs-g1") for n in in_g1),
        nr(RS, "rs-d1"), perm(USER_U, "rs-d1"),
        nr("rs-d1", "rs-d2"), nr("rs-d2", "rs-d3"),
        nr("rs-d2", "rs-d2-r"), bt("rs-d2-r", "rs-d2"),
        nr("rs-d3", "rs-d3-r"), bt("rs-d3-r", "rs-d3"), perm(USER_U, "rs-d3-r"),
        nr("rs-d3-r", "rs-d3-r-child"), ip("rs-d3-r-child", "rs-d3-r"),
        nr("rs-d1", "rs-d4"), nr("rs-d4", "rs-d4-r"), bt("rs-d4-r", "rs-d4"),
        nr(RS, "rs-d5"), nr("rs-d5", "rs-d6"), perm(USER_U, "rs-d6"), bt("rs-d6-r", "rs-d6"),
        bt("rs-multi", "rs-d2"), bt("rs-multi", "rs-g1"),
        nr(RS, "rs-u"), bt("rs-multi2", "rs-g1"), bt("rs-multi2", "rs-u"),
        nr("rs-g1", "rs-c1"), ip("rs-c1", "rs-g1"),
        nr("rs-c1", "rs-c2"), ip("rs-c2", "rs-c1"), nr("rs-c2", "rs-c1"), ip("rs-c1", "rs-c2"),
        nr("rs-g1", "rs-v1"), ip("rs-v1", "rs-g1"),
        nr("rs-g1", "rs-v0"), ip("rs-v0", "rs-g1"), perm(USER_U, "rs-v0"),
    ]
    return nodes, edges


SHAPES = (legacy_edges, shared_with_me, hidden_depth, depth_bound, hidden_membership,
          sort_shapes, nested_collection, rule_shapes)
# Browsed from every node by the invariant tests; the chains and the sort App add
# nothing there but time. The legacy-edge App has its own test.
BROWSED_APPS = (APP, KB_APP, SW, HM, KN, RS)
# One accepted divergence, pinned in test_provider_v3_scope_arms: below a
# RECORD_GROUP_LEVEL stop, browse follows the hop rule to nodes in no group of the
# declared scope, which the check refuses. These are its instances in this graph.
BROWSE_ONLY = PRUNE_GAP | {"v3-dec-r3-child", "v3-dec-nested-granted-child", "rs-d3-r-child"}


def _graph() -> tuple[list, list]:
    nodes, edges = _scope_arms_graph()
    seen = {n["id"] for n in nodes}
    for shape in SHAPES:
        more_nodes, more_edges = shape()
        for node in more_nodes:
            assert node["id"] not in seen, node["id"]
            seen.add(node["id"])
        nodes += more_nodes
        edges += more_edges
    return nodes, edges


async def _store_link_as_hierarchy(neo4j_provider, arango_provider, source: str, target: str, rel: str) -> None:
    props = {"relationshipType": rel, "createdAtTimestamp": TS}
    await neo4j_provider.client.execute_query(
        "MATCH (a:Record {id: $a}), (b:Record {id: $b}) CREATE (a)-[r:NODE_RELATION]->(b) SET r = $props",
        parameters={"a": source, "b": target, "props": props},
    )
    records = CollectionNames.RECORDS.value
    await arango_provider.http_client.execute_aql(
        "INSERT MERGE(@props, {_from: @a, _to: @b}) INTO @@edges",
        {"props": props, "a": f"{records}/{source}", "b": f"{records}/{target}",
         "@edges": CollectionNames.NODE_RELATIONS.value},
    )


@pytest.fixture(scope="module")
async def shapes(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> dict:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))
    for source, target, rel in UNMIGRATED_LINKS:
        await _store_link_as_hierarchy(neo4j_provider, arango_provider, source, target, rel)
    parents: dict[str, set[str]] = {}
    for edge in edges:
        if edge["type"] == "NODE_RELATION":
            parents.setdefault(edge["to"], set()).add(edge["from"])
    for source, target, _ in UNMIGRATED_LINKS:
        parents.setdefault(target, set()).add(source)
    return {
        "nodes": nodes,
        "kind": {n["id"]: n["kind"] for n in nodes},
        "app_of": {n["id"]: (n["id"] if n["kind"] == "App" else n["props"].get("connectorId")) for n in nodes},
        "multi_parent": {n for n, p in parents.items() if len(p) > 1},
    }


@pytest.fixture(params=BACKENDS)
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def _page(provider, *, user: str = USER_U, access: dict | None = None, **overrides: Any) -> dict:  # noqa: ANN401
    access = access or await provider.get_knowledge_hub_access_v3(user, ORG)
    kwargs = {
        "app_id": APP, "org_id": ORG,
        "grantee_ids": access["grantee_ids"],
        "gated_app_ids": access["gated_app_ids"],
        "grants_by_connector": access["by_connector"],
        "limit": 500, "flatten": True, "sort_field": "name", "sort_dir": "ASC",
        "include_total": True,
    }
    kwargs.update(overrides)
    return await provider.get_knowledge_hub_connector_page_v3(**kwargs)


def _start_type(shapes: dict, node_id: str) -> str:
    return "recordGroup" if shapes["kind"][node_id] == "RecordGroup" else "record"


async def _browse(provider, shapes: dict, start: str, *, flatten: bool = False, **overrides: Any) -> dict:  # noqa: ANN401
    return await _page(provider, start_id=start, start_type=_start_type(shapes, start),
                       flatten=flatten, include_scope=True, **overrides)


def _ids(page: dict) -> list[str]:
    return [row["id"] for row in page["rows"]]


def _trail(page: dict) -> list[str]:
    return [crumb["id"] for crumb in (page.get("scope") or {}).get("breadcrumbs") or []]


def _admitted(page: dict) -> bool:
    return bool((page.get("scope") or {}).get("admitted"))


async def _check(provider, ids, *, user: str = USER_U) -> set[str]:
    return set((await provider.check_access(user, ORG, node_ids=list(ids))).node_ids)


def _ids_of(shapes: dict, *apps: str, kinds: tuple[str, ...] = ("Record", "RecordGroup")) -> list[str]:
    return [n["id"] for n in shapes["nodes"] if n["kind"] in kinds and shapes["app_of"][n["id"]] in apps]


def _comparable(page: dict, multi_parent: set[str]) -> dict:
    rows = [{k: v for k, v in row.items() if row["id"] not in multi_parent or k not in PARENT_FIELDS}
            for row in page["rows"]]
    return {**page, "rows": rows}


async def _same(shapes: dict, neo4j_provider, arango_provider, **overrides: Any) -> dict:  # noqa: ANN401
    expected = await _page(neo4j_provider, **overrides)
    actual = await _page(arango_provider, **overrides)
    assert _ids(actual) == _ids(expected), overrides
    assert _comparable(actual, shapes["multi_parent"]) == _comparable(expected, shapes["multi_parent"]), overrides
    return expected


@pytest.fixture(scope="module")
async def browsed(shapes, neo4j_provider, arango_provider) -> dict:
    """Every node of BROWSED_APPS as a browse start (children, with scope), per
    backend. A start that raises is kept as its error, so one bad start names
    itself instead of failing the fixture."""
    starts = _ids_of(shapes, *BROWSED_APPS)
    out: dict[str, dict[str, dict]] = {}
    for name, provider in (("neo4j", neo4j_provider), ("arango", arango_provider)):
        access = await provider.get_knowledge_hub_access_v3(USER_U, ORG)
        pages: dict[str, dict] = {}
        for start in starts:
            try:
                pages[start] = await _browse(provider, shapes, start, access=access)
            except Exception as exc:
                pages[start] = {"error": repr(exc), "rows": [], "scope": None}
        out[name] = pages
    return out


@pytest.mark.parametrize("backend", BACKENDS)
async def test_browse_opens_and_lists_only_what_the_check_admits(shapes, browsed, backend, request) -> None:
    """Browse answers as the access check does: another org's node or a
    placeholder never opens, an inaccessible start does not open, and below a
    start the walk from the App does not reach, STRICT and RESTRICTED are
    skipped. BROWSE_ONLY is the one accepted exception."""
    provider = request.getfixturevalue(f"{backend}_provider")
    admitted = await _check(provider, _ids_of(shapes, *BROWSED_APPS))
    wrong = []
    for start, page in browsed[backend].items():
        if page.get("error"):
            wrong.append(f"{start}: raised {page['error']}")
            continue
        if _admitted(page) and start not in admitted and start not in BROWSE_ONLY:
            wrong.append(f"{start}: browse opens it, the check denies it")
        wrong.extend(f"{start}: lists {row_id}, the check denies it"
                     for row_id in _ids(page) if row_id not in admitted and row_id not in BROWSE_ONLY)
    assert OTHER_ORG_NODE in browsed[backend]
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_a_row_names_only_a_parent_the_user_can_open(shapes, browsed, backend, request) -> None:
    """parentId (and the parentName the service returns as ParentRef) is the node
    the item appears under, never one the user cannot open. In browse the item
    appears under the start."""
    provider = request.getfixturevalue(f"{backend}_provider")
    admitted = await _check(provider, _ids_of(shapes, *BROWSED_APPS))
    wrong = []
    for app_id in BROWSED_APPS:
        for row in (await _page(provider, app_id=app_id))["rows"]:
            parent = row["parentId"]
            if parent != app_id and parent not in admitted and parent not in NAMES_NO_GATED_APP:
                wrong.append(f"flatten {app_id}: {row['id']} names {parent} ({row.get('parentName')!r})")
    wrong.extend(f"browse {start}: {row['id']} names {row['parentId']}"
                 for start, page in browsed[backend].items() for row in page["rows"]
                 if row["parentId"] != start)
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_a_file_in_two_places_takes_the_trail_it_was_opened_by(shapes, backend, request) -> None:
    """Without via_parent_id the drive location wins; with it, the named
    accessible parent does; a via naming a closed parent or no parent is
    ignored. The flatten lists sw-x once, under its folder, and sw-y under the
    only parent the user can reach. Both routes up must reach the trail
    builder: marking only the first one the walk finds breaks one of them."""
    provider = request.getfixturevalue(f"{backend}_provider")
    drive = [SW, "sw-drive", "sw-folder", "sw-x"]
    inbox = [SW, "sw-inbox", "sw-x"]
    cases = [("sw-x", None, drive), ("sw-x", "sw-inbox", inbox), ("sw-x", "sw-folder", drive),
             ("sw-x", "sw-gapf", drive), ("sw-y", None, [SW, "sw-inbox", "sw-y"]),
             ("sw-y", "sw-gapf", [SW, "sw-inbox", "sw-y"])]
    wrong = []
    for start, via, expected in cases:
        page = await _browse(provider, shapes, start, via_parent_id=via)
        if not _admitted(page) or _trail(page) != expected:
            wrong.append(f"{start} via {via}: {_trail(page)} (admitted={_admitted(page)}), want {expected}")
    flat = await _page(provider, app_id=SW)
    ids = _ids(flat)
    if sorted(ids) != sorted({"sw-drive", "sw-inbox", "sw-folder", "sw-x", "sw-y"}):
        wrong.append(f"flatten: {ids}")
    parents = {row["id"]: row["parentId"] for row in flat["rows"]}
    for node_id, parent in (("sw-x", "sw-folder"), ("sw-y", "sw-inbox")):
        if parents.get(node_id) != parent:
            wrong.append(f"flatten: {node_id} names {parents.get(node_id)}, want {parent}")
    for start, children in (("sw-inbox", {"sw-x", "sw-y"}), ("sw-folder", {"sw-x"}), ("sw-gapf", set())):
        got = set(_ids(await _browse(provider, shapes, start)))
        if got != children:
            wrong.append(f"browse {start}: {sorted(got)}, want {sorted(children)}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_edges_that_are_not_hierarchy_are_never_walked(shapes, backend, request) -> None:
    """Only PARENT_CHILD/ATTACHMENT is hierarchy, in the listing, browse, the
    hidden-above probe and the check alike. An untyped edge (the split leaves it on
    the hierarchy storage) and a link waiting for the split are not."""
    provider = request.getfixturevalue(f"{backend}_provider")
    visible = {"ed-g", "ed-anchor", "ed-hidden", "ed-seed"}
    not_hierarchy = {"ed-untyped", "ed-untyped-child", "ed-linked"}
    wrong = []
    listed = set(_ids(await _page(provider, app_id=ED)))
    if listed != visible:
        wrong.append(f"flatten: extra {sorted(listed - visible)}, missing {sorted(visible - listed)}")
    admitted = await _check(provider, _ids_of(shapes, ED))
    if admitted != visible:
        wrong.append(f"check: extra {sorted(admitted - visible)}, missing {sorted(visible - admitted)}")
    # ed-seed is a chain-top: its parent ed-gap is closed, its own group ed-g open.
    for start, children in (("ed-g", {"ed-anchor", "ed-seed"}), ("ed-anchor", set())):
        got = set(_ids(await _browse(provider, shapes, start)))
        if got != children:
            wrong.append(f"browse {start}: {sorted(got)}, want {sorted(children)}")
    wrong.extend([f"browse {start}: opened" for start in sorted(not_hierarchy)
                  if _admitted(await _browse(provider, shapes, start))])
    if not _admitted(await _browse(provider, shapes, "ed-seed")):
        wrong.append("browse ed-seed: refused, an untyped edge put it under a hidden group")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_a_hidden_groups_records_stay_out_unless_a_visible_scope_group_holds_them(
    shapes, backend, request
) -> None:
    """A hidden group's records are not listed, unless they also belong to a
    visible group of the scope (hm-both); a granted record that only belongs to
    a hidden channel does not surface as a seed or open by browse; the check
    still admits all of them."""
    provider = request.getfixturevalue(f"{backend}_provider")
    wrong = []
    expected = {"hm-chan", "hm-dec", "hm-nest-hidden", "hm-both"}
    listed = set(_ids(await _page(provider, app_id=HM)))
    if listed != expected:
        wrong.append(f"flatten: extra {sorted(listed - expected)}, missing {sorted(expected - listed)}")
    wrong.extend([f"browse {start}: opened below a hidden group" for start in ("hm-msg", "hm-msg-nested", "hm-only-hidden")
                  if _admitted(await _browse(provider, shapes, start))])
    if set(_ids(await _browse(provider, shapes, "hm-nest-hidden"))):
        wrong.append("browse hm-nest-hidden: lists its records")
    members = {"hm-msg", "hm-msg-nested", "hm-both", "hm-only-hidden"}
    if (admitted := await _check(provider, members)) != members:
        wrong.append(f"check: missing {sorted(members - admitted)}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_hide_children_hides_at_every_depth(shapes, backend, request) -> None:
    """A channel that hides its children hides the whole subtree from listings
    and browse at any depth the walks reach (50); the check admits it all. A
    hidden-above probe bounded at 20 hops would open hd-c21 and below."""
    provider = request.getfixturevalue(f"{backend}_provider")
    chain = [f"hd-c{k}" for k in range(1, HD_DEPTH + 1)]
    wrong = []
    listed = set(_ids(await _page(provider, app_id=HD)))
    if listed != {"hd-hidden"}:
        wrong.append(f"flatten: {sorted(listed)}")
    wrong.extend([f"browse {start}: opened below the hidden channel" for start in ("hd-c1", "hd-c20", "hd-c21", "hd-c25")
                  if _admitted(await _browse(provider, shapes, start))])
    if set(_ids(await _browse(provider, shapes, "hd-hidden"))):
        wrong.append("browse hd-hidden: lists its children")
    if (admitted := await _check(provider, chain)) != set(chain):
        wrong.append(f"check: missing {sorted(set(chain) - admitted)}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("backend", BACKENDS)
async def test_rule_shapes_exact_sets(shapes, backend, request) -> None:
    provider = request.getfixturevalue(f"{backend}_provider")
    ids = _ids_of(shapes, RS, kinds=("Record", "RecordGroup", "App"))
    wrong = []
    for user, visible, checked in ((USER_U, RS_VISIBLE_U, RS_CHECK_U),
                                   (USER_W, RS_VISIBLE_W, RS_VISIBLE_W | {RS})):
        listed = set(_ids(await _page(provider, user=user, app_id=RS)))
        if listed != visible:
            wrong.append(f"{user} flatten: extra {sorted(listed - visible)}, missing {sorted(visible - listed)}")
        admitted = await _check(provider, ids, user=user)
        if admitted != checked:
            wrong.append(f"{user} check: extra {sorted(admitted - checked)}, missing {sorted(checked - admitted)}")
    # The other org's copy sorts first and is granted; it is never cited.
    for indexed_only in (False, True):
        cited = (await provider.check_access(
            USER_U, ORG, virtual_record_ids=["vr-rs"], indexed_only=indexed_only,
        )).records_by_vrid
        if cited != {"vr-rs": "rs-v1"}:
            wrong.append(f"vrid (indexed_only={indexed_only}): {cited}")
    # A cycle has one trail: the walk up must not come back through the start.
    for start, expected in (("rs-c1", [RS, "rs-g1", "rs-c1"]), ("rs-c2", [RS, "rs-g1", "rs-c1", "rs-c2"]),
                            ("rs-x", [RS, "rs-g1", "rs-p2", "rs-x"])):
        page = await _browse(provider, shapes, start)
        if _trail(page) != expected:
            wrong.append(f"trail {start}: {_trail(page)}, want {expected}")
    assert not wrong, "\n".join(wrong)


async def test_rule_shapes_pages_agree(shapes, neo4j_provider, arango_provider) -> None:
    """Arango's pages are Neo4j's on the rule shapes: flatten, direct children,
    sorts, and every node as a browse start in both modes, for U and W."""
    for user in (USER_U, USER_W):
        await _same(shapes, neo4j_provider, arango_provider, user=user, app_id=RS)
        await _same(shapes, neo4j_provider, arango_provider, user=user, app_id=RS, flatten=False, include_scope=True)
    for sort_field, sort_dir in (("name", "DESC"), ("nodeType", "ASC"), ("createdAt", "DESC")):
        await _same(shapes, neo4j_provider, arango_provider, app_id=RS, sort_field=sort_field, sort_dir=sort_dir)
    for start in _ids_of(shapes, RS):
        for flatten in (False, True):
            try:
                await _same(shapes, neo4j_provider, arango_provider, start_id=start,
                            start_type=_start_type(shapes, start), flatten=flatten, include_scope=True)
            except AssertionError as exc:
                raise AssertionError(f"browse from {start} (flatten={flatten}): {exc}") from exc


async def test_a_user_gated_into_nothing_sees_and_opens_nothing(shapes, provider) -> None:
    """USER_V holds no user-app relation, membership or grant here: every App's
    page is empty and the check admits nothing, whatever the other users hold."""
    every = [n["id"] for n in shapes["nodes"] if n["kind"] in ("Record", "RecordGroup", "App")]
    assert await _check(provider, every, user=USER_V) == set()
    for app_id in (*BROWSED_APPS, HD, DP, SO):
        assert (await _page(provider, user=USER_V, app_id=app_id))["rows"] == [], app_id


@pytest.mark.parametrize("backend", BACKENDS)
async def test_names_sort_by_utf16_unit_and_sizes_keep_zero(shapes, backend, request) -> None:
    """Lowercased names sort by UTF-16 unit, as Cypher
    orders them (an astral name before U+FF5A), ties by id, nulls last; a size of
    0 is a size, a missing one is not. Arango sorts in Python with the merge's
    comparator, so the two agree."""
    provider = request.getfixturevalue(f"{backend}_provider")
    by_name = await _page(provider, app_id=SO)
    assert _ids(by_name) == SO_NAME_ASC
    assert {r["id"]: r["sortKey"] for r in by_name["rows"]}["so-02"] == "alpha"
    assert _ids(await _page(provider, app_id=SO, sort_field="sizeInBytes")) == SO_SIZE_ASC
    assert set(_ids(await _page(provider, app_id=SO, filters={"size": {"gte": 0}}))) == SO_SIZED
    assert _ids(await _page(provider, app_id=SO, filters={"search_query": "a_b"})) == ["so-15"]
    assert _ids(await _page(provider, app_id=SO, filters={"search_query": "%"})) == ["so-16"]


def _in_comparator_order(rows: list[dict], descending: bool) -> bool:
    keys = [key_for(row, descending) for row in rows]
    return all(not later < earlier for earlier, later in zip(keys, keys[1:]))


async def _walk(provider, *, sort_field: str, sort_dir: str, filters: dict | None) -> tuple[list, list]:
    pages: list[list[str]] = []
    after = None
    page: dict = {}
    for _ in range(50):
        page = await _page(provider, app_id=SO, limit=2, sort_field=sort_field, sort_dir=sort_dir,
                           filters=filters, after=after, include_total=after is None)
        pages.append(_ids(page))
        if not page["hasMore"]:
            break
        last = page["rows"][-1]
        after = {"id": last["id"], "sortKey": last["sortKey"], "nullRank": last["nullRank"]}
    back: list[str] = []
    if page.get("rows"):
        first = page["rows"][0]
        back = _ids(await _page(
            provider, app_id=SO, limit=2, sort_field=sort_field, sort_dir=sort_dir, filters=filters,
            after={"id": first["id"], "sortKey": first["sortKey"], "nullRank": first["nullRank"]},
            direction="prev", include_total=False,
        ))
    return pages, back


@pytest.mark.parametrize(("sort_field", "sort_dir"), [
    ("name", "ASC"), ("name", "DESC"), ("sizeInBytes", "ASC"), ("sizeInBytes", "DESC"),
])
@pytest.mark.parametrize("filters", [
    None,
    {"size": {"gte": 0}},
    {"search_query": "ALPHA", "node_types": ["record"]},
    {"node_types": ["record"], "size": {"lte": 5}},
], ids=["none", "sized", "search-records", "small-records"])
async def test_pages_agree_over_ties_nulls_and_filters(
    shapes, neo4j_provider, arango_provider, sort_field, sort_dir, filters
) -> None:
    full = await _same(shapes, neo4j_provider, arango_provider, app_id=SO,
                       sort_field=sort_field, sort_dir=sort_dir, filters=filters)
    assert _in_comparator_order(full["rows"], sort_dir == "DESC")
    walks = {name: await _walk(p, sort_field=sort_field, sort_dir=sort_dir, filters=filters)
             for name, p in (("neo4j", neo4j_provider), ("arango", arango_provider))}
    assert walks["arango"] == walks["neo4j"]
    assert [i for page in walks["neo4j"][0] for i in page] == _ids(full)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(("sort_field", "sort_dir", "boundary", "after", "before"), [
    ("name", "ASC", {"id": "so-02x", "sortKey": "alpha", "nullRank": 0}, ["so-03", "so-08"], ["so-01", "so-02"]),
    ("sizeInBytes", "ASC", {"id": "so-025", "sortKey": 0, "nullRank": 0}, ["so-03", "so-09"], ["so-02"]),
    ("sizeInBytes", "ASC", {"id": "so-1", "sortKey": None, "nullRank": 1}, ["so-10", "so-11"], ["so-01", "so-08"]),
    ("sizeInBytes", "DESC", {"id": "so-045", "sortKey": 5, "nullRank": 0}, ["so-05", "so-07"], ["so-06", "so-04"]),
], ids=["tie-in-name", "tie-at-zero", "null-bucket", "tie-desc"])
async def test_a_cursor_whose_row_is_gone_resumes_in_place(
    shapes, backend, request, sort_field, sort_dir, boundary, after, before
) -> None:
    """A cursor's boundary row deleted between pages is a boundary that matches
    no row: both directions resume at its place in the order."""
    provider = request.getfixturevalue(f"{backend}_provider")
    common = {"app_id": SO, "limit": 2, "sort_field": sort_field, "sort_dir": sort_dir,
              "after": boundary, "include_total": False}
    assert _ids(await _page(provider, **common)) == after
    assert _ids(await _page(provider, **common, direction="prev")) == before


async def test_a_collection_with_nested_folders(shapes, neo4j_provider, arango_provider) -> None:
    """The collection grant opens every live item; browse follows the
    folders; a folder is its own trail; an item under a deleted folder falls back
    to the collection, and a folder there still lists its contents: a
    collection has nothing inaccessible above it. Both backends, and Arango equal
    to Neo4j."""
    flat = await _same(shapes, neo4j_provider, arango_provider, app_id=KN)
    assert set(_ids(flat)) == KN_LIVE
    top = await _same(shapes, neo4j_provider, arango_provider, app_id=KN, flatten=False, include_scope=True)
    assert set(_ids(top)) == {"kn-f1", "kn-d4"}
    children = {"kn-f1": {"kn-f2", "kn-f5"}, "kn-f2": {"kn-d3"}, "kn-f5": set(),
                "kn-d3": set(), "kn-d4": set(), "kn-d6": set(), "kn-f7": {"kn-d8"}}
    trails = {"kn-f2": [KN, "kn-f1", "kn-f2"], "kn-d3": [KN, "kn-f1", "kn-f2", "kn-d3"], "kn-d6": [KN, "kn-d6"],
              "kn-f7": [KN, "kn-f7"]}
    for start, expected in children.items():
        page = await _same(shapes, neo4j_provider, arango_provider, start_id=start, start_type="record",
                           flatten=False, include_scope=True)
        assert page["scope"]["admitted"], start
        assert set(_ids(page)) == expected, start
        if start in trails:
            assert _trail(page) == trails[start], start
    below = await _same(shapes, neo4j_provider, arango_provider, start_id="kn-f1", start_type="record",
                        flatten=True, include_scope=True)
    assert set(_ids(below)) == {"kn-f2", "kn-f5", "kn-d3"}
    gone = await _same(shapes, neo4j_provider, arango_provider, start_id="kn-del", start_type="record",
                       flatten=False, include_scope=True)
    assert gone["scope"]["admitted"] is False
    for provider in (neo4j_provider, arango_provider):
        assert await _check(provider, [*KN_LIVE, "kn-del", KN]) == KN_LIVE | {KN}


DP_VISIBLE = {"dp-g", *(f"dp-{k}" for k in range(1, DP_CHAIN)), "dp-s",
              *(f"dp-s-{k}" for k in range(1, DP_SEED_CHAIN))}


@pytest.mark.parametrize("backend", BACKENDS)
async def test_the_depth_bound_is_fifty_hops_everywhere(shapes, backend, request) -> None:
    """The walk down, the seed walk, the check's walk up and browse's walk up all
    stop at 50 hops: dp-49 (50 below the App) and dp-s-50 (50 below the seed) are
    in, dp-50 and dp-s-51 are out, identically on both backends."""
    provider = request.getfixturevalue(f"{backend}_provider")
    wrong = []
    listed = set(_ids(await _page(provider, app_id=DP)))
    if listed != DP_VISIBLE:
        wrong.append(f"flatten: extra {sorted(listed - DP_VISIBLE)}, missing {sorted(DP_VISIBLE - listed)}")
    admitted = await _check(provider, _ids_of(shapes, DP))
    if admitted != DP_VISIBLE:
        wrong.append(f"check: extra {sorted(admitted - DP_VISIBLE)}, missing {sorted(DP_VISIBLE - admitted)}")
    deepest = await _browse(provider, shapes, "dp-49")
    expected = [DP, "dp-g", *(f"dp-{k}" for k in range(1, DP_CHAIN))]
    if not _admitted(deepest) or _trail(deepest) != expected:
        wrong.append(f"browse dp-49: admitted={_admitted(deepest)}, {len(_trail(deepest))} crumbs")
    wrong.extend([f"browse {start}: opened beyond the bound" for start in ("dp-50", "dp-s-51")
                  if _admitted(await _browse(provider, shapes, start))])
    if not _admitted(await _browse(provider, shapes, "dp-s-50")):
        wrong.append("browse dp-s-50: refused within the bound")
    assert not wrong, "\n".join(wrong)


async def test_the_deep_pages_agree(shapes, neo4j_provider, arango_provider) -> None:
    await _same(shapes, neo4j_provider, arango_provider, app_id=DP)
    await _same(shapes, neo4j_provider, arango_provider, app_id=DP, sort_field="name", sort_dir="DESC", limit=25)
    await _same(shapes, neo4j_provider, arango_provider, app_id=HD)
    for start in ("dp-49", "dp-50", "dp-s", "dp-s-50", "hd-c21", "hd-c25"):
        await _same(shapes, neo4j_provider, arango_provider, start_id=start, start_type="record",
                    flatten=False, include_scope=True)
