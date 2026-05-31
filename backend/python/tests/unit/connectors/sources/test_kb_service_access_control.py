"""
Tests for KnowledgeBaseService access-control logic introduced in the KB
upload-permission PR.

Covers:
  _resolve_user_and_kb_access
    - user not found → 404
    - happy path: only 2 DB calls (kb_exists NOT called when role is found)
    - no role, KB exists → 403
    - no role, KB does not exist → 404
    - role insufficient for required_roles (generic reason, no Python list repr)
    - role insufficient with custom permission_denied_reason
    - role sufficient for required_roles
    - DB error in get_user_kb_permission → propagates (not swallowed as 404)
    - DB error in kb_exists (lazy path) → propagates (Blocker 1 fix)
    - user dict uses '_key' instead of 'id'
    - user dict has neither id nor _key → 500 (not silent 403) [MAJOR 4]

  get_knowledge_base
    - delegates auth to _resolve_user_and_kb_access
    - returns KB data on success
    - returns 404 when provider returns None after permission check
    - all error codes are int, not str (Minor 3 fix)

  list_kb_records
    - user not found → 404 (only user check)
    - no KB permission → still returns records/empty list (provider filters),
      does NOT return 403 (Blocker 3 fix)
    - never calls get_user_kb_permission or kb_exists (those belong in the
      provider, not the service, for this endpoint)

  delete_knowledge_base
    - 403 access denial does NOT log at error level [MAJOR 1]
    - OWNER can delete successfully

  create_kb_permissions
    - non-OWNER gets operation-specific message
"""

import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service():
    """Return (service, graph_provider mock)."""
    graph_provider = AsyncMock()
    kafka_service = AsyncMock()
    logger = MagicMock()

    from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService

    svc = KnowledgeBaseService(
        logger=logger,
        graph_provider=graph_provider,
        kafka_service=kafka_service,
    )
    return svc, graph_provider


def _user(key="user-key-1"):
    return {"id": key}


def _user_alt_key(key="user-key-1"):
    return {"_key": key}


# ===========================================================================
# _resolve_user_and_kb_access
# ===========================================================================

