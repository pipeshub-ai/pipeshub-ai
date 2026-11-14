"""Unit tests for normalize_user_emails backfill script."""
from __future__ import annotations

import io
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.scripts.normalize_user_emails import (
    EXIT_INVALID,
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


@pytest.mark.asyncio
async def test_stalled_batch_apply_mode_halts_and_fails_batch() -> None:
    graph = MagicMock()
    candidate = {"id": "u1", "orgId": "o1", "email": "Alice@Acme.COM", "newEmail": "alice@acme.com"}
    # Returns candidate in batch 1, then returns candidate again in batch 2
    graph.fetch_unnormalized_users = AsyncMock(side_effect=[[candidate], [candidate]])
    graph.batch_upsert_nodes = AsyncMock(return_value=True)

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", apply=True, logger=logger, out=out, batch_size=10)
    assert code == EXIT_PARTIAL
    lines = _lines(out)
    error_line = [item for item in lines if item.get("error") == "StalledBatchError"]
    assert len(error_line) == 1

    completed = lines[-1]
    assert completed["status"] == "completed"
    # Should not recount the stalled batch into total_scanned or total_updated
    assert completed["total_scanned"] == 1
    assert completed["total_updated"] == 1
    assert completed["failed_batches"] == 1


def test_batch_size_validation_cli() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--org", "o1", "--batch-size", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--org", "o1", "--batch-size", "-10"])


@pytest.mark.asyncio
async def test_batch_size_validation_programmatic() -> None:
    graph = MagicMock()
    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", apply=False, logger=logger, out=out, batch_size=0)
    assert code == EXIT_INVALID
    lines = _lines(out)
    assert lines[0]["error"] == "Batch size must be a positive integer."


@pytest.mark.asyncio
async def test_all_orgs_enumerates_inactive_and_external_and_deduplicates() -> None:
    graph = MagicMock()
    internal_docs = [{"id": "org_1"}, {"id": "org_2"}]
    external_docs = [{"id": "org_2"}, {"_key": "org_3"}]
    graph.get_all_orgs = AsyncMock(side_effect=[internal_docs, external_docs])
    graph.fetch_unnormalized_users = AsyncMock(return_value=[])

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, all_orgs=True, apply=False, logger=logger, out=out)
    assert code == EXIT_SUCCESS

    # Verify both calls were made to get_all_orgs with active=False
    assert graph.get_all_orgs.await_count == 2
    first_call = graph.get_all_orgs.await_args_list[0].kwargs
    second_call = graph.get_all_orgs.await_args_list[1].kwargs
    assert first_call["active"] is False and first_call["is_external"] is False
    assert second_call["active"] is False and second_call["is_external"] is True

    # Verify fetch was called for exactly org_1, org_2, org_3 (deduplicated)
    called_orgs = [call.args[0] for call in graph.fetch_unnormalized_users.await_args_list]
    assert called_orgs == ["org_1", "org_2", "org_3"]


@pytest.mark.asyncio
async def test_fetch_unnormalized_users_postgres_tab_padded() -> None:
    class FakePostgresProvider:
        provider_type = "postgres"

        def __init__(self) -> None:
            self.executed_query = None
            self.executed_params = None

        async def _id_rows(self, query: str, params: list, txn: object = None) -> list[dict]:
            self.executed_query = query
            self.executed_params = params
            return [
                {
                    "id": "u1",
                    "email": "\talice@acme.com\t",
                    "new_email": "alice@acme.com",
                }
            ]

    p = FakePostgresProvider()
    res = await fetch_unnormalized_users(p, "org_1", 50)
    assert len(res) == 1
    assert res[0]["id"] == "u1"
    assert res[0]["email"] == "\talice@acme.com\t"
    assert res[0]["newEmail"] == "alice@acme.com"
    assert "btrim(doc->>'email', E' \\t\\r\\n')" in p.executed_query


@pytest.mark.asyncio
async def test_postgres_regression_tab_padded_email_normalized() -> None:
    # Verify migration selects and normalizes already-lowercase email surrounded by tabs
    graph = MagicMock()
    candidates = [
        {"id": "u1", "orgId": "o1", "email": "\talice@acme.com\t", "newEmail": "alice@acme.com"},
    ]
    graph.fetch_unnormalized_users = AsyncMock(side_effect=[candidates, []])
    graph.batch_upsert_nodes = AsyncMock(return_value=True)

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", apply=True, logger=logger, out=out, batch_size=10)
    assert code == EXIT_SUCCESS
    assert graph.batch_upsert_nodes.await_count == 1
    updated = graph.batch_upsert_nodes.await_args.args[0]
    assert updated[0]["id"] == "u1"
    assert updated[0]["email"] == "alice@acme.com"


