"""
Tests for the kb_exists method in both ArangoHTTPProvider and Neo4jProvider,
and for the _validate_folder_creation null-check introduced in the review fix.

Critical invariant (Blocker 1 fix):
  kb_exists must NOT catch DB exceptions and return False.
  Any DB error must propagate so callers can return 500, not a misleading 404.

MAJOR 3 fix:
  _validate_folder_creation must null-check user_key and return 500 when
  the user document has neither '_key' nor 'id' — not a silent 403.

Test matrix:
  - Returns True when KB found (Arango + Neo4j)
  - Returns False when KB not found (Arango + Neo4j)
  - Propagates ConnectionError (Arango + Neo4j)
  - Propagates TimeoutError (Arango + Neo4j)
  - Propagates arbitrary RuntimeError (Arango + Neo4j)
  - _validate_folder_creation: malformed user_key → 500 (not 403)
  - _validate_folder_creation: uses '_key' or 'id' fallback
"""

import logging
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def arango_provider():
    provider = ArangoHTTPProvider(
        logger=MagicMock(spec=logging.Logger),
        config_service=MagicMock(),
    )
    provider.http_client = AsyncMock()
    return provider


@pytest.fixture
def neo4j_provider():
    provider = Neo4jProvider(
        logger=MagicMock(spec=logging.Logger),
        config_service=MagicMock(),
    )
    provider.client = AsyncMock()
    return provider


# ===========================================================================
# ArangoHTTPProvider.kb_exists
# ===========================================================================

class TestArangoKbExists:

    @pytest.mark.asyncio
    async def test_returns_true_when_kb_found(self, arango_provider):
        arango_provider.http_client.execute_aql.return_value = [1]

        result = await arango_provider.kb_exists("kb-abc")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_kb_not_found(self, arango_provider):
        arango_provider.http_client.execute_aql.return_value = []

        result = await arango_provider.kb_exists("kb-missing")

        assert result is False

    @pytest.mark.asyncio
    async def test_propagates_connection_error(self, arango_provider):
        """
        A DB connection failure must NOT be silently converted to False/404.
        This is the core Blocker 1 invariant.
        """
        arango_provider.http_client.execute_aql.side_effect = ConnectionError(
            "ArangoDB connection refused"
        )

        with pytest.raises(ConnectionError, match="ArangoDB connection refused"):
            await arango_provider.kb_exists("kb-1")

    @pytest.mark.asyncio
    async def test_propagates_timeout_error(self, arango_provider):
        arango_provider.http_client.execute_aql.side_effect = TimeoutError(
            "AQL query timed out"
        )

        with pytest.raises(TimeoutError):
            await arango_provider.kb_exists("kb-1")

    @pytest.mark.asyncio
    async def test_propagates_runtime_error(self, arango_provider):
        arango_provider.http_client.execute_aql.side_effect = RuntimeError(
            "Unexpected ArangoDB error"
        )

        with pytest.raises(RuntimeError, match="Unexpected ArangoDB error"):
            await arango_provider.kb_exists("kb-1")

    @pytest.mark.asyncio
    async def test_query_uses_kb_id_as_bind_var(self, arango_provider):
        """Verify the correct bind variable name is passed (no injection risk)."""
        arango_provider.http_client.execute_aql.return_value = [1]

        await arango_provider.kb_exists("target-kb-id")

        call_kwargs = arango_provider.http_client.execute_aql.call_args
        bind_vars = call_kwargs.kwargs.get("bind_vars") or call_kwargs.args[1]
        assert bind_vars["kb_id"] == "target-kb-id"


# ===========================================================================
# Neo4jProvider.kb_exists
# ===========================================================================

class TestNeo4jKbExists:

    @pytest.mark.asyncio
    async def test_returns_true_when_kb_found(self, neo4j_provider):
        neo4j_provider.client.execute_query.return_value = [{"exists": 1}]

        result = await neo4j_provider.kb_exists("kb-abc")

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_kb_not_found(self, neo4j_provider):
        neo4j_provider.client.execute_query.return_value = []

        result = await neo4j_provider.kb_exists("kb-missing")

        assert result is False

    @pytest.mark.asyncio
    async def test_propagates_connection_error(self, neo4j_provider):
        """
        A DB connection failure must NOT be silently converted to False/404.
        This is the core Blocker 1 invariant.
        """
        neo4j_provider.client.execute_query.side_effect = ConnectionError(
            "Neo4j connection refused"
        )

        with pytest.raises(ConnectionError, match="Neo4j connection refused"):
            await neo4j_provider.kb_exists("kb-1")

    @pytest.mark.asyncio
    async def test_propagates_timeout_error(self, neo4j_provider):
        neo4j_provider.client.execute_query.side_effect = TimeoutError(
            "Cypher query timed out"
        )

        with pytest.raises(TimeoutError):
            await neo4j_provider.kb_exists("kb-1")

    @pytest.mark.asyncio
    async def test_propagates_runtime_error(self, neo4j_provider):
        neo4j_provider.client.execute_query.side_effect = RuntimeError(
            "Unexpected Neo4j error"
        )

        with pytest.raises(RuntimeError, match="Unexpected Neo4j error"):
            await neo4j_provider.kb_exists("kb-1")

    @pytest.mark.asyncio
    async def test_query_uses_kb_id_as_parameter(self, neo4j_provider):
        """Verify parameterised query — kb_id passed as parameter, not interpolated."""
        neo4j_provider.client.execute_query.return_value = [{"exists": 1}]

        await neo4j_provider.kb_exists("target-kb-id")

        call_args = neo4j_provider.client.execute_query.call_args
        params = call_args.kwargs.get("parameters") or (
            call_args.args[1] if len(call_args.args) > 1 else {}
        )
        assert params.get("kb_id") == "target-kb-id"