class TestResolveUserAndKbAccess:

    @pytest.mark.asyncio
    async def test_user_not_found_returns_404(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = None

        user_key, user_role, err = await svc._resolve_user_and_kb_access("kb-1", "uid-1")

        assert user_key is None
        assert user_role is None
        assert err is not None
        assert err["code"] == 404
        assert "User not found" in err["reason"]
        # No subsequent DB calls when user is missing
        gp.get_user_kb_permission.assert_not_called()
        gp.kb_exists.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_kb_exists_not_called(self):
        """When user has a role kb_exists must NOT be called — lazy check (Major 1 fix)."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = "READER"

        user_key, user_role, err = await svc._resolve_user_and_kb_access("kb-1", "uid-1")

        assert err is None
        assert user_key == "user-key-1"
        assert user_role == "READER"
        # kb_exists must NOT have been called
        gp.kb_exists.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_role_kb_exists_returns_403(self):
        """No role + KB exists → 403 (permission denied), not 404 (not found)."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = None
        gp.kb_exists.return_value = True

        user_key, user_role, err = await svc._resolve_user_and_kb_access("kb-1", "uid-1")

        assert user_key is None
        assert err["code"] == 403
        gp.kb_exists.assert_called_once_with("kb-1")

    @pytest.mark.asyncio
    async def test_no_role_kb_not_found_returns_404(self):
        """No role + KB does not exist → 404 (not found), not 403 (forbidden)."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = None
        gp.kb_exists.return_value = False

        _, _, err = await svc._resolve_user_and_kb_access("kb-1", "uid-1")

        assert err["code"] == 404
        assert "Knowledge base not found" in err["reason"]

    @pytest.mark.asyncio
    async def test_required_roles_satisfied(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = "WRITER"

        user_key, user_role, err = await svc._resolve_user_and_kb_access(
            "kb-1", "uid-1", required_roles=["OWNER", "WRITER"]
        )

        assert err is None
        assert user_role == "WRITER"
        gp.kb_exists.assert_not_called()

    @pytest.mark.asyncio
    async def test_required_roles_not_satisfied_uses_custom_reason(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = "READER"

        _, _, err = await svc._resolve_user_and_kb_access(
            "kb-1", "uid-1",
            required_roles=["OWNER"],
            permission_denied_reason="Only KB owners can delete knowledge bases",
        )

        assert err["code"] == 403
        assert err["reason"] == "Only KB owners can delete knowledge bases"
        # Must NOT expose Python list repr like ['OWNER']
        assert "[" not in err["reason"]

    @pytest.mark.asyncio
    async def test_required_roles_not_satisfied_generic_reason_no_list_repr(self):
        """When no custom reason, the generic message must not expose Python list repr."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = "READER"

        _, _, err = await svc._resolve_user_and_kb_access(
            "kb-1", "uid-1", required_roles=["OWNER", "WRITER"]
        )

        assert err["code"] == 403
        # Must not contain Python list repr e.g. ['OWNER', 'WRITER']
        assert "[" not in err["reason"]
        assert "'" not in err["reason"]

    @pytest.mark.asyncio
    async def test_db_error_in_permission_check_propagates(self):
        """DB failure during get_user_kb_permission must NOT be converted to 404/403."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.side_effect = ConnectionError("ArangoDB is down")

        with pytest.raises(ConnectionError, match="ArangoDB is down"):
            await svc._resolve_user_and_kb_access("kb-1", "uid-1")

    @pytest.mark.asyncio
    async def test_db_error_in_kb_exists_propagates(self):
        """On the error path, if kb_exists throws the exception must propagate (Blocker 1)."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = None
        gp.kb_exists.side_effect = TimeoutError("Arango query timeout")

        with pytest.raises(TimeoutError, match="Arango query timeout"):
            await svc._resolve_user_and_kb_access("kb-1", "uid-1")

    @pytest.mark.asyncio
    async def test_user_key_fallback_to_underscore_key(self):
        """User dict may use '_key' instead of 'id'."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user_alt_key("alt-key")
        gp.get_user_kb_permission.return_value = "OWNER"

        user_key, _, err = await svc._resolve_user_and_kb_access("kb-1", "uid-1")

        assert err is None
        assert user_key == "alt-key"

    @pytest.mark.asyncio
    async def test_error_dict_code_is_integer(self):
        """Error codes in the returned dict must be int, not str (Minor 3)."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = None

        _, _, err = await svc._resolve_user_and_kb_access("kb-1", "uid-1")

        assert isinstance(err["code"], int), f"code should be int, got {type(err['code'])}"

    @pytest.mark.asyncio
    async def test_malformed_user_record_returns_500_not_403(self):
        """
        MAJOR 4 fix: user record with neither 'id' nor '_key' must return 500
        (malformed data) instead of silently becoming a 403 (permission denied).
        Before the fix, user_key=None was passed to get_user_kb_permission, which
        returned None, triggering the lazy kb_exists path and ultimately 403.
        """
        svc, gp = _make_service()
        # User record is present but has no usable key field
        gp.get_user_by_user_id.return_value = {"email": "user@example.com"}

        user_key, user_role, err = await svc._resolve_user_and_kb_access("kb-1", "uid-1")

        assert user_key is None
        assert user_role is None
        assert err is not None
        assert err["code"] == 500
        assert "malformed" in err["reason"].lower() or "internal" in err["reason"].lower()
        # Must NOT have made a permission or existence query with a None key
        gp.get_user_kb_permission.assert_not_called()
        gp.kb_exists.assert_not_called()
        # Must have logged an error (not a warning — this is a data problem)
        svc.logger.error.assert_called()


# ===========================================================================
# get_knowledge_base
# ===========================================================================

class TestGetKnowledgeBase:

    @pytest.mark.asyncio
    async def test_user_not_found_returns_404(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = None

        result = await svc.get_knowledge_base("kb-1", "uid-1")

        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_kb_not_found_returns_404(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = None
        gp.kb_exists.return_value = False

        result = await svc.get_knowledge_base("kb-1", "uid-1")

        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_permission_returns_403(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = None
        gp.kb_exists.return_value = True

        result = await svc.get_knowledge_base("kb-1", "uid-1")

        assert result["success"] is False
        assert result["code"] == 403

    @pytest.mark.asyncio
    async def test_success_returns_kb_data(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = "READER"
        kb_data = {"id": "kb-1", "name": "My KB", "userRole": "READER"}
        gp.get_knowledge_base.return_value = kb_data

        result = await svc.get_knowledge_base("kb-1", "uid-1")

        assert result == kb_data

    @pytest.mark.asyncio
    async def test_provider_returns_none_gives_404(self):
        """Provider returning None after auth passes → 404 (data integrity edge case)."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = "OWNER"
        gp.get_knowledge_base.return_value = None

        result = await svc.get_knowledge_base("kb-1", "uid-1")

        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_all_error_codes_are_int(self):
        """All error-path responses must carry int codes, never strings (Minor 3)."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = None

        result = await svc.get_knowledge_base("kb-1", "uid-1")

        assert isinstance(result["code"], int), (
            f"Expected int code, got {type(result['code'])}: {result['code']!r}"
        )

    @pytest.mark.asyncio
    async def test_404_vs_403_distinction_preserved(self):
        """404 (KB not found) and 403 (user has no permission) must be distinct."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = None

        # KB doesn't exist → 404
        gp.kb_exists.return_value = False
        result_404 = await svc.get_knowledge_base("kb-missing", "uid-1")

        # KB exists but no permission → 403
        gp.kb_exists.return_value = True
        result_403 = await svc.get_knowledge_base("kb-exists", "uid-1")

        assert result_404["code"] == 404
        assert result_403["code"] == 403


# ===========================================================================
# list_kb_records — Blocker 3: restored historical contract
# ===========================================================================

class TestListKbRecords:

    @pytest.mark.asyncio
    async def test_user_not_found_returns_404(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = None

        result = await svc.list_kb_records(
            kb_id="kb-1", user_id="uid-1", org_id="org-1", page=1, limit=10
        )

        assert result["success"] is False
        assert result["code"] == 404

    @pytest.mark.asyncio
    async def test_no_kb_permission_returns_empty_list_not_403(self):
        """
        Historical API contract: if the provider returns an empty list for an
        unauthorised user, the service must return {records: [], ...}, NOT 403.
        This is the Blocker 3 fix — list_kb_records must NOT call
        get_user_kb_permission or kb_exists at the service layer.
        """
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        # Provider handles its own access filtering and returns empty
        gp.list_kb_records.return_value = ([], 0, {})

        result = await svc.list_kb_records(
            kb_id="kb-1", user_id="uid-1", org_id="org-1", page=1, limit=10
        )

        # Must NOT be a 403 response
        assert result.get("success") is not False or result.get("code") != 403
        assert "records" in result
        assert result["records"] == []
        # Service must NOT bypass the provider by checking permission itself
        gp.get_user_kb_permission.assert_not_called()
        gp.kb_exists.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_records_returns_paginated_response(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        records = [{"id": "r1", "name": "doc1"}, {"id": "r2", "name": "doc2"}]
        gp.list_kb_records.return_value = (records, 2, {"types": ["pdf"]})

        result = await svc.list_kb_records(
            kb_id="kb-1", user_id="uid-1", org_id="org-1", page=1, limit=10
        )

        assert result["records"] == records
        assert result["pagination"]["totalCount"] == 2
        assert result["pagination"]["page"] == 1

    @pytest.mark.asyncio
    async def test_pagination_calculation(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.list_kb_records.return_value = ([], 50, {})

        result = await svc.list_kb_records(
            kb_id="kb-1", user_id="uid-1", org_id="org-1", page=3, limit=10
        )

        assert result["pagination"]["totalPages"] == 5
        assert result["pagination"]["page"] == 3
        # The skip passed to the provider must be (page-1)*limit = 20
        call_kwargs = gp.list_kb_records.call_args.kwargs
        assert call_kwargs.get("skip") == 20 or call_kwargs.get("offset") == 20


# ===========================================================================
# delete_knowledge_base — operation-specific message
# ===========================================================================

class TestDeleteKnowledgeBase:

    @pytest.mark.asyncio
    async def test_non_owner_gets_operation_specific_message(self):
        """WRITER trying to delete should get the original 'Only KB owners' message."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = "WRITER"

        result = await svc.delete_knowledge_base(kb_id="kb-1", user_id="uid-1")

        assert result["code"] == 403
        assert result["reason"] == "Only KB owners can delete knowledge bases"

    @pytest.mark.asyncio
    async def test_403_denial_does_not_log_at_error_level(self):
        """
        MAJOR 1 fix: a permission-denied (403) is an expected auth event, not a
        system failure.  It must NOT be logged at error level — doing so inflates
        error-rate dashboards and masks real failures.
        """
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = "READER"  # insufficient

        await svc.delete_knowledge_base(kb_id="kb-1", user_id="uid-1")

        # logger.error must NOT have been called for this access-control denial
        svc.logger.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_kb_not_found_does_not_log_at_error_level(self):
        """404 (KB not found) is also an expected outcome, not a system error."""
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = None
        gp.kb_exists.return_value = False

        await svc.delete_knowledge_base(kb_id="kb-missing", user_id="uid-1")

        svc.logger.error.assert_not_called()

    @pytest.mark.asyncio
    async def test_owner_can_delete(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = "OWNER"
        gp.delete_knowledge_base.return_value = {"success": True}

        result = await svc.delete_knowledge_base(kb_id="kb-1", user_id="uid-1")

        assert result["success"] is True


# ===========================================================================
# create_kb_permissions — operation-specific message
# ===========================================================================

class TestCreateKbPermissions:

    @pytest.mark.asyncio
    async def test_non_owner_gets_operation_specific_message(self):
        svc, gp = _make_service()
        gp.get_user_by_user_id.return_value = _user()
        gp.get_user_kb_permission.return_value = "WRITER"

        result = await svc.create_kb_permissions(
            kb_id="kb-1",
            requester_id="uid-1",
            user_ids=["uid-2"],
            team_ids=[],
            role="READER",
        )

        assert result["code"] == 403
        assert result["reason"] == "Only KB owners can grant permissions"
