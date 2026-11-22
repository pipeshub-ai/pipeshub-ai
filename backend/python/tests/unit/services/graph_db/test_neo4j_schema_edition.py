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


def _provider(edition: str | Exception, refuse: tuple[str, ...] = ()) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())

    async def run(query: str, parameters=None, **_) -> list[dict]:
        if "dbms.components" in query:
            if isinstance(edition, Exception):
                raise edition
            return [{"edition": edition}]
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
