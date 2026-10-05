"""The v3 per-hop rule, pinned across its whole truth table.

`test_rule_equivalence.py` gates `_kh_v2_rule_cypher`. Nothing gated
`_kh_v3_granted_hop` -- which is the rule the shipped global-flatten query
actually evaluates, and which was rewritten to name the inheritance probe once
instead of twice.

That rewrite is the risky shape: the obvious
three-conjunct compaction of this rule is equivalent for every *declared*
`accessRule` and **fails open on an undeclared one**, returning a node the real
rule hides. Scenario coverage cannot catch that -- only a deliberately
corrupted value separates the two forms. So this module enumerates the rule's
inputs directly:

    accessRule in {OPEN, STRICT, RESTRICTED, <absent>, <unrecognised>}
      x  inherits from the parent  in {yes, no}
      x  granted to the user       in {yes, no}

and asserts which of the 20 children the walk admits, and which the batch
access check admits on each backend.

Intended semantics:

    OPEN / absent   inheritance OR a direct grant
    STRICT          inheritance OR a direct grant  (strictness is the whole
                    chain being accessible, not this hop)
    RESTRICTED      inheritance AND a direct grant
    unrecognised    nothing -- corruption fails closed
"""

from __future__ import annotations

import pytest

from .fixture_graph import DRIVE, ORG, USER_U, _principals, app, ip, nr, perm, rec
from .loaders import arango_shape, load_into_arango, load_into_neo4j

APP = "hop-app"
ITEM = dict(record_type="FILE", connector_id=APP)

# (label used in ids, the accessRule property to write or None to omit it)
RULES = [
    ("open", "OPEN"),
    ("strict", "STRICT"),
    ("restricted", "RESTRICTED"),
    ("absent", None),
    ("bogus", "NOT_A_REAL_RULE"),
]


def _admits(rule: str | None, inherits: bool, granted: bool) -> bool:
    effective = rule if rule is not None else "OPEN"
    if effective in ("OPEN", "STRICT"):
        return inherits or granted
    if effective == "RESTRICTED":
        return inherits and granted
    return False


def _node_id(label: str, inherits: bool, granted: bool) -> str:
    return f"hop-{label}-{int(inherits)}{int(granted)}"


def _cases() -> list[tuple[str, str | None, bool, bool]]:
    return [
        (label, rule, inherits, granted)
        for label, rule in RULES
        for inherits in (True, False)
        for granted in (True, False)
    ]


def _graph() -> tuple[list, list]:
    principal_nodes, principal_edges = _principals()
    nodes = principal_nodes + [
        app(APP, "Hop rule", connector=DRIVE, app_group="Google Workspace"),
    ]
    edges = principal_edges + [
        {"type": "USER_APP_RELATION", "from": USER_U, "to": APP,
         "props": {"syncState": "COMPLETED", "lastSyncUpdate": 0}},
    ]
    for label, rule, inherits, granted in _cases():
        node_id = _node_id(label, inherits, granted)
        node = rec(node_id, f"{label} i={int(inherits)} g={int(granted)}",
                   **({} if rule is None else {"rule": rule}), **ITEM)
        if rule is None:
            # `rec()` always writes accessRule, defaulting to "OPEN" -- so the
            # absent case has to have the property removed, or it silently
            # becomes a second copy of the OPEN case and the test is vacuous.
            node["props"].pop("accessRule", None)
        nodes.append(node)
        edges.append(nr(APP, node_id))
        if inherits:
            edges.append(ip(node_id, APP))
        if granted:
            edges.append(perm(USER_U, node_id))
    return nodes, edges


@pytest.fixture(scope="module")
async def hop_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings):
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    # Arango's validator refuses an unrecognised accessRule, so the corrupt rows
    # cannot exist there; the rest of the table loads as it is.
    corrupt = {n["id"] for n in nodes if n["props"].get("accessRule") == "NOT_A_REAL_RULE"}
    await load_into_arango(arango_settings, *arango_shape(
        [n for n in nodes if n["id"] not in corrupt],
        [e for e in edges if e["from"] not in corrupt and e["to"] not in corrupt],
    ))
    return {"nodes": nodes, "edges": edges}


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def _visible(provider) -> set[str]:
    access = await provider.get_knowledge_hub_access_v3(
        user_key=USER_U, org_id=ORG,
    )
    page = await provider.get_knowledge_hub_connector_page_v3(
        app_id=APP, org_id=ORG,
        grantee_ids=access["grantee_ids"],
        gated_app_ids=access["gated_app_ids"],
        granted_ids=access["by_connector"].get(APP) or [],
        limit=500, flatten=True, sort_field="name", sort_dir="ASC",
        include_total=True,
    )
    return {row["id"] for row in page["rows"]}


