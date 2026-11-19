"""Neo4j index hints go through ``_execute_hinted``.

A ``USING INDEX`` on an index that is missing or still populating is an error
where the server sets ``dbms.cypher.hints_error``. Each hint is therefore a
named ``*_HINT`` constant whose query has an unhinted twin, run when the hinted
one is refused.
"""

from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from neo4j.exceptions import ClientError, Neo4jError

from app.services.graph_db.neo4j import neo4j_provider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def test_every_index_hint_is_a_named_constant() -> None:
    """An inline ``USING INDEX`` has no unhinted twin to fall back on."""
    tree = ast.parse(inspect.getsource(neo4j_provider))
    named = {
        id(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and all(isinstance(t, ast.Name) and t.id.endswith("_HINT") for t in node.targets)
    }
    inline = sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and "USING INDEX" in node.value and id(node) not in named
    )
    assert not inline, f"USING INDEX outside a *_HINT constant at lines {inline}"


def _provider(execute: AsyncMock) -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = MagicMock()
    provider.client.execute_query = execute
    return provider


def _error(code: str) -> Neo4jError:
    return Neo4jError._hydrate_neo4j(code=code, message="refused")


class TestPageRecordIdsByType:
    async def test_a_missing_index_falls_back_to_the_unhinted_page(self) -> None:
        execute = AsyncMock(side_effect=[_error("Neo.ClientError.Schema.IndexNotFound"), [{"id": "r1"}, {"id": "r2"}]])
        provider = _provider(execute)
        assert await provider.page_record_ids_by_type("org-1", ["MAIL"], after_key="r0", limit=2) == ["r1", "r2"]
        hinted, unhinted = (call.args[0] for call in execute.await_args_list)
        assert "USING INDEX" in hinted
        assert "USING INDEX" not in unhinted
        assert execute.await_args_list[1].kwargs["parameters"] == execute.await_args_list[0].kwargs["parameters"]
        provider.logger.warning.assert_called_once()

    async def test_the_hint_is_used_when_the_index_is_there(self) -> None:
        execute = AsyncMock(return_value=[{"id": "r1"}])
        assert await _provider(execute).page_record_ids_by_type("org-1", ["MAIL"]) == ["r1"]
        assert execute.await_count == 1
        assert "USING INDEX record:Record(orgId, id)" in execute.await_args.args[0]

    async def test_any_other_refusal_is_raised(self) -> None:
        with pytest.raises(ClientError):
            await _provider(AsyncMock(side_effect=_error("Neo.ClientError.Statement.SyntaxError"))).page_record_ids_by_type(
                "org-1", ["MAIL"],
            )
