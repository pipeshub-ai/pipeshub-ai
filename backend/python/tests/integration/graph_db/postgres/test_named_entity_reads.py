"""Named-entity reads on PostgreSQL, against a real database, as ``Neo4jProvider`` (graph_ops) answers them.

Both directions for every read that decides which records, mentions or values come back: nothing of another org, a trashed record or a record outside ``within``
(and nothing at all for an empty ``within``), and everything of the asked ids, kinds and ranges. A fuzz test compares a restricted answer with the unrestricted one.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.modules.named_entities.graph_ops import NamedEntityQuery

from .test_grants import failing, in_org
from .test_named_entity_writes import orgs as _orgs  # noqa: F401

if TYPE_CHECKING:
    from .test_named_entity_writes import Org

pytestmark = pytest.mark.integration


@pytest.fixture
def orgs(request: pytest.FixtureRequest) -> tuple[Org, Org]:
    return request.getfixturevalue("_orgs")


@pytest.fixture
def w(orgs: tuple[Org, Org]) -> Org:
    return orgs[0]


async def put(w: Org, *names: str, **over: object) -> None:
    await w.p.create_named_entities_if_absent([w.ne(n, **over) for n in names])


# ---- nodes -----------------------------------------------------------------------------------------------------------------------------

async def test_find_matches_a_key_or_an_alias_of_the_kind_in_this_org(w: Org, orgs: tuple[Org, Org]) -> None:
    await put(w, "Acme", normalizedAliases=["acme", "acme inc"])
    await w.p.create_named_entities_if_absent([w.ne("Zed", kind="person", normKey="acme"), w.ne("Old", mergedInto="Acme", normKey="oldco", normalizedAliases=["oldco"])])
    assert [n["id"] for n in await w.p.find_named_entities(w.org, "org", ["acme"])] == ["Acme"]
    assert [n["id"] for n in await w.p.find_named_entities(w.org, "org", ["acme inc"])] == ["Acme"]          # by alias
    assert [n["id"] for n in await w.p.find_named_entities(w.org, "org", ["oldco", "nobody"])] == ["Old"]    # a merged node too
    assert [n["id"] for n in await w.p.find_named_entities(w.org, "person", ["acme"])] == ["Zed"]
    assert await w.p.find_named_entities(w.org, "org", []) == [] and await w.p.find_named_entities(w.org, "org", ["x"]) == []
    assert await w.p.find_named_entities(orgs[1].org, "org", ["acme"]) == []
    with in_org(orgs[1].org):
        assert await orgs[1].p.find_named_entities(w.org, "org", ["acme"]) == []        # this org's id, asked from another org
    assert (await w.p.find_named_entities(w.org, "org", ["acme"]))[0]["name"] == "Acme"


async def test_by_ids_gives_the_projection_of_the_orgs_nodes_once_each(w: Org, orgs: tuple[Org, Org]) -> None:
    await put(w, "A", "B")
    got = await w.p.get_named_entities_by_ids(w.org, ["B", "A", "B", "nobody"])
    assert got == [{"id": "A", "kind": "org", "name": "A", "normKey": "a", "normalizedAliases": ["a"]}, {"id": "B", "kind": "org", "name": "B", "normKey": "b", "normalizedAliases": ["b"]}]
    assert await w.p.get_named_entities_by_ids(w.org, []) == [] and await w.p.get_named_entities_by_ids(orgs[1].org, ["A"]) == []


async def test_by_alias_leaves_out_the_nodes_own_key_merged_nodes_and_other_kinds(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.p.create_named_entities_if_absent([w.ne("A", normKey="pinecrest hospitality", normalizedAliases=["pinecrest hospitality", "pinecrest"]),
                                               w.ne("M", normKey="m", normalizedAliases=["m", "pinecrest"], mergedInto="A"),
                                               w.ne("P", kind="person", normKey="p", normalizedAliases=["p", "pinecrest"]),
                                               w.ne("S", normKey="pinecrest", normalizedAliases=["pinecrest"])])
    rows = await w.p.find_named_entities_by_alias(w.org, [("org", "pinecrest")])
    assert rows == [{"key": "pinecrest", "id": "A", "kind": "org", "name": "A", "normKey": "pinecrest hospitality"}]
    await w.p.create_named_entities_if_absent([w.ne(f"X{i}", normKey=f"x{i}", normalizedAliases=[f"x{i}", "shared"]) for i in range(5)])
    assert len(await w.p.find_named_entities_by_alias(w.org, [("org", "shared"), ("org", "shared"), ("org", "pinecrest")], limit=2)) == 3      # at most 2 per pair, a repeated pair once
    assert len(await w.p.find_named_entities_by_alias(w.org, [("org", "shared")], limit=0)) == 1                                              # at least one
    assert await w.p.find_named_entities_by_alias(w.org, []) == [] and await w.p.find_named_entities_by_alias(orgs[1].org, [("org", "pinecrest")]) == []


async def test_by_key_prefix_is_a_name_followed_by_more_words(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.p.create_named_entities_if_absent([*(w.ne(k, normKey=k) for k in ("sam", "sam lee", "sam lee jr", "samsung", "other sam")), w.ne("P", kind="person", normKey="sam kim")])
    rows = await w.p.find_named_entities_by_key_prefix(w.org, [("org", "sam")])
    assert [(r["key"], r["normKey"]) for r in rows] == [("sam", "sam lee"), ("sam", "sam lee jr")]
    assert [r["normKey"] for r in await w.p.find_named_entities_by_key_prefix(w.org, [("org", "sam")], limit=1)] == ["sam lee"]
    assert [r["normKey"] for r in await w.p.find_named_entities_by_key_prefix(w.org, [("person", "sam")])] == ["sam kim"]
    assert await w.p.find_named_entities_by_key_prefix(w.org, []) == [] and await w.p.find_named_entities_by_key_prefix(orgs[1].org, [("org", "sam")]) == []
    await w.p.create_named_entities_if_absent([w.ne("pct", normKey="100% sure"), w.ne("us", normKey="a_b c")])
    assert len(await w.p.find_named_entities_by_key_prefix(w.org, [("org", "100%")])) == 1 and await w.p.find_named_entities_by_key_prefix(w.org, [("org", "a%b")]) == []   # no wildcards


async def test_query_pages_by_id_and_narrows_by_kind_and_name_prefix(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.p.create_named_entities_if_absent([w.ne("a1", name="Alpha"), w.ne("a2", name="alpine"), w.ne("b1", name="Beta"), w.ne("p1", kind="person", name="Alpha Person")])
    page = await w.p.query_named_entities(w.org, NamedEntityQuery(limit=2))
    assert [e["id"] for e in page["entities"]] == ["a1", "a2"] and page["cursor"] == "2"
    page = await w.p.query_named_entities(w.org, NamedEntityQuery(limit=2, cursor="2"))
    assert [e["id"] for e in page["entities"]] == ["b1", "p1"] and page["cursor"] == "4"
    assert (await w.p.query_named_entities(w.org, NamedEntityQuery(limit=2, cursor="4"))) == {"entities": [], "cursor": ""}
    assert [e["id"] for e in (await w.p.query_named_entities(w.org, NamedEntityQuery(name_prefix=" AL ")))["entities"]] == ["a1", "a2", "p1"]       # trimmed and case-folded
    assert [e["id"] for e in (await w.p.query_named_entities(w.org, NamedEntityQuery(kinds=["person"], name_prefix="alpha")))["entities"]] == ["p1"]
    await w.p.create_named_entities_if_absent([w.ne(f"z{i:03}", name=f"zz{i}") for i in range(205)])
    big = await w.p.query_named_entities(w.org, NamedEntityQuery(limit=1000))
    assert len(big["entities"]) == 200 and big["cursor"] == "200"                                                                       # a limit above 200 is 200
    assert (await w.p.query_named_entities(orgs[1].org, NamedEntityQuery()))["entities"] == []
    with pytest.raises(ValueError):
        await w.p.query_named_entities(w.org, NamedEntityQuery(cursor="x"))


async def test_a_records_entities_carry_the_edges_count_and_blocks_then_its_values(w: Org, orgs: tuple[Org, Org]) -> None:
    await put(w, "A", "B")
    await w.record("r1")
    await w.record("r2")
    await w.mention("r1", "A", mentionCount=3, blockIndexes=[0, 2])
    await w.mention("r1", "B")
    await w.mention("r2", "A")
    await w.p.replace_named_entity_values("r1", [w.vm("r1", "d1")])
    got = await w.p.get_named_entities_for_record("r1")
    assert [(e["id"], e.get("mentionCount"), e.get("blockIndexes")) for e in got[:2]] == [("A", 3, [0, 2]), ("B", 1, None)]
    assert got[2]["recordId"] == "r1" and len(got) == 3
    assert await w.p.get_named_entities_for_record("nobody") == []
    await w.mention("ghost", "A")
    assert await w.p.get_named_entities_for_record("ghost") == []                                                                        # a record that is not there has none
    with in_org(orgs[1].org):
        assert await orgs[1].p.get_named_entities_for_record("r1") == []


# ---- mentions --------------------------------------------------------------------------------------------------------------------------

async def mention_world(w: Org, other: Org) -> None:
    await put(w, "A", "B")
    await w.p.create_named_entities_if_absent([w.ne("M", mergedInto="A")])
    for r in ("r1", "r2"):
        await w.record(r)
    await w.mention("r1", "A", extractedNames=["a"], blockIndexes=[1], mentionCount=2)
    await w.mention("r1", "M", extractedNames=["m"])
    await w.mention("r2", "B", extractedNames=["b"])
    await w.mention("r2", "gone", extractedNames=["x"])                      # a node that no longer exists
    await w.mention("ghost", "A", extractedNames=["y"])                       # a record that does not exist
    await w.mention("r2", "A", extractedNames=["foreign"], orgId=other.org)   # an edge of another org


async def test_mentions_in_records_are_the_edges_between_the_given_records_and_entities(w: Org, orgs: tuple[Org, Org]) -> None:
    await mention_world(w, orgs[1])
    rows = await w.p.get_named_entity_mentions_in_records(w.org, ["A", "B", "M"], ["r1", "r2", "ghost", "r1"])
    assert rows == [{"recordId": "r1", "entityId": "A", "blockIndexes": [1], "extractedNames": ["a"], "mentionCount": 2},
                    {"recordId": "r1", "entityId": "M", "blockIndexes": None, "extractedNames": ["m"], "mentionCount": 1},
                    {"recordId": "r2", "entityId": "B", "blockIndexes": None, "extractedNames": ["b"], "mentionCount": 1}]
    assert [r["entityId"] for r in await w.p.get_named_entity_mentions_in_records(w.org, ["B"], ["r1", "r2"])] == ["B"]
    assert await w.p.get_named_entity_mentions_in_records(w.org, ["gone"], ["r2"]) == []                                                # an edge to a node that is gone
    assert await w.p.get_named_entity_mentions_in_records(w.org, [], ["r1"]) == [] and await w.p.get_named_entity_mentions_in_records(w.org, ["A"], []) == []
    assert await w.p.get_named_entity_mentions_in_records(orgs[1].org, ["B"], ["r1", "r2"]) == []


async def test_mentions_from_records_leave_out_merged_nodes_missing_ones_and_other_orgs_edges(w: Org, orgs: tuple[Org, Org]) -> None:
    await mention_world(w, orgs[1])
    rows = await w.p.get_named_entity_mentions_from_records(w.org, ["r1", "r2", "ghost"])
    assert rows == [{"recordId": "r1", "entityId": "A", "kind": "org", "extractedNames": ["a"]}, {"recordId": "r2", "entityId": "B", "kind": "org", "extractedNames": ["b"]}]
    assert await w.p.get_named_entity_mentions_from_records(w.org, []) == [] and await w.p.get_named_entity_mentions_from_records(orgs[1].org, ["r1"]) == []
    assert [r["recordId"] for r in await w.p.get_named_entity_mentions_from_records(w.org, ["r2"])] == ["r2"]


# ---- values ----------------------------------------------------------------------------------------------------------------------------

DAY = 86_400_000
NODATE = {"startMs": None, "endMs": None}      # a null is not stored


async def value_world(w: Org) -> None:
    for r in ("r1", "r2", "r3"):
        await w.record(r)
    await w.p.replace_named_entity_values("r1", [w.vm("r1", "may1", startMs=10 * DAY, endMs=11 * DAY, name="1 May"), w.vm("r1", "may", startMs=10 * DAY, endMs=40 * DAY, name="May"),
                                                 w.vm("r1", "usd", kind="currency", name="$5", amountFloat=5.0, currency="USD", **NODATE),
                                                 w.vm("r1", "pct", kind="percentage", name="50%", numericValue=50.0, **NODATE)])
    await w.p.replace_named_entity_values("r2", [w.vm("r2", "jun", startMs=50 * DAY, endMs=51 * DAY, name="1 June"),
                                                 w.vm("r2", "usd", kind="currency", name="$500", amountFloat=400.0, amountHighFloat=500.0, currency="USD", **NODATE),
                                                 w.vm("r2", "eur", kind="currency", name="E7", amountFloat=7.0, currency="EUR", **NODATE),
                                                 w.vm("r2", "kg", kind="quantity", name="3 kg", siValue=3.0, numericValue=50.0, dimension="mass", **NODATE)])
    await w.p.replace_named_entity_values("r3", [w.vm("r3", "bad", startMs="soon", endMs=None, name="bad", numericValue="x")])


async def test_date_spans_name_the_records_with_a_date_overlapping_the_range(w: Org, orgs: tuple[Org, Org]) -> None:
    await value_world(w)
    got = await w.p.get_named_entity_date_spans(w.org, ["r1", "r2", "r3", "nobody", "r1"], 10 * DAY, 12 * DAY)
    assert got == {"r1": (True, DAY)}                                       # the month overlaps too; the day starts in the range: named, narrowest span
    got = await w.p.get_named_entity_date_spans(w.org, ["r1", "r2"], 20 * DAY, 21 * DAY)
    assert got == {"r1": (False, 30 * DAY)}                                  # only the month overlaps, and it is longer than the range and a day
    assert await w.p.get_named_entity_date_spans(w.org, ["r1", "r2"], 49 * DAY, 60 * DAY) == {"r2": (True, DAY)}
    assert await w.p.get_named_entity_date_spans(w.org, ["r1"], 100 * DAY, 101 * DAY) == {} and await w.p.get_named_entity_date_spans(w.org, [], 0, DAY) == {}
    assert await w.p.get_named_entity_date_spans(orgs[1].org, ["r1"], 10 * DAY, 12 * DAY) == {}
    assert await w.p.get_named_entity_date_spans(w.org, ["r3"], 0, 100 * DAY) == {}                  # a malformed date never matches and never raises
    await w.record("r4")
    await w.p.replace_named_entity_values("r4", [w.vm("r4", "early", startMs=int(19.5 * DAY), endMs=int(20.5 * DAY)), w.vm("r4", "long", startMs=int(20.5 * DAY), endMs=60 * DAY)])
    assert await w.p.get_named_entity_date_spans(w.org, ["r4"], 20 * DAY, 21 * DAY) == {"r4": (False, DAY)}      # one starts before the range, one is longer than it: neither names it
    assert await w.p.get_named_entity_date_spans(w.org, ["r1"], 10 * DAY, 10 * DAY + 3_600_000) == {"r1": (True, DAY)}      # a one-hour question still counts a whole-day value


async def test_values_in_records_name_the_values_that_satisfy_a_constraint_largest_first(w: Org, orgs: tuple[Org, Org]) -> None:
    await value_world(w)
    q = NamedEntityQuery(amount_min=6.0, amount_max=1000.0, currency="USD")
    assert await w.p.get_named_entity_values_in_records(w.org, q, ["r1", "r2"]) == {"r2": ["$500"]}                        # $5 is below the range; $500 is 400..500
    assert await w.p.get_named_entity_values_in_records(w.org, NamedEntityQuery(amount_min=1.0, amount_max=1000.0), ["r1", "r2"]) == {"r1": ["$5"], "r2": ["$500", "E7"]}
    assert await w.p.get_named_entity_values_in_records(w.org, NamedEntityQuery(amount_min=1.0, amount_max=1000.0), ["r2"], per_record=1) == {"r2": ["$500"]}
    dates = await w.p.get_named_entity_values_in_records(w.org, NamedEntityQuery(date_start_ms=0, date_end_ms=60 * DAY), ["r1", "r2"])
    assert dates == {"r1": ["1 May", "May"], "r2": ["1 June"]}                                                              # equal starts: by name
    assert await w.p.get_named_entity_values_in_records(w.org, NamedEntityQuery(quantity_min=1.0, quantity_max=5.0, dimension="mass"), ["r1", "r2"]) == {"r2": ["3 kg"]}
    assert await w.p.get_named_entity_values_in_records(w.org, NamedEntityQuery(amount_min=450.0, amount_max=1000.0, currency="USD"), ["r2"]) == {"r2": ["$500"]}      # the range reaches the high end
    assert await w.p.get_named_entity_values_in_records(w.org, NamedEntityQuery(percent_min=10.0, percent_max=60.0), ["r1", "r2"]) == {"r1": ["50%"]}      # r2's 3 kg has a numeric value of 50 too
    await w.record("r5")
    await w.p.replace_named_entity_values("r5", [w.vm("r5", "both", name="both", startMs=10 * DAY, endMs=11 * DAY, amountFloat=9.0, currency="USD")])
    assert await w.p.get_named_entity_values_in_records(w.org, NamedEntityQuery(date_start_ms=0, date_end_ms=60 * DAY, amount_min=1.0, amount_max=10.0), ["r5"]) == {"r5": ["both"]}      # two constraints, one name
    assert await w.p.get_named_entity_values_in_records(w.org, NamedEntityQuery(kinds=["percentage"]), ["r1", "r2"]) == {"r1": ["50%"]}
    assert await w.p.get_named_entity_values_in_records(w.org, NamedEntityQuery(kinds=["org"], name_prefix="x"), ["r1"]) == {}      # entity-node constraints are ignored
    assert await w.p.get_named_entity_values_in_records(w.org, None, ["r1"]) == {} and await w.p.get_named_entity_values_in_records(w.org, q, []) == {}
    assert await w.p.get_named_entity_values_in_records(orgs[1].org, q, ["r1", "r2"]) == {}
    assert await w.p.get_named_entity_values_in_records(w.org, q, ["r1"]) == {}                                                       # only the records asked for


# ---- the records of a filter -----------------------------------------------------------------------------------------------------------

async def filter_world(w: Org, other: Org) -> None:
    await put(w, "Acme", "Beta")
    await w.p.create_named_entities_if_absent([w.ne("Old", mergedInto="Acme")])
    await w.record("live", virtualRecordId="v1", connectorId="c1", recordType="FILE", sourceLastModifiedTimestamp=500, updatedAtTimestamp=9)
    await w.record("later", recordType="FILE", updatedAtTimestamp=77)
    await w.record("trashed", isDeleted=True)
    await w.record("placeholder", isPlaceholder=True)
    await w.record("foreign", orgId=other.org)
    for r in ("live", "later", "trashed", "placeholder", "foreign"):
        await w.mention(r, "Acme")
    await w.mention("later", "Beta")
    await w.mention("live", "Old")


async def hits(w: Org, *a: object, **k: object) -> tuple[list[str], bool]:
    r = await w.p.get_records_for_named_entities(w.org, *a, **k)
    return sorted(h["recordId"] for h in r["hits"]), r["truncated"]


async def test_records_for_entities_are_the_live_records_of_the_org_that_mention_them(w: Org, orgs: tuple[Org, Org]) -> None:
    await filter_world(w, orgs[1])
    assert await hits(w, ["Acme"]) == (["later", "live", "placeholder"], False)       # trashed and other-org records never come back; a placeholder is a record
    assert await hits(w, ["Beta"]) == (["later"], False)
    assert await hits(w, ["Acme", "Beta"]) == (["later", "live", "placeholder"], False)
    assert await hits(w, ["Old"]) == (["later", "live", "placeholder"], False)         # a merged id: its survivor's mentions count too
    assert await hits(w, ["nobody"]) == ([], False) and await hits(w, []) == ([], False) and await hits(w, None) == ([], False)
    rows = (await w.p.get_records_for_named_entities(w.org, ["Acme"]))["hits"]
    by = {h["recordId"]: h for h in rows}
    assert by["live"] == {"recordId": "live", "virtualRecordId": "v1", "connectorId": "c1", "recordType": "FILE", "sortTs": 500} and by["later"]["sortTs"] == 77
    assert (await w.p.get_records_for_named_entities(orgs[1].org, ["Acme"])) == {"hits": [], "truncated": False}


async def test_within_bounds_the_answer_to_the_records_given_and_an_empty_within_matches_nothing(w: Org, orgs: tuple[Org, Org]) -> None:
    await filter_world(w, orgs[1])
    assert await hits(w, ["Acme"], within=["live", "trashed", "foreign", "nobody"]) == (["live"], False)       # only live records of the org, only those given
    assert await hits(w, ["Acme"], within=[]) == ([], False)                                                  # not "all"
    assert await hits(w, ["Beta"], within=["live"]) == ([], False)
    assert await hits(w, ["Acme"], within=["live", "live"]) == (["live"], False)
    assert await hits(w, None, NamedEntityQuery(kinds=["org"]), within=["later", "trashed"]) == (["later"], False)
    assert (await w.p.get_records_for_named_entities(orgs[1].org, ["Acme"], within=["live"])) == {"hits": [], "truncated": False}


async def test_a_filter_too_broad_to_list_is_truncated_and_a_narrow_one_within_it_is_not(w: Org, orgs: tuple[Org, Org]) -> None:
    await put(w, "Acme", "Beta")
    for i in range(6):
        await w.record(f"r{i}")
        await w.mention(f"r{i}", "Acme")
    await w.mention("r1", "Beta")
    assert await hits(w, ["Acme"], limit=3) == ([], True)
    assert await hits(w, ["Acme", "Beta"], limit=3) == ([], True)                                           # one id list: its union is too broad
    assert await hits(w, ["Acme"], limit=6) == ([f"r{i}" for i in range(6)], False)
    assert await hits(w, ["Acme"], limit=3, within=["r0", "r1"]) == (["r0", "r1"], False)
    assert await hits(w, ["Acme"], limit=1, within=["r0", "r1"]) == ([], True)                              # within, but more than the cap
    assert await hits(w, ["Beta"], NamedEntityQuery(kinds=["org"]), limit=3) == (["r1"], False)
    await put(w, *(f"X{i}" for i in range(4)))
    assert await hits(w, None, NamedEntityQuery(kinds=["org"]), limit=3) == ([], True)                      # more entities of the kind than can be listed
    await w.p.create_named_entities_if_absent([w.ne(f"P{i}", kind="person") for i in range(5)])
    await w.mention("r0", "P0")
    assert await hits(w, None, NamedEntityQuery(kinds=["person"]), limit=3) == ([], True)                  # five nodes of the kind, though one record mentions them
    assert await hits(w, None, NamedEntityQuery(kinds=["person"]), limit=5) == (["r0"], False)
    await put(w, "G")
    for g in ("ghost1", "ghost2", "ghost3"):
        await w.mention(g, "G")
    await w.mention("r0", "G")
    assert await hits(w, ["G"], limit=2) == (["r0"], False)                                                # mentions by records that are gone do not count toward the cap


async def test_rows_of_trashed_or_missing_records_and_dangling_edges_do_not_count_or_answer(w: Org, orgs: tuple[Org, Org]) -> None:
    await w.record("a")
    await w.record("t", isDeleted=True)
    for r in ("a", "t", "m"):                                    # "m" names a record that is not in the graph
        await w.p.replace_named_entity_values(r, [w.vm(r, "usd", kind="currency", name="$5", amountFloat=5.0, currency="USD", startMs=None, endMs=None)])
    q = NamedEntityQuery(amount_min=1.0, amount_max=10.0)
    assert await hits(w, None, q, limit=1) == (["a"], False)                                   # the dead rows are not "too many"
    assert await hits(w, None, q, limit=1, within=["a", "t", "m"]) == (["a"], False)
    await put(w, "D")
    await w.mention("a", "D")
    await w.mention("t", "D")
    await w.p._db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'namedEntities' AND id = 'D'", w.org), write=True)
    assert await hits(w, ["D"]) == await hits(w, ["D"], within=["a", "t"]) == ([], False)     # an edge to an entity that is gone names nothing, either way


async def test_value_and_entity_constraints_must_both_hold_in_the_record(w: Org, orgs: tuple[Org, Org]) -> None:
    await value_world(w)
    await put(w, "Acme")
    await w.mention("r1", "Acme")
    await w.mention("r3", "Acme")
    q = NamedEntityQuery(amount_min=1.0, amount_max=1000.0)
    assert await hits(w, None, q) == (["r1", "r2"], False)
    assert await hits(w, ["Acme"], q) == (["r1"], False)
    assert await hits(w, ["Acme"], q, within=["r2", "r3"]) == ([], False)
    assert await hits(w, None, NamedEntityQuery(amount_min=6.0, amount_max=1000.0, currency="USD")) == (["r2"], False)
    assert await hits(w, None, NamedEntityQuery(kinds=["currency", "quantity"])) == (["r1", "r2"], False)
    assert await hits(w, None, NamedEntityQuery(date_start_ms=45 * DAY, date_end_ms=60 * DAY)) == (["r2"], False)
    assert await hits(w, None, NamedEntityQuery(date_start_ms=45 * DAY, date_end_ms=60 * DAY), within=["r1"]) == ([], False)
    assert (await w.p.get_records_for_named_entities("another-org", None, q)) == {"hits": [], "truncated": False}
    assert (await w.p.get_records_for_named_entities("another-org", None, q, limit=1)) == {"hits": [], "truncated": False}      # not "too broad" for rows of another org
    assert (await w.p.get_records_for_named_entities(w.org, None, q, limit=1))["truncated"] is True


async def test_a_restricted_answer_is_the_unrestricted_one_cut_to_the_records_given(w: Org, orgs: tuple[Org, Org]) -> None:
    """Over seeded random worlds: for any ``within`` the hits are the unrestricted hits that lie in it, never one more, never one less."""
    rng = random.Random(20261106)
    names = ["E0", "E1", "E2"]
    await put(w, *names)
    records = [f"q{i}" for i in range(12)]
    for r in records:
        trashed = rng.random() < 0.2
        await w.record(r, isDeleted=trashed, **({"orgId": orgs[1].org} if rng.random() < 0.1 else {}))
        for n in names:
            if rng.random() < 0.4:
                await w.mention(r, n)
        if rng.random() < 0.5:
            await w.p.replace_named_entity_values(r, [w.vm(r, "a", kind="currency", amountFloat=rng.choice([1.0, 50.0]), currency="USD", **NODATE)])
    filters = [(["E0"], None), (["E0", "E1"], None), (None, NamedEntityQuery(amount_min=10.0, amount_max=100.0)), (["E2"], NamedEntityQuery(amount_min=0.0, amount_max=100.0))]
    for ids, q in filters:
        full, truncated = await hits(w, ids, q)
        assert not truncated
        for _ in range(6):
            sub = rng.sample([*records, "nobody"], rng.randint(0, 8))
            got, cut = await hits(w, ids, q, within=sub)
            assert not cut and got == sorted(set(full) & set(sub)), (ids, sub)


# ---- failures --------------------------------------------------------------------------------------------------------------------------

async def test_a_failed_read_raises_and_answers_right_once_the_store_is_back(w: Org, orgs: tuple[Org, Org], monkeypatch: pytest.MonkeyPatch) -> None:
    await filter_world(w, orgs[1])
    await w.p.replace_named_entity_values("live", [w.vm("live", "d", startMs=1, endMs=2)])
    q = NamedEntityQuery(date_start_ms=0, date_end_ms=5)
    calls = lambda: (w.p.find_named_entities(w.org, "org", ["acme"]), w.p.get_named_entities_by_ids(w.org, ["Acme"]),   # noqa: E731
                     w.p.find_named_entities_by_alias(w.org, [("org", "x")]), w.p.find_named_entities_by_key_prefix(w.org, [("org", "x")]),
                     w.p.query_named_entities(w.org, NamedEntityQuery()), w.p.get_named_entities_for_record("live"),
                     w.p.get_named_entity_mentions_in_records(w.org, ["Acme"], ["live"]), w.p.get_named_entity_mentions_from_records(w.org, ["live"]),
                     w.p.get_named_entity_date_spans(w.org, ["live"], 0, 5), w.p.get_named_entity_values_in_records(w.org, q, ["live"]),
                     w.p.get_records_for_named_entities(w.org, ["Acme"]), w.p.get_records_for_named_entities(w.org, ["Acme"], within=["live"]),
                     w.p.get_records_for_named_entities(w.org, None, q))
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    for call in calls():
        with pytest.raises(RuntimeError, match="store down"):
            await call
    assert (await w.p.find_named_entities(w.org, "org", []), await w.p.get_named_entity_mentions_in_records(w.org, [], ["live"]),
            await w.p.get_named_entity_mentions_in_records(w.org, ["Acme"], [])) == ([], [], [])        # nothing to look for: the store is not asked
    monkeypatch.setattr(w.p, "_q", real)
    assert await hits(w, ["Acme"], within=["live"]) == (["live"], False) and await hits(w, None, q) == (["live"], False)       # nothing is cached as "none"
    assert len(await w.p.get_named_entities_for_record("live")) == 3 and (await w.p.get_named_entity_date_spans(w.org, ["live"], 0, 5)) == {"live": (True, 1)}


async def test_no_org_is_an_error_not_an_empty_answer(pg_provider) -> None:
    token = org_context.push(None)
    try:
        for call in (pg_provider.find_named_entities("o", "org", ["a"]), pg_provider.get_records_for_named_entities("o", ["a"]), pg_provider.get_named_entities_for_record("r"),
                     pg_provider.get_named_entity_date_spans("o", ["r"], 0, 1), pg_provider.query_named_entities("o", NamedEntityQuery())):
            with pytest.raises(org_context.MissingOrgContext):
                await call
    finally:
        org_context.pop(token)
