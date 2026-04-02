"""Unit tests for app.api.routes.entity module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.api.routes.entity import (
    _validate_and_filter_owner_updates,
    _validate_owner_removal,
    add_users_to_team,
    bulk_manage_team_users,
    create_team,
    delete_team,
    get_services,
    get_team,
    get_team_users,
    get_teams,
    get_user_created_teams,
    get_user_teams,
    get_users,
    remove_user_from_team,
    search_teams,
    update_team,
    update_user_permissions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(body_dict=None, query_params=None):
    """Create a mock FastAPI Request."""
    req = MagicMock()
    req.state.user = {"userId": "user-1", "orgId": "org-1"}
    req.app.container = MagicMock()
    req.app.container.logger.return_value = MagicMock()
    req.app.state.graph_provider = AsyncMock()

    if body_dict is not None:
        body_bytes = json.dumps(body_dict).encode("utf-8")
        req.body = AsyncMock(return_value=body_bytes)
    else:
        req.body = AsyncMock(return_value=b"{}")

    if query_params:
        req.query_params = query_params
    else:
        req.query_params = {}

    return req


def _graph_provider(request):
    return request.app.state.graph_provider


def _logger(request):
    return request.app.container.logger()


# ---------------------------------------------------------------------------
# get_services
# ---------------------------------------------------------------------------

class TestGetServices:
    @pytest.mark.asyncio
    async def test_returns_graph_provider_and_logger(self):
        req = _make_request()
        services = await get_services(req)
        assert "graph_provider" in services
        assert "logger" in services
        assert services["graph_provider"] is req.app.state.graph_provider


# ---------------------------------------------------------------------------
# _validate_owner_removal
# ---------------------------------------------------------------------------

class TestValidateOwnerRemoval:
    @pytest.mark.asyncio
    async def test_empty_user_ids_returns_immediately(self):
        gp = AsyncMock()
        logger = MagicMock()
        # Should not raise, should not call graph_provider
        await _validate_owner_removal(gp, "team-1", [], logger)
        gp.get_team_owner_removal_info.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_owners_being_removed(self):
        gp = AsyncMock()
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": [],
            "total_owner_count": 2,
        }
        logger = MagicMock()
        await _validate_owner_removal(gp, "team-1", ["user-x"], logger)

    @pytest.mark.asyncio
    async def test_raises_when_all_owners_removed(self):
        gp = AsyncMock()
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": ["user-owner"],
            "total_owner_count": 1,
        }
        logger = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await _validate_owner_removal(gp, "team-1", ["user-owner"], logger)
        assert exc.value.status_code == 400
        assert "At least one owner must remain" in exc.value.detail

    @pytest.mark.asyncio
    async def test_allows_removal_when_other_owners_remain(self):
        gp = AsyncMock()
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": ["user-owner1"],
            "total_owner_count": 3,
        }
        logger = MagicMock()
        # Should not raise
        await _validate_owner_removal(gp, "team-1", ["user-owner1"], logger)
        logger.info.assert_called()


# ---------------------------------------------------------------------------
# _validate_and_filter_owner_updates
# ---------------------------------------------------------------------------

class TestValidateAndFilterOwnerUpdates:
    @pytest.mark.asyncio
    async def test_team_not_found_raises_404(self):
        gp = AsyncMock()
        gp.get_team_permissions_and_owner_count.return_value = None
        logger = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await _validate_and_filter_owner_updates(
                gp, "team-1", [{"userId": "u1", "role": "READER"}], logger
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_team_not_found_empty_team_key(self):
        gp = AsyncMock()
        gp.get_team_permissions_and_owner_count.return_value = {"team": None}
        logger = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await _validate_and_filter_owner_updates(
                gp, "team-1", [{"userId": "u1", "role": "READER"}], logger
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_changes_when_all_owners_unchanged(self):
        gp = AsyncMock()
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u1": "OWNER"},
            "owner_count": 2,
        }
        logger = MagicMock()
        filtered, count = await _validate_and_filter_owner_updates(
            gp, "team-1", [{"userId": "u1", "role": "OWNER"}], logger
        )
        assert filtered == []
        assert count == 2
        logger.info.assert_called()

    @pytest.mark.asyncio
    async def test_bulk_operation_on_owners_raises(self):
        gp = AsyncMock()
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u1": "OWNER", "u2": "READER"},
            "owner_count": 2,
        }
        logger = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await _validate_and_filter_owner_updates(
                gp,
                "team-1",
                [
                    {"userId": "u1", "role": "READER"},  # downgrade owner
                    {"userId": "u2", "role": "OWNER"},   # promote reader
                ],
                logger,
            )
        assert exc.value.status_code == 400
        assert "bulk operations" in exc.value.detail.lower() or "Cannot perform bulk" in exc.value.detail

    @pytest.mark.asyncio
    async def test_single_owner_downgrade_allowed_when_others_remain(self):
        gp = AsyncMock()
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u1": "OWNER"},
            "owner_count": 2,
        }
        logger = MagicMock()
        filtered, count = await _validate_and_filter_owner_updates(
            gp, "team-1", [{"userId": "u1", "role": "READER"}], logger
        )
        assert len(filtered) == 1
        assert filtered[0]["userId"] == "u1"
        assert count == 2

    @pytest.mark.asyncio
    async def test_single_owner_downgrade_blocked_when_last_owner(self):
        gp = AsyncMock()
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u1": "OWNER"},
            "owner_count": 1,
        }
        logger = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await _validate_and_filter_owner_updates(
                gp, "team-1", [{"userId": "u1", "role": "READER"}], logger
            )
        assert exc.value.status_code == 400
        assert "At least one owner must remain" in exc.value.detail

    @pytest.mark.asyncio
    async def test_non_owner_update_passes_through(self):
        gp = AsyncMock()
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u1": "READER"},
            "owner_count": 1,
        }
        logger = MagicMock()
        filtered, count = await _validate_and_filter_owner_updates(
            gp, "team-1", [{"userId": "u1", "role": "OWNER"}], logger
        )
        assert len(filtered) == 1
        assert filtered[0]["role"] == "OWNER"


# ---------------------------------------------------------------------------
# create_team
# ---------------------------------------------------------------------------

class TestCreateTeam:
    @pytest.mark.asyncio
    async def test_success_basic(self):
        body = {"name": "Team Alpha", "description": "Desc"}
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = True
        gp.commit_transaction.return_value = True
        gp.get_team_with_users.return_value = {"team": "data"}

        with patch("app.api.routes.entity.uuid.uuid4", return_value="fake-uuid"):
            resp = await create_team(req)

        assert isinstance(resp, JSONResponse)
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["status"] == "success"
        assert content["data"] == {"team": "data"}

    @pytest.mark.asyncio
    async def test_user_not_found_raises_404(self):
        body = {"name": "Team"}
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await create_team(req)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_failed_node_creation_raises_500(self):
        body = {"name": "Team"}
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = None

        with pytest.raises(HTTPException) as exc:
            await create_team(req)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_failed_edge_creation_raises_500(self):
        body = {"name": "Team"}
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = None

        with pytest.raises(HTTPException) as exc:
            await create_team(req)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_legacy_format_user_ids(self):
        body = {"name": "Team", "userIds": ["u2", "u3"], "role": "EDITOR"}
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = True
        gp.commit_transaction.return_value = True
        gp.get_team_with_users.return_value = {"team": "data"}

        resp = await create_team(req)
        assert resp.status_code == 200
        # batch_create_edges should be called with edges for creator + u2, u3
        call_args = gp.batch_create_edges.call_args_list[0]
        edges = call_args[0][0]
        # Creator edge + 2 user edges = 3
        assert len(edges) == 3

    @pytest.mark.asyncio
    async def test_user_roles_format(self):
        body = {
            "name": "Team",
            "userRoles": [
                {"userId": "u2", "role": "EDITOR"},
                {"userId": "u3", "role": "READER"},
            ],
        }
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = True
        gp.commit_transaction.return_value = True
        gp.get_team_with_users.return_value = {}

        resp = await create_team(req)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_creator_not_duplicated_in_user_roles(self):
        body = {
            "name": "Team",
            "userRoles": [{"userId": "user-key-1", "role": "READER"}],
        }
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = True
        gp.commit_transaction.return_value = True
        gp.get_team_with_users.return_value = {}

        resp = await create_team(req)
        assert resp.status_code == 200
        # Only creator OWNER edge, no duplicate
        edges = gp.batch_create_edges.call_args_list[0][0][0]
        assert len(edges) == 1
        assert edges[0]["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_empty_user_id_in_roles_skipped(self):
        body = {
            "name": "Team",
            "userRoles": [{"userId": "", "role": "READER"}, {"userId": None, "role": "READER"}],
        }
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = True
        gp.commit_transaction.return_value = True
        gp.get_team_with_users.return_value = {}

        resp = await create_team(req)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_transaction_exception_triggers_rollback(self):
        body = {"name": "Team"}
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.side_effect = RuntimeError("db error")

        with pytest.raises(HTTPException) as exc:
            await create_team(req)
        assert exc.value.status_code == 500
        gp.rollback_transaction.assert_called_once_with("tx-1")

    @pytest.mark.asyncio
    async def test_exception_no_transaction_id(self):
        body = {"name": "Team"}
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.begin_transaction.return_value = None
        gp.batch_upsert_nodes.side_effect = RuntimeError("db error")

        with pytest.raises(HTTPException) as exc:
            await create_team(req)
        assert exc.value.status_code == 500
        gp.rollback_transaction.assert_not_called()


# ---------------------------------------------------------------------------
# get_teams
# ---------------------------------------------------------------------------

class TestGetTeams:
    @pytest.mark.asyncio
    async def test_success_with_results(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_teams.return_value = ([{"name": "T1"}], 1)

        resp = await get_teams(req, search=None, page=1, limit=10)
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["status"] == "success"
        assert len(content["teams"]) == 1
        assert content["pagination"]["total"] == 1
        assert content["pagination"]["pages"] == 1

    @pytest.mark.asyncio
    async def test_empty_results(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_teams.return_value = ([], 0)

        resp = await get_teams(req, search=None, page=1, limit=10)
        content = json.loads(resp.body.decode())
        assert content["teams"] == []
        assert content["pagination"]["pages"] == 0

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await get_teams(req, search=None, page=1, limit=10)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_teams.side_effect = RuntimeError("db down")

        with pytest.raises(HTTPException) as exc:
            await get_teams(req, search=None, page=1, limit=10)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_pagination_fields(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_teams.return_value = ([{"name": "T"}] * 10, 25)

        resp = await get_teams(req, search="test", page=2, limit=10)
        content = json.loads(resp.body.decode())
        assert content["pagination"]["pages"] == 3
        assert content["pagination"]["hasNext"] is True
        assert content["pagination"]["hasPrev"] is True

    @pytest.mark.asyncio
    async def test_search_passed_to_provider(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_teams.return_value = ([], 0)

        await get_teams(req, search="alpha", page=1, limit=10)
        gp.get_teams.assert_called_once()
        call_kwargs = gp.get_teams.call_args
        assert call_kwargs[1]["search"] == "alpha" or call_kwargs.kwargs.get("search") == "alpha"


# ---------------------------------------------------------------------------
# get_team
# ---------------------------------------------------------------------------

class TestGetTeam:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_team_with_users.return_value = {"name": "T1", "users": []}

        resp = await get_team(req, "team-1")
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["team"]["name"] == "T1"

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await get_team(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_team_not_found(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_team_with_users.return_value = None

        with pytest.raises(HTTPException) as exc:
            await get_team(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_team_with_users.side_effect = RuntimeError("unexpected")

        with pytest.raises(HTTPException) as exc:
            await get_team(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_re_raised(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_team_with_users.side_effect = HTTPException(status_code=403, detail="forbidden")

        with pytest.raises(HTTPException) as exc:
            await get_team(req, "team-1")
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# update_team
# ---------------------------------------------------------------------------

class TestUpdateTeam:
    def _setup_authorized_request(self, body_dict):
        req = _make_request(body_dict)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_edge.return_value = {"role": "OWNER"}
        gp.update_node.return_value = True
        gp.get_team_with_users.return_value = {"team": "updated"}
        return req, gp

    @pytest.mark.asyncio
    async def test_success_update_name(self):
        req, gp = self._setup_authorized_request({"name": "New Name"})
        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["message"] == "Team updated successfully"

    @pytest.mark.asyncio
    async def test_success_update_description(self):
        req, gp = self._setup_authorized_request({"description": "New desc"})
        resp = await update_team(req, "team-1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        req = _make_request({"name": "X"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await update_team(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_permission_edge(self):
        req = _make_request({"name": "X"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_edge.return_value = None

        with pytest.raises(HTTPException) as exc:
            await update_team(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_non_owner_permission(self):
        req = _make_request({"name": "X"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_edge.return_value = {"role": "READER"}

        with pytest.raises(HTTPException) as exc:
            await update_team(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_team_not_found_on_update(self):
        req = _make_request({"name": "X"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_edge.return_value = {"role": "OWNER"}
        gp.update_node.return_value = None

        with pytest.raises(HTTPException) as exc:
            await update_team(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_remove_users(self):
        body = {"name": "T", "removeUserIds": ["u2", "u3"]}
        req, gp = self._setup_authorized_request(body)
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": [],
            "total_owner_count": 1,
        }
        gp.delete_team_member_edges.return_value = ["e1", "e2"]

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        gp.delete_team_member_edges.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_users_legacy_format(self):
        body = {"name": "T", "addUserIds": ["u2"], "role": "EDITOR"}
        req, gp = self._setup_authorized_request(body)
        gp.batch_create_edges.return_value = True

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        gp.batch_create_edges.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_users_new_format(self):
        body = {"name": "T", "addUserRoles": [{"userId": "u2", "role": "READER"}]}
        req, gp = self._setup_authorized_request(body)
        gp.batch_create_edges.return_value = True

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_add_user_skips_creator(self):
        body = {
            "name": "T",
            "addUserRoles": [{"userId": "user-key-1", "role": "READER"}],
        }
        req, gp = self._setup_authorized_request(body)

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        # batch_create_edges should NOT be called since only creator was in add list
        gp.batch_create_edges.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_user_roles(self):
        body = {
            "name": "T",
            "updateUserRoles": [{"userId": "u2", "role": "EDITOR"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u2": "READER"},
            "owner_count": 1,
        }
        gp.batch_update_team_member_roles.return_value = [{"userId": "u2"}]

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        gp.batch_update_team_member_roles.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_user_roles_invalid_entries_skipped(self):
        body = {
            "name": "T",
            "updateUserRoles": [{"userId": "", "role": "READER"}, {"role": "READER"}],
        }
        req, gp = self._setup_authorized_request(body)

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        # No valid user roles, so batch_update should not be called
        gp.batch_update_team_member_roles.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_user_roles_no_changes_needed(self):
        body = {
            "name": "T",
            "updateUserRoles": [{"userId": "u1", "role": "OWNER"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u1": "OWNER"},
            "owner_count": 1,
        }

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_roles_batch_error(self):
        body = {
            "name": "T",
            "updateUserRoles": [{"userId": "u2", "role": "EDITOR"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u2": "READER"},
            "owner_count": 1,
        }
        gp.batch_update_team_member_roles.side_effect = RuntimeError("db fail")

        with pytest.raises(HTTPException) as exc:
            await update_team(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_add_user_roles_with_empty_user_id(self):
        """Cover line 452: continue when userId is empty in addUserRoles."""
        body = {
            "name": "T",
            "addUserRoles": [
                {"userId": "", "role": "READER"},
                {"userId": "u2", "role": "READER"},
            ],
        }
        req, gp = self._setup_authorized_request(body)
        gp.batch_create_edges.return_value = True

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        edges = gp.batch_create_edges.call_args[0][0]
        # Only u2 should be added, empty userId is skipped
        assert len(edges) == 1
        assert edges[0]["from_id"] == "u2"

    @pytest.mark.asyncio
    async def test_add_users_batch_create_returns_falsy(self):
        """Cover branch 468->472: batch_create_edges returns falsy."""
        body = {
            "name": "T",
            "addUserRoles": [{"userId": "u2", "role": "READER"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.batch_create_edges.return_value = None

        resp = await update_team(req, "team-1")
        # Should still succeed, just won't log about added users
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_remove_users_returns_empty(self):
        """Cover branch 412->416: deleted_list is empty/falsy."""
        body = {"name": "T", "removeUserIds": ["u2"]}
        req, gp = self._setup_authorized_request(body)
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": [],
            "total_owner_count": 1,
        }
        gp.delete_team_member_edges.return_value = []

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_generic_exception_raises_500(self):
        req = _make_request({"name": "X"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_edge.return_value = {"role": "OWNER"}
        gp.update_node.side_effect = RuntimeError("oops")

        with pytest.raises(HTTPException) as exc:
            await update_team(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_re_raised(self):
        req = _make_request({"name": "X"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_edge.return_value = {"role": "OWNER"}
        gp.update_node.side_effect = HTTPException(status_code=409, detail="conflict")

        with pytest.raises(HTTPException) as exc:
            await update_team(req, "team-1")
        assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# add_users_to_team
# ---------------------------------------------------------------------------

class TestAddUsersToTeam:
    def _setup(self, body_dict):
        req = _make_request(body_dict)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_edge.return_value = {"role": "OWNER"}
        return req, gp

    @pytest.mark.asyncio
    async def test_success(self):
        body = {"userRoles": [{"userId": "u2", "role": "READER"}]}
        req, gp = self._setup(body)
        gp.batch_create_edges.return_value = True
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await add_users_to_team(req, "team-1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_legacy_format(self):
        body = {"userIds": ["u2", "u3"], "role": "EDITOR"}
        req, gp = self._setup(body)
        gp.batch_create_edges.return_value = True
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await add_users_to_team(req, "team-1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_no_users_provided(self):
        body = {}
        req, gp = self._setup(body)

        with pytest.raises(HTTPException) as exc:
            await add_users_to_team(req, "team-1")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        req = _make_request({"userRoles": [{"userId": "u2", "role": "R"}]})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await add_users_to_team(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_permission(self):
        req = _make_request({"userRoles": [{"userId": "u2", "role": "R"}]})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_edge.return_value = None

        with pytest.raises(HTTPException) as exc:
            await add_users_to_team(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_non_owner_role(self):
        req = _make_request({"userRoles": [{"userId": "u2", "role": "R"}]})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_edge.return_value = {"role": "READER"}

        with pytest.raises(HTTPException) as exc:
            await add_users_to_team(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_only_creator_in_list(self):
        body = {"userRoles": [{"userId": "user-key-1", "role": "READER"}]}
        req, gp = self._setup(body)
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await add_users_to_team(req, "team-1")
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert "No users to add" in content["message"]

    @pytest.mark.asyncio
    async def test_empty_user_id_skipped(self):
        body = {"userRoles": [{"userId": "", "role": "R"}, {"userId": "u2", "role": "R"}]}
        req, gp = self._setup(body)
        gp.batch_create_edges.return_value = True
        gp.get_team_with_users.return_value = {}

        resp = await add_users_to_team(req, "team-1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_batch_create_edges_fails(self):
        body = {"userRoles": [{"userId": "u2", "role": "READER"}]}
        req, gp = self._setup(body)
        gp.batch_create_edges.return_value = None

        with pytest.raises(HTTPException) as exc:
            await add_users_to_team(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        body = {"userRoles": [{"userId": "u2", "role": "READER"}]}
        req, gp = self._setup(body)
        gp.batch_create_edges.side_effect = RuntimeError("boom")

        with pytest.raises(HTTPException) as exc:
            await add_users_to_team(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_re_raised(self):
        body = {"userRoles": [{"userId": "u2", "role": "READER"}]}
        req, gp = self._setup(body)
        gp.batch_create_edges.side_effect = HTTPException(status_code=409, detail="dup")

        with pytest.raises(HTTPException) as exc:
            await add_users_to_team(req, "team-1")
        assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# remove_user_from_team
# ---------------------------------------------------------------------------

class TestRemoveUserFromTeam:
    def _setup(self, body_dict):
        req = _make_request(body_dict)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_edge.return_value = {"role": "OWNER"}
        return req, gp

    @pytest.mark.asyncio
    async def test_success(self):
        body = {"userIds": ["u2"]}
        req, gp = self._setup(body)
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": [],
            "total_owner_count": 1,
        }
        gp.delete_team_member_edges.return_value = ["e1"]
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await remove_user_from_team(req, "team-1")
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert "1 user(s)" in content["message"]

    @pytest.mark.asyncio
    async def test_no_user_ids(self):
        body = {"userIds": []}
        req = _make_request(body)
        with pytest.raises(HTTPException) as exc:
            await remove_user_from_team(req, "team-1")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        body = {"userIds": ["u2"]}
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await remove_user_from_team(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_permission(self):
        body = {"userIds": ["u2"]}
        req, gp = self._setup(body)
        gp.get_edge.return_value = None

        with pytest.raises(HTTPException) as exc:
            await remove_user_from_team(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_non_owner_role(self):
        body = {"userIds": ["u2"]}
        req, gp = self._setup(body)
        gp.get_edge.return_value = {"role": "MEMBER"}

        with pytest.raises(HTTPException) as exc:
            await remove_user_from_team(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_users_found_to_remove(self):
        body = {"userIds": ["u2"]}
        req, gp = self._setup(body)
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": [],
            "total_owner_count": 1,
        }
        gp.delete_team_member_edges.return_value = None

        with pytest.raises(HTTPException) as exc:
            await remove_user_from_team(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_owner_removal_blocked(self):
        body = {"userIds": ["owner-1"]}
        req, gp = self._setup(body)
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": ["owner-1"],
            "total_owner_count": 1,
        }

        with pytest.raises(HTTPException) as exc:
            await remove_user_from_team(req, "team-1")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        body = {"userIds": ["u2"]}
        req, gp = self._setup(body)
        gp.get_team_owner_removal_info.side_effect = RuntimeError("db")

        with pytest.raises(HTTPException) as exc:
            await remove_user_from_team(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_re_raised(self):
        body = {"userIds": ["u2"]}
        req, gp = self._setup(body)
        gp.get_team_owner_removal_info.side_effect = HTTPException(
            status_code=400, detail="cannot remove"
        )

        with pytest.raises(HTTPException) as exc:
            await remove_user_from_team(req, "team-1")
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# update_user_permissions
# ---------------------------------------------------------------------------

class TestUpdateUserPermissions:
    def _setup(self, body_dict):
        req = _make_request(body_dict)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_edge.return_value = {"role": "OWNER"}
        return req, gp

    @pytest.mark.asyncio
    async def test_success(self):
        body = {"userRoles": [{"userId": "u2", "role": "EDITOR"}]}
        req, gp = self._setup(body)
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u2": "READER"},
            "owner_count": 1,
        }
        gp.batch_update_team_member_roles.return_value = [{"userId": "u2"}]
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await update_user_permissions(req, "team-1")
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["updated_count"] == 1

    @pytest.mark.asyncio
    async def test_legacy_format(self):
        body = {"userIds": ["u2"], "role": "EDITOR"}
        req, gp = self._setup(body)
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u2": "READER"},
            "owner_count": 1,
        }
        gp.batch_update_team_member_roles.return_value = [{"userId": "u2"}]
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await update_user_permissions(req, "team-1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_no_users_provided(self):
        body = {}
        req, gp = self._setup(body)

        with pytest.raises(HTTPException) as exc:
            await update_user_permissions(req, "team-1")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        req = _make_request({"userRoles": [{"userId": "u2", "role": "R"}]})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await update_user_permissions(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_permission(self):
        req = _make_request({"userRoles": [{"userId": "u2", "role": "R"}]})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_edge.return_value = None

        with pytest.raises(HTTPException) as exc:
            await update_user_permissions(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_non_owner_role(self):
        req = _make_request({"userRoles": [{"userId": "u2", "role": "R"}]})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_edge.return_value = {"role": "READER"}

        with pytest.raises(HTTPException) as exc:
            await update_user_permissions(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_valid_roles(self):
        body = {"userRoles": [{"userId": "", "role": "R"}, {"userId": "u1"}]}
        req, gp = self._setup(body)

        with pytest.raises(HTTPException) as exc:
            await update_user_permissions(req, "team-1")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_no_changes_needed(self):
        body = {"userRoles": [{"userId": "u1", "role": "OWNER"}]}
        req, gp = self._setup(body)
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u1": "OWNER"},
            "owner_count": 1,
        }
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await update_user_permissions(req, "team-1")
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert "No changes needed" in content["message"]

    @pytest.mark.asyncio
    async def test_batch_update_returns_none(self):
        body = {"userRoles": [{"userId": "u2", "role": "EDITOR"}]}
        req, gp = self._setup(body)
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"u2": "READER"},
            "owner_count": 1,
        }
        gp.batch_update_team_member_roles.return_value = None

        with pytest.raises(HTTPException) as exc:
            await update_user_permissions(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        body = {"userRoles": [{"userId": "u2", "role": "EDITOR"}]}
        req, gp = self._setup(body)
        gp.get_team_permissions_and_owner_count.side_effect = RuntimeError("db")

        with pytest.raises(HTTPException) as exc:
            await update_user_permissions(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_re_raised(self):
        body = {"userRoles": [{"userId": "u2", "role": "EDITOR"}]}
        req, gp = self._setup(body)
        gp.get_team_permissions_and_owner_count.side_effect = HTTPException(
            status_code=400, detail="bad"
        )

        with pytest.raises(HTTPException) as exc:
            await update_user_permissions(req, "team-1")
        assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# delete_team
# ---------------------------------------------------------------------------

class TestDeleteTeam:
    def _setup(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_edge.return_value = {"role": "OWNER"}
        return req, gp

    @pytest.mark.asyncio
    async def test_success(self):
        req, gp = self._setup()
        gp.delete_all_team_permissions.return_value = True
        gp.delete_nodes.return_value = True

        resp = await delete_team(req, "team-1")
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["message"] == "Team deleted successfully"

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_permission(self):
        req, gp = self._setup()
        gp.get_edge.return_value = None

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_non_owner_role(self):
        req, gp = self._setup()
        gp.get_edge.return_value = {"role": "MEMBER"}

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_team_not_found_on_delete(self):
        req, gp = self._setup()
        gp.delete_all_team_permissions.return_value = True
        gp.delete_nodes.return_value = None

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        req, gp = self._setup()
        gp.delete_all_team_permissions.side_effect = RuntimeError("db")

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_re_raised(self):
        req, gp = self._setup()
        gp.delete_all_team_permissions.side_effect = HTTPException(
            status_code=404, detail="not found"
        )

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "team-1")
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# get_user_teams
# ---------------------------------------------------------------------------

class TestGetUserTeams:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_user_teams.return_value = ([{"name": "T1"}], 1)

        resp = await get_user_teams(req, search=None, page=1, limit=100)
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert len(content["teams"]) == 1

    @pytest.mark.asyncio
    async def test_empty_results(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_user_teams.return_value = ([], 0)

        resp = await get_user_teams(req, search=None, page=1, limit=100)
        content = json.loads(resp.body.decode())
        assert content["teams"] == []
        assert content["pagination"]["pages"] == 0

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await get_user_teams(req, search=None, page=1, limit=100)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_user_teams.side_effect = RuntimeError("db")

        with pytest.raises(HTTPException) as exc:
            await get_user_teams(req, search=None, page=1, limit=100)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_pagination(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_user_teams.return_value = ([{"name": "T"}] * 5, 15)

        resp = await get_user_teams(req, search=None, page=2, limit=5)
        content = json.loads(resp.body.decode())
        assert content["pagination"]["pages"] == 3
        assert content["pagination"]["hasNext"] is True
        assert content["pagination"]["hasPrev"] is True


# ---------------------------------------------------------------------------
# get_user_created_teams
# ---------------------------------------------------------------------------

class TestGetUserCreatedTeams:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_user_created_teams.return_value = ([{"name": "T1"}], 1)

        resp = await get_user_created_teams(req, search=None, page=1, limit=100)
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["message"] == "User created teams fetched successfully"

    @pytest.mark.asyncio
    async def test_empty_results(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_user_created_teams.return_value = ([], 0)

        resp = await get_user_created_teams(req, search=None, page=1, limit=100)
        content = json.loads(resp.body.decode())
        assert content["teams"] == []

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await get_user_created_teams(req, search=None, page=1, limit=100)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_user_created_teams.side_effect = RuntimeError("db")

        with pytest.raises(HTTPException) as exc:
            await get_user_created_teams(req, search=None, page=1, limit=100)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_pagination(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_user_created_teams.return_value = ([{"name": "T"}] * 3, 9)

        resp = await get_user_created_teams(req, search="x", page=3, limit=3)
        content = json.loads(resp.body.decode())
        assert content["pagination"]["pages"] == 3
        assert content["pagination"]["hasNext"] is False
        assert content["pagination"]["hasPrev"] is True


# ---------------------------------------------------------------------------
# get_users
# ---------------------------------------------------------------------------

class TestGetUsers:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_organization_users.return_value = ([{"name": "U1"}], 1)

        resp = await get_users(req, search=None, page=1, limit=100)
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert len(content["users"]) == 1

    @pytest.mark.asyncio
    async def test_empty_results(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_organization_users.return_value = ([], 0)

        resp = await get_users(req, search=None, page=1, limit=100)
        content = json.loads(resp.body.decode())
        assert content["users"] == []

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_organization_users.side_effect = RuntimeError("db")

        with pytest.raises(HTTPException) as exc:
            await get_users(req, search=None, page=1, limit=100)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_pagination(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_organization_users.return_value = ([{"u": "x"}] * 10, 50)

        resp = await get_users(req, search="john", page=2, limit=10)
        content = json.loads(resp.body.decode())
        assert content["pagination"]["pages"] == 5
        assert content["pagination"]["hasNext"] is True
        assert content["pagination"]["hasPrev"] is True


# ---------------------------------------------------------------------------
# get_team_users
# ---------------------------------------------------------------------------

class TestGetTeamUsers:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_team_users.return_value = {"users": [{"name": "U1"}]}

        resp = await get_team_users(req, "team-1")
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["team"]["users"] == [{"name": "U1"}]

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await get_team_users(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_team_not_found(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_team_users.return_value = None

        with pytest.raises(HTTPException) as exc:
            await get_team_users(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_team_users.side_effect = RuntimeError("db")

        with pytest.raises(HTTPException) as exc:
            await get_team_users(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_re_raised(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.get_team_users.side_effect = HTTPException(status_code=403, detail="nope")

        with pytest.raises(HTTPException) as exc:
            await get_team_users(req, "team-1")
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# bulk_manage_team_users
# ---------------------------------------------------------------------------

class TestBulkManageTeamUsers:
    def _setup(self, body_dict):
        req = _make_request(body_dict)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        return req, gp

    @pytest.mark.asyncio
    async def test_success_add_only(self):
        body = {"addUserIds": ["u2", "u3"]}
        req, gp = self._setup(body)
        gp.batch_create_edges.return_value = True
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await bulk_manage_team_users(req, "team-1")
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["added"] == 2
        assert content["removed"] == 0

    @pytest.mark.asyncio
    async def test_success_remove_only(self):
        body = {"removeUserIds": ["u2"]}
        req, gp = self._setup(body)
        gp.delete_team_member_edges.return_value = ["e1"]
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await bulk_manage_team_users(req, "team-1")
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["removed"] == 1
        assert content["added"] == 0

    @pytest.mark.asyncio
    async def test_success_add_and_remove(self):
        body = {"addUserIds": ["u3"], "removeUserIds": ["u2"]}
        req, gp = self._setup(body)
        gp.delete_team_member_edges.return_value = ["e1"]
        gp.batch_create_edges.return_value = True
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await bulk_manage_team_users(req, "team-1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_no_users_provided(self):
        body = {}
        req, gp = self._setup(body)

        with pytest.raises(HTTPException) as exc:
            await bulk_manage_team_users(req, "team-1")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        body = {"addUserIds": ["u2"]}
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await bulk_manage_team_users(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cannot_remove_owner(self):
        body = {"removeUserIds": ["user-key-1"]}
        req, gp = self._setup(body)

        with pytest.raises(HTTPException) as exc:
            await bulk_manage_team_users(req, "team-1")
        assert exc.value.status_code == 400
        assert "Cannot remove team owner" in exc.value.detail

    @pytest.mark.asyncio
    async def test_remove_no_users_found(self):
        body = {"removeUserIds": ["u2"]}
        req, gp = self._setup(body)
        gp.delete_team_member_edges.return_value = None

        with pytest.raises(HTTPException) as exc:
            await bulk_manage_team_users(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_add_batch_create_fails(self):
        body = {"addUserIds": ["u2"]}
        req, gp = self._setup(body)
        gp.batch_create_edges.return_value = None

        with pytest.raises(HTTPException) as exc:
            await bulk_manage_team_users(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        body = {"addUserIds": ["u2"]}
        req, gp = self._setup(body)
        gp.batch_create_edges.side_effect = RuntimeError("db")

        with pytest.raises(HTTPException) as exc:
            await bulk_manage_team_users(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_re_raised(self):
        body = {"removeUserIds": ["user-key-1"]}
        req, gp = self._setup(body)

        with pytest.raises(HTTPException) as exc:
            await bulk_manage_team_users(req, "team-1")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_custom_role(self):
        body = {"addUserIds": ["u2"], "role": "EDITOR"}
        req, gp = self._setup(body)
        gp.batch_create_edges.return_value = True
        gp.get_team_with_users.return_value = {"team": "t"}

        resp = await bulk_manage_team_users(req, "team-1")
        assert resp.status_code == 200
        edges = gp.batch_create_edges.call_args[0][0]
        assert edges[0]["role"] == "EDITOR"


# ---------------------------------------------------------------------------
# search_teams
# ---------------------------------------------------------------------------

class TestSearchTeams:
    @pytest.mark.asyncio
    async def test_success(self):
        req = _make_request(query_params={"q": "alpha", "limit": "5", "offset": "0"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.search_teams.return_value = [{"name": "Alpha Team"}]

        resp = await search_teams(req)
        assert resp.status_code == 200
        content = json.loads(resp.body.decode())
        assert content["query"] == "alpha"
        assert content["count"] == 1

    @pytest.mark.asyncio
    async def test_user_not_found(self):
        req = _make_request(query_params={"q": "test"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = None

        with pytest.raises(HTTPException) as exc:
            await search_teams(req)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_query(self):
        req = _make_request(query_params={"q": ""})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}

        with pytest.raises(HTTPException) as exc:
            await search_teams(req)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_query_param(self):
        req = _make_request(query_params={})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}

        with pytest.raises(HTTPException) as exc:
            await search_teams(req)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_default_limit_and_offset(self):
        req = _make_request(query_params={"q": "test"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.search_teams.return_value = []

        resp = await search_teams(req)
        content = json.loads(resp.body.decode())
        assert content["limit"] == 10
        assert content["offset"] == 0

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        req = _make_request(query_params={"q": "test"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.search_teams.side_effect = RuntimeError("db")

        with pytest.raises(HTTPException) as exc:
            await search_teams(req)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_custom_limit_and_offset(self):
        req = _make_request(query_params={"q": "test", "limit": "20", "offset": "5"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk"}
        gp.search_teams.return_value = [{"name": "T1"}, {"name": "T2"}]

        resp = await search_teams(req)
        content = json.loads(resp.body.decode())
        assert content["limit"] == 20
        assert content["offset"] == 5
        assert content["count"] == 2