def test_the_matrix_is_complete() -> None:
    """20 children, one per (rule x inherits x granted)."""
    cases = _cases()
    assert len(cases) == 20
    assert len({_node_id(label, i, g) for label, _, i, g in cases}) == 20


def test_the_absent_case_really_omits_the_property() -> None:
    """Guard the premise of `test_an_absent_rule_reads_as_open`.

    `rec()` writes `accessRule="OPEN"` by default, so without the explicit pop
    in `_graph()` the absent rows would be ordinary OPEN rows and that test
    would assert nothing.
    """
    nodes, _ = _graph()
    by_id = {n["id"]: n for n in nodes}
    for inherits in (True, False):
        for granted in (True, False):
            absent = by_id[_node_id("absent", inherits, granted)]
            assert "accessRule" not in absent["props"]
            bogus = by_id[_node_id("bogus", inherits, granted)]
            assert bogus["props"]["accessRule"] == "NOT_A_REAL_RULE"


async def test_every_combination_of_the_hop_rule(hop_graph, neo4j_provider) -> None:
    """The whole truth table at once, so a wrong cell names itself."""
    visible = await _visible(neo4j_provider)
    wrong = []
    for label, rule, inherits, granted in _cases():
        node_id = _node_id(label, inherits, granted)
        expected = _admits(rule, inherits, granted)
        if (node_id in visible) != expected:
            wrong.append(
                f"{node_id} (rule={rule!r} inherits={inherits} granted={granted}): "
                f"expected {'visible' if expected else 'hidden'}"
            )
    assert not wrong, "\n".join(wrong)


async def test_restricted_refuses_a_grant_without_inheritance(
    hop_graph, neo4j_provider
) -> None:
    """Called out on its own because it is the branch that separates
    RESTRICTED from STRICT and the one a compaction is most likely to lose."""
    visible = await _visible(neo4j_provider)
    assert _node_id("restricted", False, True) not in visible
    assert _node_id("restricted", True, False) not in visible
    assert _node_id("restricted", True, True) in visible


async def test_an_unrecognised_rule_fails_closed(hop_graph, neo4j_provider) -> None:
    """A node whose `accessRule` is corrupt must be hidden, even
    when it both inherits and is granted -- the case where the tempting
    three-conjunct compaction of this rule silently returns it.
    """
    visible = await _visible(neo4j_provider)
    for inherits in (True, False):
        for granted in (True, False):
            node_id = _node_id("bogus", inherits, granted)
            assert node_id not in visible, node_id


async def test_an_absent_rule_reads_as_open(hop_graph, neo4j_provider) -> None:
    """A node written before `accessRule` existed must behave as OPEN, not as
    corrupt -- absent and unrecognised are deliberately different."""
    visible = await _visible(neo4j_provider)
    assert _node_id("absent", True, False) in visible
    assert _node_id("absent", False, True) in visible
    assert _node_id("absent", False, False) not in visible


async def test_the_check_admits_the_same_cells(hop_graph, provider) -> None:
    """The check walks the same rule upward, so it admits the same cells. On
    Arango the corrupt rows are not stored, so there they are unknown ids."""
    ids = [_node_id(label, inherits, granted) for label, _, inherits, granted in _cases()]
    allowed = (await provider.check_access(USER_U, ORG, node_ids=ids)).node_ids
    wrong = [
        f"{_node_id(label, i, g)} (rule={rule!r} inherits={i} granted={g})"
        for label, rule, i, g in _cases()
        if (_node_id(label, i, g) in allowed) != _admits(rule, i, g)
    ]
    assert not wrong, "\n".join(wrong)
