"""Unit tests for app.api.routes.entity module."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.api.routes.entity import (
    _validate_and_filter_owner_updates,
    _validate_owner_removal,
    create_team,
    delete_team,
    get_services,
    get_team,
    get_team_users,
    get_user_teams,
    get_users,
    update_team,
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
    # A mock's coroutine is truthy, which would read as "retry" to the idempotent writer.
    req.app.state.graph_provider.is_write_conflict = MagicMock(return_value=False)

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

CREATOR_MONGO_ID = "507f1f77bcf86cd799439011"
MEMBER_MONGO_ID_2 = "507f1f77bcf86cd799439012"
MEMBER_MONGO_ID_3 = "507f1f77bcf86cd799439013"


def _mock_get_graph_user_keys_by_mongo_user_ids(user_ids, org_id=None, chunk_size=500):
    mapping = {
        CREATOR_MONGO_ID: "user-key-1",
        "user-1": "user-key-1",
        MEMBER_MONGO_ID_2: "graph-key-2",
        MEMBER_MONGO_ID_3: "graph-key-3",
    }
    resolved = {mid: mapping[mid] for mid in user_ids if mid in mapping}
    missing = [mid for mid in user_ids if mid not in resolved]
    if missing:
        raise ValueError(f"Users not found in graph: {missing}")
    return resolved


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
    async def test_a_failed_user_read_is_not_a_404(self) -> None:
        """The provider answers None to a failed read unless told to raise; "User not
        found" for a person who is there would send them away for good."""
        req = _make_request({"name": "Team"})
        gp = _graph_provider(req)
        gp.get_user_by_user_id.side_effect = RuntimeError("LockAcquisitionTimeout")

        with pytest.raises(RuntimeError, match="LockAcquisitionTimeout"):
            await create_team(req)
        gp.get_user_by_user_id.assert_awaited_once_with("user-1", raise_on_error=True)
        gp.batch_upsert_nodes.assert_not_called()

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
        body = {"name": "Team", "userIds": [MEMBER_MONGO_ID_2, MEMBER_MONGO_ID_3], "role": "EDITOR"}
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_graph_user_keys_by_mongo_user_ids.side_effect = _mock_get_graph_user_keys_by_mongo_user_ids
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = True
        gp.commit_transaction.return_value = True
        gp.get_team_with_users.return_value = {"team": "data"}

        resp = await create_team(req)
        assert resp.status_code == 200
        call_args = gp.batch_create_edges.call_args_list[0]
        edges = call_args[0][0]
        assert len(edges) == 3
        member_edges = [e for e in edges if e["role"] != "OWNER"]
        assert {e["from_id"] for e in member_edges} == {"graph-key-2", "graph-key-3"}

    @pytest.mark.asyncio
    async def test_user_roles_format(self):
        body = {
            "name": "Team",
            "userRoles": [
                {"userId": MEMBER_MONGO_ID_2, "role": "EDITOR"},
                {"userId": MEMBER_MONGO_ID_3, "role": "READER"},
            ],
        }
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_graph_user_keys_by_mongo_user_ids.side_effect = _mock_get_graph_user_keys_by_mongo_user_ids
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = True
        gp.commit_transaction.return_value = True
        gp.get_team_with_users.return_value = {}

        resp = await create_team(req)
        assert resp.status_code == 200
        edges = gp.batch_create_edges.call_args_list[0][0][0]
        assert len(edges) == 3

    @pytest.mark.asyncio
    async def test_creator_not_duplicated_in_user_roles(self):
        req = _make_request({
            "name": "Team",
            "userRoles": [{"userId": CREATOR_MONGO_ID, "role": "READER"}],
        })
        req.state.user = {"userId": CREATOR_MONGO_ID, "orgId": "org-1"}
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_graph_user_keys_by_mongo_user_ids.return_value = {}
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = True
        gp.commit_transaction.return_value = True
        gp.get_team_with_users.return_value = {}

        resp = await create_team(req)
        assert resp.status_code == 200
        edges = gp.batch_create_edges.call_args_list[0][0][0]
        assert len(edges) == 1
        assert edges[0]["role"] == "OWNER"
        gp.get_graph_user_keys_by_mongo_user_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_member_raises_400(self):
        body = {
            "name": "Team",
            "userRoles": [{"userId": MEMBER_MONGO_ID_2, "role": "READER"}],
        }
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_graph_user_keys_by_mongo_user_ids.side_effect = ValueError(
            f"Users not found in graph: [{MEMBER_MONGO_ID_2}]"
        )

        with pytest.raises(HTTPException) as exc:
            await create_team(req)
        assert exc.value.status_code == 400
        # people are told who to remove, never the raw ids
        assert exc.value.detail == "Some people you picked are no longer in this workspace. Remove them and try sharing again."

    @pytest.mark.asyncio
    async def test_unknown_user_id_raises_400(self):
        body = {
            "name": "Team",
            "userRoles": [{"userId": "user-key-1", "role": "READER"}],
        }
        req = _make_request(body)
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_graph_user_keys_by_mongo_user_ids.side_effect = ValueError(
            "Users not found in graph: ['user-key-1']"
        )

        with pytest.raises(HTTPException) as exc:
            await create_team(req)
        assert exc.value.status_code == 400
        # people are told who to remove, never the raw ids
        assert exc.value.detail == "Some people you picked are no longer in this workspace. Remove them and try sharing again."

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
    @pytest.mark.parametrize("read_back", [None, RuntimeError("read timed out")], ids=["none", "raises"])
    async def test_a_failed_read_back_after_the_commit_answers_with_the_team_as_written(
        self, read_back: object
    ) -> None:
        """The team is committed by then. The providers answer None to a failed read, and
        any error response makes the dashboard say "try again", which creates a second
        team; so the response carries the team that was written."""
        req = _make_request({
            "name": "Team", "description": "Desc",
            "userRoles": [{"userId": MEMBER_MONGO_ID_2, "role": "EDITOR"}],
        })
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_graph_user_keys_by_mongo_user_ids.side_effect = _mock_get_graph_user_keys_by_mongo_user_ids
        gp.begin_transaction.return_value = "tx-1"
        gp.batch_upsert_nodes.return_value = True
        gp.batch_create_edges.return_value = True
        if isinstance(read_back, Exception):
            gp.get_team_with_users.side_effect = read_back
        else:
            gp.get_team_with_users.return_value = read_back

        with patch("app.api.routes.entity.uuid.uuid4", return_value="fake-uuid"):
            resp = await create_team(req)

        assert resp.status_code == 200
        data = json.loads(resp.body.decode())["data"]
        assert data["id"] == "fake-uuid"
        assert (data["name"], data["description"]) == ("Team", "Desc")
        assert {(m["id"], m["role"]) for m in data["members"]} == {("user-key-1", "OWNER"), ("graph-key-2", "EDITOR")}
        assert data["memberCount"] == 2
        gp.commit_transaction.assert_awaited_once_with("tx-1")
        gp.rollback_transaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_conflict_on_the_edge_write_is_retried_into_one_team(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Neo4j the team node commits before its edges; a retry must finish that team, not add one."""
        monkeypatch.setattr("app.connectors.core.base.data_store.graph_data_store.asyncio.sleep", AsyncMock())
        req = _make_request({"name": "Team"})
        graph = _TeamGraph(conflicts_on=2)
        req.app.state.graph_provider = graph

        resp = await create_team(req)

        assert resp.status_code == 200
        assert len(graph.teams) == 1, graph.teams
        (team_key,) = graph.teams
        assert graph.edges == {("permission", "user-key-1", team_key)}
        assert graph.read_back == [team_key]


class _Conflict(RuntimeError):
    pass


class _TeamGraph:
    """One user's teams on a graph where each statement lands as it runs (Neo4j's
    default) and the write numbered *conflicts_on* hits another writer."""

    def __init__(self, *, conflicts_on: int | None = None) -> None:
        self.logger = MagicMock()
        self.teams: dict[str, dict] = {}
        self.edges: set[tuple[str, str, str]] = set()
        self.read_back: list[str] = []
        self.writes = 0
        self.conflicts_on = conflicts_on

    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False) -> dict:
        return {"_key": "user-key-1"}

    async def begin_transaction(self, read: list | None = None, write: list | None = None) -> str:
        return "txn"

    async def commit_transaction(self, txn: str) -> None:
        pass

    async def rollback_transaction(self, txn: str) -> None:
        pass  # Nothing to undo: every statement already committed.

    def is_write_conflict(self, error: BaseException) -> bool:
        return isinstance(error, _Conflict)

    def is_transient_error(self, error: BaseException) -> bool:
        return False

    def _statement(self) -> None:
        self.writes += 1
        if self.writes == self.conflicts_on:
            raise _Conflict("DeadlockDetected")

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        self._statement()
        for node in nodes:
            self.teams.setdefault(node["_key"], {}).update(node)
        return True

    async def batch_create_edges(self, edges: list[dict], collection: str, transaction: str | None = None) -> bool:
        self._statement()
        self.edges.update((collection, e["from_id"], e["to_id"]) for e in edges)
        return True

    async def get_team_with_users(self, team_id: str, user_key: str) -> dict:
        self.read_back.append(team_id)
        return {"_key": team_id, "users": sorted(f for _, f, t in self.edges if t == team_id)}


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
        gp.get_graph_user_keys_by_mongo_user_ids.side_effect = (
            _mock_get_graph_user_keys_by_mongo_user_ids
        )
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
        body = {
            "name": "T",
            "removeUserIds": [MEMBER_MONGO_ID_2, MEMBER_MONGO_ID_3],
        }
        req, gp = self._setup_authorized_request(body)
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": [],
            "total_owner_count": 1,
        }
        gp.delete_team_member_edges.return_value = ["e1", "e2"]

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        gp.delete_team_member_edges.assert_called_once_with(
            team_id="team-1",
            user_ids=["graph-key-2", "graph-key-3"],
        )

    @pytest.mark.asyncio
    async def test_add_users_legacy_format(self):
        body = {"name": "T", "addUserIds": [MEMBER_MONGO_ID_2], "role": "EDITOR"}
        req, gp = self._setup_authorized_request(body)
        gp.batch_create_edges.return_value = True

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        gp.batch_create_edges.assert_called_once()
        edges = gp.batch_create_edges.call_args[0][0]
        assert edges[0]["from_id"] == "graph-key-2"

    @pytest.mark.asyncio
    async def test_add_users_new_format(self):
        body = {
            "name": "T",
            "addUserRoles": [{"userId": MEMBER_MONGO_ID_2, "role": "READER"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.batch_create_edges.return_value = True

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        edges = gp.batch_create_edges.call_args[0][0]
        assert edges[0]["from_id"] == "graph-key-2"

    @pytest.mark.asyncio
    async def test_add_user_skips_creator(self):
        body = {
            "name": "T",
            "addUserRoles": [{"userId": "user-1", "role": "READER"}],
        }
        req, gp = self._setup_authorized_request(body)

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        gp.get_graph_user_keys_by_mongo_user_ids.assert_called_once()
        gp.batch_create_edges.assert_not_called()

    @pytest.mark.asyncio
    async def test_add_users_missing_member_raises_400(self):
        body = {
            "name": "T",
            "addUserRoles": [{"userId": MEMBER_MONGO_ID_2, "role": "READER"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.get_graph_user_keys_by_mongo_user_ids.side_effect = ValueError(
            f"Users not found in graph: [{MEMBER_MONGO_ID_2}]"
        )

        with pytest.raises(HTTPException) as exc:
            await update_team(req, "team-1")
        assert exc.value.status_code == 400
        # people are told who to remove, never the raw ids
        assert exc.value.detail == "Some people you picked are no longer in this workspace. Remove them and try sharing again."

    @pytest.mark.asyncio
    async def test_update_user_roles(self):
        body = {
            "name": "T",
            "updateUserRoles": [{"userId": MEMBER_MONGO_ID_2, "role": "EDITOR"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"graph-key-2": "READER"},
            "owner_count": 1,
        }
        gp.batch_update_team_member_roles.return_value = [{"userId": "graph-key-2"}]

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        gp.batch_update_team_member_roles.assert_called_once()
        user_roles = gp.batch_update_team_member_roles.call_args.kwargs["user_roles"]
        assert user_roles[0]["userId"] == "graph-key-2"

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
            "updateUserRoles": [{"userId": MEMBER_MONGO_ID_2, "role": "OWNER"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"graph-key-2": "OWNER"},
            "owner_count": 1,
        }

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_update_user_roles_batch_error(self):
        body = {
            "name": "T",
            "updateUserRoles": [{"userId": MEMBER_MONGO_ID_2, "role": "EDITOR"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"graph-key-2": "READER"},
            "owner_count": 1,
        }
        gp.batch_update_team_member_roles.side_effect = RuntimeError("db fail")

        with pytest.raises(HTTPException) as exc:
            await update_team(req, "team-1")
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_add_user_roles_with_empty_user_id(self):
        """Continue when userId is empty in addUserRoles."""
        body = {
            "name": "T",
            "addUserRoles": [
                {"userId": "", "role": "READER"},
                {"userId": MEMBER_MONGO_ID_2, "role": "READER"},
            ],
        }
        req, gp = self._setup_authorized_request(body)
        gp.batch_create_edges.return_value = True

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        edges = gp.batch_create_edges.call_args[0][0]
        assert len(edges) == 1
        assert edges[0]["from_id"] == "graph-key-2"

    @pytest.mark.asyncio
    async def test_add_users_batch_create_returns_falsy(self):
        """Cover branch: batch_create_edges returns falsy."""
        body = {
            "name": "T",
            "addUserRoles": [{"userId": MEMBER_MONGO_ID_2, "role": "READER"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.batch_create_edges.return_value = None

        resp = await update_team(req, "team-1")
        # Should still succeed, just won't log about added users
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_remove_users_returns_empty(self):
        """Cover branch 412->416: deleted_list is empty/falsy."""
        body = {"name": "T", "removeUserIds": [MEMBER_MONGO_ID_2]}
        req, gp = self._setup_authorized_request(body)
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": [],
            "total_owner_count": 1,
        }
        gp.delete_team_member_edges.return_value = []

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        gp.delete_team_member_edges.assert_called_once_with(
            team_id="team-1",
            user_ids=["graph-key-2"],
        )

    @pytest.mark.asyncio
    async def test_remove_users_ignores_falsy_ids(self):
        body = {"name": "T", "removeUserIds": ["", MEMBER_MONGO_ID_2]}
        req, gp = self._setup_authorized_request(body)
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": [],
            "total_owner_count": 1,
        }
        gp.delete_team_member_edges.return_value = ["e1"]

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        gp.get_graph_user_keys_by_mongo_user_ids.assert_called_once()
        assert MEMBER_MONGO_ID_2 in gp.get_graph_user_keys_by_mongo_user_ids.call_args[0][0]
        gp.delete_team_member_edges.assert_called_once_with(
            team_id="team-1",
            user_ids=["graph-key-2"],
        )

    @pytest.mark.asyncio
    async def test_combined_member_ops_single_resolve(self):
        body = {
            "name": "T",
            "removeUserIds": [MEMBER_MONGO_ID_2],
            "updateUserRoles": [{"userId": MEMBER_MONGO_ID_3, "role": "WRITER"}],
            "addUserRoles": [{"userId": MEMBER_MONGO_ID_3, "role": "READER"}],
        }
        req, gp = self._setup_authorized_request(body)
        gp.get_team_owner_removal_info.return_value = {
            "owners_being_removed": [],
            "total_owner_count": 1,
        }
        gp.delete_team_member_edges.return_value = ["e1"]
        gp.get_team_permissions_and_owner_count.return_value = {
            "team": {"_key": "team-1"},
            "permissions": {"graph-key-3": "READER"},
            "owner_count": 1,
        }
        gp.batch_update_team_member_roles.return_value = [{"userId": "graph-key-3"}]
        gp.batch_create_edges.return_value = True

        resp = await update_team(req, "team-1")
        assert resp.status_code == 200
        gp.get_graph_user_keys_by_mongo_user_ids.assert_called_once()
        resolved_ids = set(gp.get_graph_user_keys_by_mongo_user_ids.call_args[0][0])
        assert resolved_ids == {MEMBER_MONGO_ID_2, MEMBER_MONGO_ID_3}

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

class TestDeleteTeam:
    def _setup(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "user-key-1"}
        gp.get_document.return_value = {"_key": "team-1"}
        gp.get_edge.return_value = {"role": "OWNER"}
        return req, gp

    @pytest.mark.asyncio
    async def test_a_team_that_is_not_there_is_not_a_permissions_problem(self):
        """Deleting a team removes its permission edges along with it.

        So a team that is already gone has no edge either, and checking the
        edge first reported that as "you are not allowed" - which tells the
        reader the wrong thing, and makes deleting twice look like a
        permissions failure rather than a no-op.
        """
        req, gp = self._setup()
        gp.get_document.return_value = None

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "team-1")

        assert exc.value.status_code == 404
        assert "permission" not in exc.value.detail.lower()
        gp.delete_nodes.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_team_you_have_no_part_in_looks_the_same_as_one_that_is_gone(self):
        """Otherwise the status code says which team ids exist.

        A caller with no edge to the team is told the same thing as a caller
        naming a team that was never there, so neither answer can be used to
        find out which is which.
        """
        req, gp = self._setup()
        gp.get_edge.return_value = None

        with pytest.raises(HTTPException) as absent_exc:
            gp.get_document.return_value = None
            await delete_team(req, "team-1")

        req, gp = self._setup()
        gp.get_edge.return_value = None
        with pytest.raises(HTTPException) as no_access_exc:
            await delete_team(req, "team-1")

        assert no_access_exc.value.status_code == absent_exc.value.status_code == 404
        assert no_access_exc.value.detail == absent_exc.value.detail

    @pytest.mark.asyncio
    async def test_a_member_who_is_not_the_owner_is_told_why(self):
        """They can already see the team, so naming the real reason gives
        nothing away and saves them hunting for a team that is right there."""
        req, gp = self._setup()
        gp.get_edge.return_value = {"role": "MEMBER"}

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "team-1")

        assert exc.value.status_code == 403
        assert "permission" in exc.value.detail.lower()

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
        """No edge at all now answers 404, so it cannot be told apart from a
        team that does not exist."""
        req, gp = self._setup()
        gp.get_edge.return_value = None

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "team-1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_non_owner_role(self):
        req, gp = self._setup()
        gp.get_edge.return_value = {"role": "MEMBER"}

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "team-1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_system_all_team_cannot_be_deleted(self):
        req, gp = self._setup()

        with pytest.raises(HTTPException) as exc:
            await delete_team(req, "all_org-1")

        assert exc.value.status_code == 403
        assert "All team" in exc.value.detail
        gp.get_edge.assert_not_called()
        gp.delete_all_team_permissions.assert_not_called()

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
        gp.get_user_by_user_id.return_value = {"_key": "uk", "orgId": "org-1"}
        gp.get_user_teams.return_value = ([{"name": "T1"}], 1)

        resp = await get_user_teams(req, search=None, page=1, limit=100, created_by=None)
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
            await get_user_teams(req, search=None, page=1, limit=100, created_by=None)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_exception_raises_500(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk", "orgId": "org-1"}
        gp.get_user_teams.side_effect = RuntimeError("db")

        with pytest.raises(HTTPException) as exc:
            await get_user_teams(req, search=None, page=1, limit=100, created_by=None)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_created_by_mongo_id_resolved_to_graph_key(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.side_effect = [
            {"_key": "uk", "orgId": "org-1"},
            {"_key": "graph-key-2", "userId": MEMBER_MONGO_ID_2, "orgId": "org-1"},
        ]
        gp.get_user_teams.return_value = ([{"name": "T1"}], 1)

        resp = await get_user_teams(
            req,
            search=None,
            page=1,
            limit=100,
            created_by=MEMBER_MONGO_ID_2,
        )
        assert resp.status_code == 200
        assert gp.get_user_by_user_id.call_count == 2
        call_kwargs = gp.get_user_teams.call_args[1]
        assert call_kwargs["created_by"] == "graph-key-2"

    @pytest.mark.asyncio
    async def test_created_by_unknown_mongo_id_raises_400(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.side_effect = [
            {"_key": "uk", "orgId": "org-1"},
            None,
        ]

        with pytest.raises(HTTPException) as exc:
            await get_user_teams(
                req,
                search=None,
                page=1,
                limit=100,
                created_by=MEMBER_MONGO_ID_2,
            )
        assert exc.value.status_code == 400
        # a filter naming someone who isn't here is a lookup miss, not a picked person
        assert exc.value.detail == (
            "This person was removed, or you no longer have access. "
            "Refresh the page and try again."
        )
        assert MEMBER_MONGO_ID_2 not in exc.value.detail
        gp.get_user_teams.assert_not_called()

    @pytest.mark.asyncio
    async def test_pagination(self):
        req = _make_request()
        gp = _graph_provider(req)
        gp.get_user_by_user_id.return_value = {"_key": "uk", "orgId": "org-1"}
        gp.get_user_teams.return_value = ([{"name": "T"}] * 5, 15)

        resp = await get_user_teams(req, search=None, page=2, limit=5, created_by=None)
        content = json.loads(resp.body.decode())
        assert content["pagination"]["pages"] == 3
        assert content["pagination"]["hasNext"] is True
        assert content["pagination"]["hasPrev"] is True

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

        resp = await get_team_users(req, "team-1", search=None, page=1, limit=100)
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


