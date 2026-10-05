"""Example 1's STRICT/RESTRICTED rules, plus the declaration and collection passes.

Example 2 is entirely OPEN nodes, so `$allowStrict` and the RESTRICTED
"inheritance AND grant" branch never discriminate there. This module covers the
rest of the rule, on both backends.

Example 1: App → RG1[R!], RG2[R!,granted]; RG1 → R3[S], R4[R!];
R4 → R6[S]; RG2 → R5[S]. U holds grants on both spaces. Accessible: 1, 2, 3, 5.
Hidden: 4 (RESTRICTED with no grant of its own) and 6 (below 4).
"""

import pytest

from .test_aql_parity import _ids as aql_ids
from .test_qpp_semantics import PER_HOP_RULE, _ids as cypher_ids

pytestmark = pytest.mark.integration


def _cypher_from(root_label: str, root_id: str) -> str:
    return f"""
    MATCH (root:{root_label} {{id:'{root_id}'}})
          ((p)-[r:NODE_RELATION]->(c) WHERE {PER_HOP_RULE})+ (n)
    RETURN DISTINCT n.id AS id
    """


async def test_example_one_hides_the_restricted_page(loaded_graph, neo4j_settings) -> None:
    """R4 is restricted and holds no grant, so it and everything under it go."""
    found = await cypher_ids(neo4j_settings, _cypher_from("App", "ex1-app"))
    assert found == {"ex1-rg1", "ex1-rg2", "ex1-r3", "ex1-r5"}, sorted(found)
    assert "ex1-r4" not in found, "restricted page with no grant must be hidden"
    assert "ex1-r6" not in found, "a strict node below a hidden ancestor must be hidden"


async def test_example_one_agrees_across_backends(
    loaded_graph, neo4j_settings, arango_settings
) -> None:
    cypher = await cypher_ids(neo4j_settings, _cypher_from("App", "ex1-app"))
    aql = await aql_ids(arango_settings, ["ex1-app"], "apps")
    print(f"\n[strict] cypher={sorted(cypher)}\n         aql   ={sorted(aql)}")
    assert cypher == aql, "Example 1 diverges between backends"


async def test_strict_node_below_a_gap_is_never_seeded(
    loaded_graph, neo4j_settings, arango_settings
) -> None:
    """The grants pass skips strict nodes.

    ex1-r4's only child, ex1-r6, is strict, so seeding with allowStrict=false —
    as the grants pass always is — must return nothing through it.

    The expected result being empty is exactly why the control below is here:
    on its own, `cypher == aql` compares two empty sets and would hold for a
    wrong seed id, a wrong Arango seed collection, or a QPP that matched
    nothing at all. ex2-r6 is the same shape with non-strict children, so it
    proves the seeded pass can return anything in the first place.
    """
    seeded_query = f"""
        UNWIND $seeds AS seedId
        MATCH (root {{id: seedId}})
              ((p)-[r:NODE_RELATION]->(c) WHERE {PER_HOP_RULE})+ (n)
        RETURN DISTINCT n.id AS id
        """
    control_cypher = await cypher_ids(
        neo4j_settings, seeded_query, seeds=["ex2-r6"], allowStrict=False,
    )
    control_aql = await aql_ids(
        arango_settings, ["ex2-r6"], "records", allow_strict=False,
    )
    assert control_cypher == {"ex2-r7", "ex2-r8"}, sorted(control_cypher)
    assert control_cypher == control_aql, "the seeded pass itself diverges"

    cypher = await cypher_ids(
        neo4j_settings, seeded_query, seeds=["ex1-r4"], allowStrict=False,
    )
    aql = await aql_ids(arango_settings, ["ex1-r4"], "records", allow_strict=False)
    assert cypher == set(), f"a strict child must not be seeded: {sorted(cypher)}"
    assert cypher == aql, f"grants-pass strict handling diverges: {cypher} vs {aql}"
    assert "ex1-r6" not in cypher, (
        "ex1-r6 is strict; below a gap reading (b) can never admit it"
    )


