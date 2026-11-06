"""Unit tests for normalize_user_emails backfill script."""
from __future__ import annotations

import io
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.scripts.normalize_user_emails import (
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    build_parser,
    fetch_unnormalized_users,
    run_normalization,
)
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _lines(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_parser_requires_org_or_all_orgs() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])

    args_org = parser.parse_args(["--org", "org_1", "--apply", "--batch-size", "100"])
    assert args_org.org == "org_1"
    assert args_org.apply is True
    assert args_org.batch_size == 100

    args_all = parser.parse_args(["--all-orgs"])
    assert args_all.all_orgs is True
    assert args_all.apply is False
    assert args_all.batch_size == 200


@pytest.mark.asyncio
async def test_dry_run_scans_and_updates_nothing() -> None:
    graph = MagicMock()
    candidates = [
        {"id": "u1", "orgId": "o1", "email": "Alice@Acme.COM", "newEmail": "alice@acme.com"},
        {"id": "u2", "orgId": "o1", "email": " Bob@acme.com ", "newEmail": "bob@acme.com"},
    ]
    graph.fetch_unnormalized_users = AsyncMock(side_effect=[candidates, []])
    graph.batch_upsert_nodes = AsyncMock()

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", apply=False, logger=logger, out=out, batch_size=10)
    assert code == EXIT_SUCCESS
    graph.batch_upsert_nodes.assert_not_awaited()

    lines = _lines(out)
    assert any(item.get("status") == "progress" and item.get("applied") is False for item in lines)
    completed = lines[-1]
    assert completed["status"] == "completed"
    assert completed["total_scanned"] == 2
    assert completed["total_updated"] == 2
    assert completed["applied"] is False


@pytest.mark.asyncio
async def test_apply_writes_each_batch() -> None:
    graph = MagicMock()
    candidates = [
        {"id": "u1", "orgId": "o1", "email": "Alice@Acme.COM", "newEmail": "alice@acme.com"},
    ]
    graph.fetch_unnormalized_users = AsyncMock(side_effect=[candidates, []])
    graph.batch_upsert_nodes = AsyncMock()

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", apply=True, logger=logger, out=out, batch_size=10)
    assert code == EXIT_SUCCESS
    assert graph.batch_upsert_nodes.await_count == 1
    call_args = graph.batch_upsert_nodes.await_args
    assert call_args.args[1] == CollectionNames.USERS.value
    updated_nodes = call_args.args[0]
    assert len(updated_nodes) == 1
    assert updated_nodes[0]["id"] == "u1"
    assert updated_nodes[0]["email"] == "alice@acme.com"
    assert "updatedAtTimestamp" in updated_nodes[0]

    lines = _lines(out)
    completed = lines[-1]
    assert completed["status"] == "completed"
    assert completed["total_updated"] == 1
    assert completed["applied"] is True


@pytest.mark.asyncio
async def test_fetch_unnormalized_users_arango() -> None:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.http_client = MagicMock(execute_aql=AsyncMock(return_value=[{"id": "u1", "email": "A@X.COM", "newEmail": "a@x.com"}]))
    res = await fetch_unnormalized_users(p, "org_1", 50)
    assert len(res) == 1
    query, kwargs = p.http_client.execute_aql.await_args.args[0], p.http_client.execute_aql.await_args.kwargs
    assert "FILTER u.orgId == @org_id" in query
    assert "u.email != LOWER(TRIM(u.email))" in query
    assert kwargs["bind_vars"]["org_id"] == "org_1"
    assert kwargs["bind_vars"]["limit"] == 50


@pytest.mark.asyncio
async def test_fetch_unnormalized_users_neo4j() -> None:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = MagicMock(execute_query=AsyncMock(return_value=[{"id": "u1", "orgId": "org_1", "email": "A@X.COM", "newEmail": "a@x.com"}]))
    res = await fetch_unnormalized_users(p, "org_1", 50)
    assert len(res) == 1
    query, kwargs = p.client.execute_query.await_args.args[0], p.client.execute_query.await_args.kwargs
    assert "u.orgId = $org_id" in query
    assert "toLower(trim(u.email))" in query
    assert kwargs["parameters"]["org_id"] == "org_1"
    assert kwargs["parameters"]["limit"] == 50


@pytest.mark.asyncio
async def test_already_normalized_users_report_zero_updates() -> None:
    graph = MagicMock()
    graph.fetch_unnormalized_users = AsyncMock(return_value=[])
    graph.batch_upsert_nodes = AsyncMock()

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", apply=True, logger=logger, out=out)
    assert code == EXIT_SUCCESS
    graph.batch_upsert_nodes.assert_not_awaited()
    lines = _lines(out)
    completed = lines[-1]
    assert completed["total_scanned"] == 0
    assert completed["total_updated"] == 0


@pytest.mark.asyncio
async def test_falsy_batch_upsert_fails_batch_and_counts_zero_updated() -> None:
    graph = MagicMock()
    candidates = [
        {"id": "u1", "orgId": "o1", "email": "Alice@Acme.COM", "newEmail": "alice@acme.com"},
    ]
    graph.fetch_unnormalized_users = AsyncMock(return_value=candidates)
    # Return False (falsy) to simulate provider failure without raising
    graph.batch_upsert_nodes = AsyncMock(return_value=False)

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", apply=True, logger=logger, out=out, batch_size=10)
    assert code == EXIT_PARTIAL
    lines = _lines(out)
    error_line = [item for item in lines if "error" in item]
    assert len(error_line) == 1
    assert error_line[0]["error"] == "RuntimeError"

    completed = lines[-1]
    assert completed["status"] == "completed"
    assert completed["total_scanned"] == 1
    assert completed["total_updated"] == 0
    assert completed.get("total_failed_batches", completed.get("failed_batches")) == 1
