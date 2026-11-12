"""FilterExpression to SQL, table-driven over every filter shape the callers build."""

from __future__ import annotations

import dataclasses

import pytest

from app.services.vector_db.models import FieldCondition as F
from app.services.vector_db.models import FilterExpression as E
from app.services.vector_db.pgvector.sql_filters import (
    compile_filter,
    single_org,
    values_of,
)

ORG = "a" * 24
MEMBER = ("EXISTS (SELECT 1 FROM retrieval.group_members m WHERE m.org_id = $1 AND m.collection_id = $2 AND m.group_key = p.group_key "
          "AND m.kind = {kind} AND m.member_id = ANY(${n}::text[]))")
ORG_COND = "(p.org_id = ANY($5::text[]))"

CASES = {
    "org only": (E(must=[F("metadata.orgId", ORG)]), ORG_COND, [[ORG]]),
    "one record (purge, reindex)": (
        E(must=[F("metadata.orgId", ORG), F("metadata.virtualRecordId", "r1")]),
        f"{ORG_COND} AND (p.vrid = ANY($6::text[]))", [[ORG], ["r1"]]),
    "blocks of a record (reconcile)": (
        E(must=[F("metadata.orgId", ORG), F("metadata.virtualRecordId", "r1"), F("metadata.blockId", values=["b1", "b2"])]),
        f"{ORG_COND} AND (p.vrid = ANY($6::text[])) AND (p.block_id = ANY($7::text[]))", [[ORG], ["r1"], ["b1", "b2"]]),
    "legacy path: every accessible vrid": (
        E(must=[F("metadata.orgId", ORG), F("metadata.virtualRecordId", values=["r1", "r2", "r3"])]),
        f"{ORG_COND} AND (p.vrid = ANY($6::text[]))", [[ORG], ["r1", "r2", "r3"]]),
    "container mode: tool vrids ANDed with the membership arms": (
        E(must=[F("metadata.orgId", ORG), F("metadata.virtualRecordId", values=["t1"])],
          should=[F("connectorIds", values=["c1", "c2"]), F("recordGroupIds", values=["g1"]), F("rootRecordGroupIds", values=["x1"]),
                  F("metadata.virtualRecordId", values=["direct"])]),
        f"{ORG_COND} AND (p.vrid = ANY($6::text[])) AND (({MEMBER.format(kind=1, n=7)}) OR ({MEMBER.format(kind=2, n=8)}) OR "
        f"({MEMBER.format(kind=3, n=9)}) OR (p.vrid = ANY($10::text[])))", [[ORG], ["t1"], ["c1", "c2"], ["g1"], ["x1"], ["direct"]]),
    "single owner (purge of a connector)": (
        E(must=[F("metadata.orgId", ORG), F("connectorIds", "c1", values_count_lte=1)]),
        f"{ORG_COND} AND ({MEMBER.format(kind=1, n=6)} AND cardinality(coalesce(g.connector_ids, '{{}}')) <= $7::int)", [[ORG], ["c1"], 1]),
    "a bound alone": (
        E(must=[F("metadata.orgId", ORG), F("recordGroupIds", values_count_lte=3)]),
        f"{ORG_COND} AND (cardinality(coalesce(g.record_group_ids, '{{}}')) <= $6::int)", [[ORG], 3]),
    "entities": (
        E(must=[F("metadata.orgId", ORG), F("metadata.entityType", "Person"), F("metadata.entityId", values=["e1", "e2"]),
                F("metadata.level", 2), F("metadata.kind", "alias")]),
        f"{ORG_COND} AND (p.ent_type = ANY($6::text[])) AND (p.ent_id = ANY($7::text[])) AND (p.ent_level = ANY($8::text[])) AND "
        "(p.ent_kind = ANY($9::text[]))", [[ORG], ["Person"], ["e1", "e2"], ["2"], ["alias"]]),
    "must_not keeps points that lack the field": (
        E(must=[F("metadata.orgId", ORG)], must_not=[F("metadata.virtualRecordId", "r9"), F("connectorIds", "c1")]),
        f"{ORG_COND} AND NOT coalesce((p.vrid = ANY($6::text[])), false) AND NOT coalesce(({MEMBER.format(kind=1, n=7)}), false)",
        [[ORG], ["r9"], ["c1"]]),
    "another payload key": (
        E(must=[F("metadata.orgId", ORG), F("metadata.isBlock", True)]),
        f"{ORG_COND} AND (pt.payload #>> $6::text[] = ANY($7::text[]))", [[ORG], ["metadata", "isBlock"], ["true"]]),
    "an empty value list matches nothing": (E(must=[F("metadata.orgId", ORG), F("metadata.virtualRecordId", values=[])]), f"{ORG_COND} AND (false)", [[ORG]]),
}