async def test_declaration_pass_ignores_node_flags(
    loaded_graph, neo4j_settings, arango_settings
) -> None:
    """Under APP_LEVEL, strict and restricted flags are not consulted.

    dec-rg1 and dec-r1 are both strict and restricted with no grants and no
    inheritance — invisible under the normal rule, reachable under the
    declaration pass.
    """
    normal = await cypher_ids(neo4j_settings, _cypher_from("App", "dec-app"))
    assert normal == set(), f"without the declaration these must be hidden: {sorted(normal)}"

    declared_cypher = await cypher_ids(
        neo4j_settings, _cypher_from("App", "dec-app"), skipChecks=True
    )
    declared_aql = await aql_ids(
        arango_settings, ["dec-app"], "apps", skip_checks=True
    )
    print(f"\n[declaration] cypher={sorted(declared_cypher)}\n              aql   ={sorted(declared_aql)}")
    assert declared_cypher == {"dec-rg1", "dec-r1"}, sorted(declared_cypher)
    assert declared_cypher == declared_aql, "declaration pass diverges between backends"


async def test_collection_pass_returns_every_item(
    loaded_graph, neo4j_settings, arango_settings
) -> None:
    """A collection's items are not reachable by a hierarchy traversal.

    These oracles walk NODE_RELATION only, which is the whole rule they exist to
    pin. A collection's items are attached to their App by BELONGS_TO
    (`entityType: KB`) and carry no hierarchy edge from it, as `kb_service`
    writes them. So neither pass can see them, and skipChecks changes nothing:
    the declaration shortcut still needs an edge to follow.

    Do not teach the oracle BELONGS_TO: it is the rule text every other test
    here depends on, so widening it for collections would change what all of
    them assert. The collection path is covered against the real provider, in
    `test_provider_v2_listing`.
    """
    normal = await cypher_ids(neo4j_settings, _cypher_from("App", "kb-1"))
    assert normal == set(), f"collection items are not reachable by the generic rule: {normal}"

    cypher = await cypher_ids(neo4j_settings, _cypher_from("App", "kb-1"), skipChecks=True)
    aql = await aql_ids(arango_settings, ["kb-1"], "apps", skip_checks=True)
    assert cypher == set(), sorted(cypher)
    assert cypher == aql, "collection pass diverges between backends"


async def test_the_flag_branches_decide_the_right_way(
    loaded_graph, neo4j_settings, arango_settings
) -> None:
    """The branches nothing else decides.

    flag-open is OPEN: inheritance alone admits it, with no grant of its own. If
    the OPEN branch ever started consulting a restriction, this fails.
    flag-granted is STRICT and granted with no inheritance edge, so the grant
    disjunct is what admits it — drop that disjunct and this fails. flag-neither
    has neither, and its absence shows the branch still rejects rather than
    admitting everything.
    """
    cypher = await cypher_ids(neo4j_settings, _cypher_from("App", "flag-app"))
    aql = await aql_ids(arango_settings, ["flag-app"], "apps")
    assert cypher == {"flag-rg", "flag-open", "flag-granted"}, sorted(cypher)
    assert cypher == aql, f"flag branches diverge between backends: {cypher} vs {aql}"


async def test_a_restricted_node_is_refused_on_a_grant_alone(
    loaded_graph, neo4j_settings, arango_settings
) -> None:
    """RESTRICTED against STRICT: same grant, same missing inheritance edge,
    opposite outcome.

    flag-granted is STRICT and admitted by its grant alone. flag-restricted is
    identical except for accessRule, and RESTRICTED demands inheritance *and* a
    grant, so it must be refused. Both halves are asserted because either alone
    passes vacuously: if everything were refused the first assertion still
    holds, and it is the contrast that pins the conjunct. Weaken the RESTRICTED
    AND to an OR -- the classic leak -- and this is what catches it.
    """
    cypher = await cypher_ids(neo4j_settings, _cypher_from("App", "flag-app"))
    aql = await aql_ids(arango_settings, ["flag-app"], "apps")
    assert "flag-restricted" not in cypher, (
        "RESTRICTED needs inheritance AND a grant; a grant alone admitted it"
    )
    assert "flag-granted" in cypher, (
        "the STRICT node must still be admitted, or the pair proves nothing about the difference"
    )
    assert cypher == aql, f"RESTRICTED handling diverges between backends: {cypher} vs {aql}"


