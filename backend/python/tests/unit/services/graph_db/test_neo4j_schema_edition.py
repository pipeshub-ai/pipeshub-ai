"""Schema setup on Neo4j Community Edition.

Property existence constraints need Enterprise Edition. On Community every one
was sent anyway, Neo4j refused each and logged the refusal as an error (about
100 per service start), and the provider still logged "Created N constraints".
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

EXISTENCE = "IS NOT NULL"


def _provider(
    edition: str | Exception, refuse: tuple[str, ...] = (), existing: tuple[dict, ...] | Exception = (),
) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())

    async def run(query: str, parameters=None, **_) -> list[dict]:
        if "dbms.components" in query:
            if isinstance(edition, Exception):
                raise edition
            return [{"edition": edition}]
        if query.startswith("SHOW CONSTRAINTS") and "PROPERTY_EXISTENCE" in query:
            if isinstance(existing, Exception):
                raise existing
            return list(existing)
        if "RETURN count(key) AS keys" in query:
            return [{"keys": 0}]
        if any(marker in query for marker in refuse):
            raise RuntimeError("refused")
        return []

    p.client = MagicMock()
    p.client.execute_query = AsyncMock(side_effect=run)
    p.heal_taxonomy_alias_nodes = AsyncMock()
    p._initialize_departments = AsyncMock()
    return p


def _existence_constraints_sent(p: Neo4jProvider) -> list[str]:
    return [
        c.args[0] for c in p.client.execute_query.await_args_list
        if c.args[0].startswith("CREATE CONSTRAINT") and EXISTENCE in c.args[0]
    ]


def _infos(p: Neo4jProvider) -> list[str]:
    return [c.args[0] % c.args[1:] if len(c.args) > 1 else c.args[0] for c in p.logger.info.call_args_list]


async def test_community_edition_is_never_sent_an_existence_constraint() -> None:
    p = _provider("community")

    assert await p.ensure_schema()

    assert _existence_constraints_sent(p) == []
    assert any("Community Edition" in line for line in _infos(p))
    assert p._generate_required_field_constraints(), "the registry does have required fields to skip"


@pytest.mark.parametrize("edition", ["enterprise", RuntimeError("procedure not found")], ids=["enterprise", "unknown"])
async def test_enterprise_or_an_unknown_edition_gets_them(edition: str | Exception) -> None:
    p = _provider(edition)

    assert await p.ensure_schema()

    assert len(_existence_constraints_sent(p)) == len(p._generate_required_field_constraints())


async def test_the_log_counts_what_is_in_place_not_what_was_sent() -> None:
    p = _provider("enterprise", refuse=(EXISTENCE,))
    total = len(p._generate_required_field_constraints())

    assert await p.ensure_schema()

    assert f"✅ 0 of {total} property existence constraints in place" in _infos(p)
    unique = len(p._generate_unique_id_constraints())
    assert f"✅ {unique} of {unique} unique id constraints in place" in _infos(p)


async def test_a_refused_statement_is_logged_and_the_rest_still_run() -> None:
    p = _provider("community", refuse=("FOR (n:Records) REQUIRE n.id IS UNIQUE",))
    p._generate_unique_id_constraints = MagicMock(return_value=[
        "CREATE CONSTRAINT a IF NOT EXISTS FOR (n:Records) REQUIRE n.id IS UNIQUE",
        "CREATE CONSTRAINT b IF NOT EXISTS FOR (n:Files) REQUIRE n.id IS UNIQUE",
    ])

    assert await p.ensure_schema()

    sent = [c.args[0] for c in p.client.execute_query.await_args_list]
    assert any("FOR (n:Files)" in q for q in sent)
    assert "✅ 1 of 2 unique id constraints in place" in _infos(p)
    level, *_ = p.logger.log.call_args_list[0].args
    assert level == logging.WARNING


def _existing(name: str, label: str, field: str) -> dict:
    return {"name": name, "labels": [label], "properties": [field]}


def _drops(p: Neo4jProvider) -> list[str]:
    return [c.args[0] for c in p.client.execute_query.await_args_list if c.args[0].startswith("DROP CONSTRAINT")]


async def test_an_existence_constraint_the_schema_no_longer_requires_is_dropped_first() -> None:
    """A person a source names without an email is stored without one. A database first set up
    when email was required kept the constraint, and refused every sync that met such a person."""
    p = _provider("enterprise", existing=(_existing("person_email_exists", "Person", "email"),))

    assert await p.ensure_schema()

    assert _drops(p) == ["DROP CONSTRAINT `person_email_exists` IF EXISTS"]
    sent = [c.args[0] for c in p.client.execute_query.await_args_list]
    assert sent.index(_drops(p)[0]) < sent.index(_existence_constraints_sent(p)[0])


async def test_a_required_field_keeps_its_constraint() -> None:
    wanted = Neo4jProvider._generate_required_field_constraints(_provider("enterprise"))
    assert any(q.startswith("CREATE CONSTRAINT person_createdAtTimestamp_exists ") for q in wanted)
    p = _provider("enterprise", existing=(
        _existing("person_createdAtTimestamp_exists", "Person", "createdAtTimestamp"),
    ))

    assert await p.ensure_schema()

    assert _drops(p) == []


@pytest.mark.parametrize("row", [
    _existing("my_own_rule", "Person", "email"),
    _existing("customer_email_exists", "Customer", "email"),
    {"name": "person_email_exists", "labels": ["Person"], "properties": ["email", "orgId"]},
    {"name": "person_email_exists", "labels": ["Person", "User"], "properties": ["email"]},
], ids=["another name", "a label that is not ours", "several properties", "several labels"])
async def test_a_constraint_this_provider_did_not_create_is_left_alone(row: dict) -> None:
    p = _provider("enterprise", existing=(row,))

    assert await p.ensure_schema()

    assert _drops(p) == []


async def test_community_edition_is_not_asked_for_them() -> None:
    p = _provider("community", existing=(_existing("person_email_exists", "Person", "email"),))

    assert await p.ensure_schema()

    assert _drops(p) == []


async def test_a_listing_that_fails_does_not_stop_the_schema() -> None:
    p = _provider("enterprise", existing=RuntimeError("no privilege"))

    assert await p.ensure_schema()

    assert _drops(p) == []
    assert _existence_constraints_sent(p)