@pytest.mark.parametrize("name", list(CASES))
def test_compiled_sql_and_binds(name) -> None:
    expr, where, params = CASES[name]
    got = compile_filter(expr, first_param=5)
    assert got.where == where
    assert got.params == params


@pytest.mark.parametrize("name", list(CASES))
def test_no_value_is_ever_inlined(name) -> None:
    expr, _, _ = CASES[name]
    got = compile_filter(expr, first_param=5)
    for cond in [*expr.must, *expr.should, *expr.must_not]:
        for v in values_of(cond) or []:
            if len(v) > 1:                                    # a one-letter value could occur in SQL keywords by chance
                assert v not in got.where


def test_joins_follow_the_conditions() -> None:
    plain = compile_filter(CASES["one record (purge, reindex)"][0], first_param=5)
    assert (plain.needs_group, plain.needs_text) == (False, False)
    assert compile_filter(CASES["a bound alone"][0], first_param=5).needs_group
    assert compile_filter(CASES["another payload key"][0], first_param=5).needs_text


def test_first_param_shifts_every_placeholder() -> None:
    got = compile_filter(E(must=[F("metadata.virtualRecordId", "r1"), F("connectorIds", "c1")]), first_param=3)
    assert "$3::text[]" in got.where and "$4::text[]" in got.where and "$5" not in got.where


def test_a_bound_only_belongs_in_must() -> None:
    for expr in (E(should=[F("connectorIds", values_count_lte=1)]), E(must_not=[F("connectorIds", values_count_lte=1)])):
        with pytest.raises(ValueError, match="only supported in the MUST clause"):
            compile_filter(expr, first_param=3)
    with pytest.raises(ValueError, match="membership arrays"):
        compile_filter(E(must=[F("metadata.virtualRecordId", values_count_lte=1)]), first_param=3)
    with pytest.raises(ValueError, match="neither a value nor"):
        compile_filter(E(must=[F("metadata.virtualRecordId")]), first_param=3)


def test_the_org_is_the_single_must_value() -> None:
    assert single_org(E(must=[F("metadata.orgId", ORG)])) == ORG
    assert single_org(E(must=[F("metadata.orgId", values=[ORG])])) == ORG
    assert single_org(E(must=[F("metadata.orgId", values=[ORG, "b" * 24])])) is None        # a list is not an org
    assert single_org(E(should=[F("metadata.orgId", ORG)])) is None                          # only MUST pins the org
    assert single_org(E(must_not=[F("metadata.orgId", ORG)])) is None
    assert single_org(E(must=[F("metadata.virtualRecordId", "r1")])) is None


@pytest.mark.parametrize("n", [0, 2, 3])
def test_a_min_should_match_the_sql_cannot_honour_is_refused(n) -> None:
    from app.services.vector_db.pgvector.errors import PgVectorUnsupportedError

    expr = E(must=[F("metadata.orgId", ORG)], should=[F("metadata.virtualRecordId", "r1"), F("metadata.virtualRecordId", "r2")], min_should_match=n)
    with pytest.raises(PgVectorUnsupportedError, match="min_should_match"):
        compile_filter(expr, first_param=5)
    for ok in (None, 1):
        compile_filter(dataclasses.replace(expr, min_should_match=ok), first_param=5)