# ===========================================================================
# ArangoHTTPProvider._validate_folder_creation — MAJOR 3 fix
# ===========================================================================

class TestArangoValidateFolderCreation:
    """
    _validate_folder_creation must null-check user_key and return 500 (not 403)
    when the user document has neither '_key' nor 'id'.

    Before the fix:
      user_key = user.get("_key")   # could be None, no fallback, no null check
      → get_user_kb_permission(kb_id, None) returns None
      → code 403 "Insufficient permissions" — wrong status, misleading message

    After the fix:
      user_key = user.get("_key") or user.get("id")
      if not user_key: return {"code": 500, "reason": "user record is malformed"}
    """

    @pytest.mark.asyncio
    async def test_malformed_user_no_key_returns_500(self, arango_provider):
        """User record present but has no '_key' or 'id' → 500, not 403."""
        arango_provider.get_user_by_user_id = AsyncMock(
            return_value={"email": "ghost@example.com"}  # no _key, no id
        )

        result = await arango_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is False
        assert result["code"] == 500
        assert "malformed" in result["reason"].lower() or "internal" in result["reason"].lower()
        # Must NOT have queried kb_exists or permissions with a None key
        arango_provider.http_client.execute_aql.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_key_via_underscore_key_field(self, arango_provider):
        """User document with only '_key' (no 'id') must be accepted."""
        arango_provider.get_user_by_user_id = AsyncMock(
            return_value={"_key": "user-key-1"}
        )
        arango_provider.http_client.execute_aql.return_value = [1]  # kb_exists → True
        arango_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")

        result = await arango_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is True
        assert result["user_key"] == "user-key-1"

    @pytest.mark.asyncio
    async def test_user_key_via_id_field_fallback(self, arango_provider):
        """User document with only 'id' (no '_key') must be accepted."""
        arango_provider.get_user_by_user_id = AsyncMock(
            return_value={"id": "user-id-1"}
        )
        arango_provider.http_client.execute_aql.return_value = [1]  # kb_exists → True
        arango_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await arango_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is True
        assert result["user_key"] == "user-id-1"

    @pytest.mark.asyncio
    async def test_kb_not_found_returns_404(self, arango_provider):
        arango_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk-1"})
        arango_provider.http_client.execute_aql.return_value = []  # kb_exists → False

        result = await arango_provider._validate_folder_creation("kb-missing", "uid-1")

        assert result["valid"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_role_returns_403_no_access_message(self, arango_provider):
        """User with NO role on the KB gets 404 to hide existence from unauthorized callers."""
        arango_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk-1"})
        arango_provider.http_client.execute_aql.return_value = [1]  # kb_exists → True
        arango_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await arango_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is False
        assert result["code"] == 404
        assert "kb-1" in result["reason"]

    @pytest.mark.asyncio
    async def test_insufficient_role_returns_403_with_role_in_message(self, arango_provider):
        """
        User with a valid but insufficient role (e.g. READER) must get a message
        that names their current role and the required roles.
        """
        arango_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk-1"})
        arango_provider.http_client.execute_aql.return_value = [1]  # kb_exists → True
        arango_provider.get_user_kb_permission = AsyncMock(return_value="READER")

        result = await arango_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is False
        assert result["code"] == 403
        assert "READER" in result["reason"]
        assert "OWNER" in result["reason"] or "WRITER" in result["reason"]

    @pytest.mark.asyncio
    async def test_db_error_in_kb_exists_returns_500_not_404(self, arango_provider):
        """
        When kb_exists throws, the except block must produce code=500, not 404.
        This ensures infrastructure failures are reported correctly.
        """
        arango_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk-1"})
        arango_provider.http_client.execute_aql.side_effect = ConnectionError("DB down")

        result = await arango_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is False
        assert result["code"] == 500


# ===========================================================================
# Neo4jProvider._validate_folder_creation — same fixes applied in parallel
# ===========================================================================

class TestNeo4jValidateFolderCreation:

    @pytest.mark.asyncio
    async def test_malformed_user_no_key_returns_500(self, neo4j_provider):
        """Neo4j provider: malformed user record → 500, not silent 403."""
        neo4j_provider.get_user_by_user_id = AsyncMock(
            return_value={"email": "ghost@example.com"}
        )

        result = await neo4j_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is False
        assert result["code"] == 500
        assert "malformed" in result["reason"].lower() or "internal" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_no_role_returns_403_no_access_message(self, neo4j_provider):
        """No role on KB returns 404 to hide existence."""
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk-1"})
        neo4j_provider.client.execute_query.return_value = [{"exists": 1}]  # kb_exists True
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value=None)

        result = await neo4j_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is False
        assert result["code"] == 404
        assert "kb-1" in result["reason"]

    @pytest.mark.asyncio
    async def test_insufficient_role_message_names_role(self, neo4j_provider):
        """COMMENTER role must produce a message naming their role and required roles."""
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk-1"})
        neo4j_provider.client.execute_query.return_value = [{"exists": 1}]
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="COMMENTER")

        result = await neo4j_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is False
        assert result["code"] == 403
        assert "COMMENTER" in result["reason"]

    @pytest.mark.asyncio
    async def test_owner_passes_validation(self, neo4j_provider):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"id": "uk-1"})
        neo4j_provider.client.execute_query.return_value = [{"exists": 1}]
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="OWNER")

        result = await neo4j_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_writer_passes_validation(self, neo4j_provider):
        neo4j_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk-1"})
        neo4j_provider.client.execute_query.return_value = [{"exists": 1}]
        neo4j_provider.get_user_kb_permission = AsyncMock(return_value="WRITER")

        result = await neo4j_provider._validate_folder_creation("kb-1", "uid-1")

        assert result["valid"] is True