@pytest.mark.asyncio
async def test_normalization_flag_set_on_all_orgs_apply_success() -> None:
    from app.services.graph_db.interface.graph_db_provider import (
        USER_EMAIL_NORMALIZATION_FLAG,
    )

    graph = MagicMock()
    graph.get_all_orgs = AsyncMock(side_effect=[[{"id": "org_1"}], []])
    graph.fetch_unnormalized_users = AsyncMock(return_value=[])

    config_service = MagicMock()
    config_service.set_config = AsyncMock(return_value=True)

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(
        graph,
        all_orgs=True,
        apply=True,
        logger=logger,
        out=out,
        config_service=config_service,
    )
    assert code == EXIT_SUCCESS
    config_service.set_config.assert_awaited_once_with(
        USER_EMAIL_NORMALIZATION_FLAG, {"done": True}
    )


@pytest.mark.asyncio
async def test_normalization_flag_not_set_on_dry_run_or_single_org() -> None:

    graph = MagicMock()
    graph.get_all_orgs = AsyncMock(side_effect=[[{"id": "org_1"}], []])
    graph.fetch_unnormalized_users = AsyncMock(return_value=[])

    config_service = MagicMock()
    config_service.set_config = AsyncMock(return_value=True)
    logger = logging.getLogger("test")

    # Dry-run on all_orgs: must not set flag
    out1 = io.StringIO()
    await run_normalization(
        graph,
        all_orgs=True,
        apply=False,
        logger=logger,
        out=out1,
        config_service=config_service,
    )
    config_service.set_config.assert_not_called()

    # Apply on single org: must not set flag
    out2 = io.StringIO()
    await run_normalization(
        graph,
        org_id="org_1",
        apply=True,
        logger=logger,
        out=out2,
        config_service=config_service,
    )
    config_service.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_normalization_flag_set_config_false_returns_partial() -> None:
    from app.services.graph_db.interface.graph_db_provider import (
        USER_EMAIL_NORMALIZATION_FLAG,
    )

    graph = MagicMock()
    graph.get_all_orgs = AsyncMock(side_effect=[[{"id": "org_1"}], []])
    graph.fetch_unnormalized_users = AsyncMock(return_value=[])

    config_service = MagicMock()
    config_service.set_config = AsyncMock(return_value=False)

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(
        graph,
        all_orgs=True,
        apply=True,
        logger=logger,
        out=out,
        config_service=config_service,
    )
    assert code == EXIT_PARTIAL
    config_service.set_config.assert_awaited_once_with(
        USER_EMAIL_NORMALIZATION_FLAG, {"done": True}
    )


@pytest.mark.asyncio
async def test_normalization_flag_set_config_exception_returns_partial() -> None:
    from app.services.graph_db.interface.graph_db_provider import (
        USER_EMAIL_NORMALIZATION_FLAG,
    )

    graph = MagicMock()
    graph.get_all_orgs = AsyncMock(side_effect=[[{"id": "org_1"}], []])
    graph.fetch_unnormalized_users = AsyncMock(return_value=[])

    config_service = MagicMock()
    config_service.set_config = AsyncMock(side_effect=RuntimeError("store unavailable"))

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(
        graph,
        all_orgs=True,
        apply=True,
        logger=logger,
        out=out,
        config_service=config_service,
    )
    assert code == EXIT_PARTIAL
    config_service.set_config.assert_awaited_once_with(
        USER_EMAIL_NORMALIZATION_FLAG, {"done": True}
    )


@pytest.mark.asyncio
async def test_simultaneous_org_and_all_orgs_rejected() -> None:
    graph = MagicMock()
    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", all_orgs=True, logger=logger, out=out)
    assert code == EXIT_INVALID
    lines = _lines(out)
    assert lines[0]["error"] == "Cannot specify both --org and --all-orgs."