async def test_hidden_children_are_pruned(
    loaded_graph, neo4j_settings, arango_settings
) -> None:
    """A hideChildren group lists nothing beneath it, the
    same way on both engines.

    The exact set matters more than the two absences: the hidden *group* must
    still be listed, and asserting only what is missing would hold just as well
    if the traversal returned nothing at all. ex-live-child is absent because
    the deletion test is a per-hop conjunct, so ex-deleted ends that branch
    rather than merely being filtered from the output.
    """
    cypher = await cypher_ids(neo4j_settings, _cypher_from("App", "ex-app"))
    aql = await aql_ids(arango_settings, ["ex-app"], "apps")
    assert cypher == {"ex-rg1", "ex-hidden", "ex-stub", "ex-under-stub"}, sorted(cypher)
    assert cypher == aql, f"hideChildren handling diverges: {cypher} vs {aql}"
    assert "ex-message" not in cypher, "messages under a hidden channel must not surface"
    assert "ex-deleted" not in cypher, "deleted records are never returned"


_SEEDED = """
    UNWIND $seeds AS seedId
    MATCH (root {{id: seedId}})
          ((p)-[r:NODE_RELATION]->(c) WHERE {rule})+ (n)
    RETURN DISTINCT n.id AS id
    """


async def test_record_group_level_declaration_covers_only_its_own_subtree(
    loaded_graph, neo4j_settings, arango_settings
) -> None:
    """The declaration is a separate pass, not a per-hop term.

    At the hop dec-rg2 -> dec-r3 the predicate would have to know that an
    *ancestor* carries RECORD_GROUP_LEVEL, and neither a QPP nor PRUNE carries
    ancestor state — which is why the admitted declared group is re-entered
    with checks off instead.

    dec-rg4 is the control that gives the case its teeth: it is an undeclared
    sibling under the same App, so a declaration leaking to app scope would
    pull it in.
    """
    normal = await cypher_ids(neo4j_settings, _cypher_from("App", "dec-rgl-app"))
    assert normal == {"dec-rg2"}, (
        f"only the granted group is reachable under the normal rule: {sorted(normal)}"
    )

    declared_cypher = await cypher_ids(
        neo4j_settings, _SEEDED.format(rule=PER_HOP_RULE),
        seeds=["dec-rg2"], skipChecks=True,
    )
    declared_aql = await aql_ids(
        arango_settings, ["dec-rg2"], "recordGroups", skip_checks=True,
    )

    assert declared_cypher == {"dec-r2", "dec-rg3", "dec-r3"}, sorted(declared_cypher)
    assert declared_cypher == declared_aql, "the declaration pass diverges"
    assert "dec-rg4" not in declared_cypher, (
        "an undeclared sibling must not be swept in by another group's declaration"
    )


async def test_shared_with_me_gives_a_record_a_second_ancestry(
    loaded_graph, neo4j_settings, arango_settings
) -> None:
    """One accessible path is enough.

    swm-y is strict and its drive folder (swm-f2) is unreachable, so it is
    admitted only through the Shared with Me parent — the case that shows a
    strict node needs *an* accessible ancestry path, not all of them. swm-f2
    itself stays hidden, which is what proves the walk is not simply admitting
    everything under the App.
    """
    cypher = await cypher_ids(neo4j_settings, _cypher_from("App", "swm-app"))
    aql = await aql_ids(arango_settings, ["swm-app"], "apps")

    assert cypher == {"swm-drive", "swm-inbox", "swm-f1", "swm-x", "swm-y"}, sorted(cypher)
    assert cypher == aql, f"Shared with Me diverges between backends: {cypher} vs {aql}"
    assert "swm-f2" not in cypher, "the unreachable drive folder must stay hidden"