@pytest.mark.asyncio
async def test_single_org_run_does_not_set_global_flag() -> None:
    graph = MagicMock()
    graph.fetch_unnormalized_users = AsyncMock(return_value=[])

    config_service = MagicMock()
    config_service.set_config = AsyncMock()

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(
        graph,
        org_id="o1",
        apply=True,
        logger=logger,
        out=out,
        config_service=config_service,
    )
    assert code == EXIT_SUCCESS
    config_service.set_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_normalization_skips_when_stored_email_changed() -> None:
    graph = MagicMock()
    candidates = [
        {"id": "u1", "orgId": "o1", "email": "Alice@Acme.COM", "newEmail": "alice@acme.com"},
    ]
    graph.fetch_unnormalized_users = AsyncMock(side_effect=[candidates, []])
    # Concurrent write changed the stored email
    graph.get_document = AsyncMock(return_value={"id": "u1", "email": "alice_concurrent@acme.com"})
    graph.batch_upsert_nodes = AsyncMock()

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", apply=True, logger=logger, out=out, batch_size=10)
    assert code == EXIT_SUCCESS
    graph.batch_upsert_nodes.assert_not_awaited()
    lines = _lines(out)
    completed = lines[-1]
    assert completed["status"] == "completed"
    assert completed["total_scanned"] == 1
    assert completed["total_updated"] == 0


@pytest.mark.asyncio
async def test_normalization_skips_when_stored_user_deleted() -> None:
    graph = MagicMock()
    candidates = [
        {"id": "u1", "orgId": "o1", "email": "Alice@Acme.COM", "newEmail": "alice@acme.com"},
    ]
    graph.fetch_unnormalized_users = AsyncMock(side_effect=[candidates, []])
    # Document deleted concurrently
    graph.get_document = AsyncMock(return_value=None)
    graph.batch_upsert_nodes = AsyncMock()

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", apply=True, logger=logger, out=out, batch_size=10)
    assert code == EXIT_SUCCESS
    graph.batch_upsert_nodes.assert_not_awaited()
    lines = _lines(out)
    completed = lines[-1]
    assert completed["status"] == "completed"
    assert completed["total_scanned"] == 1
    assert completed["total_updated"] == 0


@pytest.mark.asyncio
async def test_normalization_proceeds_when_stored_email_matches() -> None:
    graph = MagicMock()
    candidates = [
        {"id": "u1", "orgId": "o1", "email": "Alice@Acme.COM", "newEmail": "alice@acme.com"},
    ]
    graph.fetch_unnormalized_users = AsyncMock(side_effect=[candidates, []])
    graph.get_document = AsyncMock(return_value={"id": "u1", "email": "Alice@Acme.COM"})
    graph.batch_upsert_nodes = AsyncMock(return_value=True)

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", apply=True, logger=logger, out=out, batch_size=10)
    assert code == EXIT_SUCCESS
    assert graph.batch_upsert_nodes.await_count == 1
    updated = graph.batch_upsert_nodes.await_args.args[0][0]
    assert updated["id"] == "u1"
    assert updated["email"] == "alice@acme.com"
    lines = _lines(out)
    completed = lines[-1]
    assert completed["total_scanned"] == 1
    assert completed["total_updated"] == 1


@pytest.mark.asyncio
async def test_reject_simultaneous_org_and_all_orgs() -> None:
    graph = MagicMock()
    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, org_id="o1", all_orgs=True, apply=False, logger=logger, out=out)
    assert code == EXIT_INVALID
    lines = _lines(out)
    assert lines[0]["error"] == "Cannot specify both --org and --all-orgs."


@pytest.mark.asyncio
async def test_all_orgs_does_not_set_flag_if_unnormalized_users_remain_outside_orgs() -> None:
    graph = MagicMock()
    graph.get_all_orgs = AsyncMock(return_value=[{"id": "org_1"}])
    # fetch_unnormalized_users returns empty for org_1 pass, but returns unnormalized user for unscoped check
    def mock_fetch(org_id: str | None, limit: int, offset: int = 0) -> list:
        if org_id == "org_1":
            return []
        if org_id is None:
            # User with blank orgId exists outside enumerated passes
            return [{"id": "u_blank", "orgId": "", "email": "orphan@example.com", "newEmail": "orphan@example.com"}]
        return []

    graph.fetch_unnormalized_users = AsyncMock(side_effect=mock_fetch)
    cs = AsyncMock()
    cs.set_config = AsyncMock(return_value=True)

    out = io.StringIO()
    logger = logging.getLogger("test")

    code = await run_normalization(graph, all_orgs=True, apply=True, logger=logger, out=out, config_service=cs)
    # Should exit with partial and NOT set flag
    assert code == EXIT_PARTIAL
    cs.set_config.assert_not_called()
