"""Tests for app.connectors.api.connector_resolvers — OSS edition connector resolver helpers."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

if TYPE_CHECKING:
    from app.connectors.core.registry.connector_registry import ConnectorRegistry


# ---------------------------------------------------------------------------
# resolve_config_service
# ---------------------------------------------------------------------------


class TestResolveConfigService:
    def test_returns_container_config_service(self) -> None:
        from app.connectors.api.connector_resolvers import resolve_config_service

        mock_cs = MagicMock(name="config_service")
        container = MagicMock()
        container.config_service.return_value = mock_cs

        result = resolve_config_service(container, "org-1")
        assert result is mock_cs
        container.config_service.assert_called_once()


# ---------------------------------------------------------------------------
# build_graph_data_store
# ---------------------------------------------------------------------------


class TestBuildGraphDataStore:
    def test_creates_graph_data_store(self) -> None:
        from app.connectors.api.connector_resolvers import build_graph_data_store

        logger_ = MagicMock()
        graph_provider = MagicMock()
        with patch("app.connectors.api.connector_resolvers.GraphDataStore") as MockGDS:
            result = build_graph_data_store(logger_, graph_provider, "org-1")
        MockGDS.assert_called_once()
        assert result is MockGDS.return_value


# ---------------------------------------------------------------------------
# lookup_user_for_records
# ---------------------------------------------------------------------------


class TestLookupUserForRecords:
    async def test_returns_user(self) -> None:
        from app.connectors.api.connector_resolvers import lookup_user_for_records

        user = {"_key": "u1", "name": "Alice", "orgId": "org-1"}
        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value=user)

        result = await lookup_user_for_records(graph_provider, "u1", "org-1")
        assert result["_key"] == "u1"
        # A failed lookup must raise, not read as "user not found".
        graph_provider.get_user_by_user_id.assert_awaited_once_with(user_id="u1", raise_on_error=True)

    async def test_returns_none_when_not_found(self) -> None:
        from app.connectors.api.connector_resolvers import lookup_user_for_records

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value=None)

        result = await lookup_user_for_records(graph_provider, "missing", "org-1")
        assert result is None


# ---------------------------------------------------------------------------
# records_user_id_arg
# ---------------------------------------------------------------------------


class TestRecordsUserIdArg:
    def test_returns_user_key(self) -> None:
        from app.connectors.api.connector_resolvers import records_user_id_arg

        user = {"_key": "u1", "name": "Alice"}
        result = records_user_id_arg(user, "ext-user-123")
        assert result == "u1"


# ---------------------------------------------------------------------------
# authorize_connector_stats
# ---------------------------------------------------------------------------


ORG = "org-1"
CONN = "conn-1"


def _stats_registry(app_doc: dict | None, graph_provider: AsyncMock) -> "ConnectorRegistry":
    """A real registry, so the stats route meets the same read gate as GET /connectors/{id}."""
    from app.connectors.core.registry.connector_registry import ConnectorRegistry

    registry = ConnectorRegistry.__new__(ConnectorRegistry)
    registry.logger = MagicMock()
    registry._graph_provider = graph_provider
    registry._collection_name = "apps"
    registry._connectors = {"GOOGLE_DRIVE": {"appGroup": "Google Workspace"}}
    registry._build_connector_info = MagicMock(return_value={"_key": CONN})
    graph_provider.get_document = AsyncMock(return_value=app_doc)
    return registry


def _app(*, scope: str = "personal", created_by: str = "member-a", type_: str = "GOOGLE_DRIVE") -> dict:
    return {"_key": CONN, "type": type_, "scope": scope, "createdBy": created_by, "orgId": ORG}


async def _authorize(app_doc: dict | None, caller: str, *, is_admin: bool, kb_role: str | None = None) -> None:
    from app.connectors.api.connector_resolvers import authorize_connector_stats

    request = MagicMock()
    request.state.user = {"userId": caller}
    graph_provider = AsyncMock()
    graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": f"{caller}-key"})
    graph_provider.get_user_kb_permission = AsyncMock(return_value=kb_role)
    registry = _stats_registry(app_doc, graph_provider)
    with patch("app.connectors.api.connector_resolvers.is_request_admin", return_value=is_admin):
        await authorize_connector_stats(request, graph_provider, registry, CONN, ORG)


class TestAuthorizeConnectorStats:
    """Stats answer like every other connector read: 404 where GET /connectors/{id} refuses."""

    @pytest.mark.parametrize(
        ("app_doc", "caller", "is_admin"),
        [
            pytest.param(_app(), "member-a", False, id="creator-personal"),
            pytest.param(_app(scope="team", created_by="admin"), "admin", True, id="admin-team"),
            pytest.param(_app(scope="team"), "member-a", False, id="creator-team"),
        ],
    )
    async def test_a_connector_the_caller_can_open_is_allowed(
        self, app_doc: dict, caller: str, is_admin: bool
    ) -> None:
        await _authorize(app_doc, caller, is_admin=is_admin)

    @pytest.mark.parametrize(
        ("app_doc", "caller", "is_admin"),
        [
            pytest.param(None, "member-a", False, id="missing"),
            pytest.param({**_app(scope="team"), "orgId": "org-2"}, "admin", True, id="another-org"),
            pytest.param(_app(), "admin", True, id="admin-on-members-personal"),
            pytest.param(_app(), "member-b", False, id="member-on-members-personal"),
            pytest.param(_app(scope="team"), "member-b", False, id="member-on-team-not-theirs"),
            pytest.param(_app(type_="RETIRED_TYPE"), "member-a", False, id="type-not-registered"),
        ],
    )
    async def test_a_connector_the_caller_cannot_open_answers_404(
        self, app_doc: dict | None, caller: str, is_admin: bool
    ) -> None:
        from app.utils.user_messages import not_found

        with pytest.raises(HTTPException) as exc_info:
            await _authorize(app_doc, caller, is_admin=is_admin)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == not_found("This connector")

    async def test_kb_reader_is_allowed(self) -> None:
        await _authorize(_app(type_="KB"), "member-b", is_admin=False, kb_role="READER")

    async def test_kb_without_any_role_answers_404(self) -> None:
        from app.utils.user_messages import not_found

        with pytest.raises(HTTPException) as exc_info:
            await _authorize(_app(type_="KB"), "member-b", is_admin=False, kb_role=None)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == not_found("This connector")

    async def test_kb_role_below_reader_stays_403(self) -> None:
        """The caller can see this collection, so the refusal is about permission, not existence."""
        with pytest.raises(HTTPException) as exc_info:
            await _authorize(_app(type_="KB"), "member-b", is_admin=False, kb_role="COMMENTER")

        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# strip_redacted_fields (OSS passthrough)
# ---------------------------------------------------------------------------


class TestStripRedactedFields:
    def test_returns_copy(self) -> None:
        from app.connectors.api.connector_resolvers import strip_redacted_fields

        data = {"clientId": "id", "clientSecret": "secret"}
        result = strip_redacted_fields(data)
        assert result == data
        assert result is not data

    def test_handles_empty(self) -> None:
        from app.connectors.api.connector_resolvers import strip_redacted_fields

        result = strip_redacted_fields({})
        assert result == {}


# ---------------------------------------------------------------------------
# mask_oauth_config_for_response
# ---------------------------------------------------------------------------


class TestMaskOauthConfigForResponse:
    def test_admin_gets_raw_config(self) -> None:
        from app.connectors.api.connector_resolvers import mask_oauth_config_for_response

        oauth_config = {"config": {"clientId": "id", "clientSecret": "s3c"}}
        result = mask_oauth_config_for_response(oauth_config, "org-1", is_admin=True)
        assert result["config"]["clientId"] == "id"
        assert result["inherited"] is False

    def test_non_admin_gets_empty_config(self) -> None:
        from app.connectors.api.connector_resolvers import mask_oauth_config_for_response

        oauth_config = {"config": {"clientId": "id", "clientSecret": "s3c"}}
        result = mask_oauth_config_for_response(oauth_config, "org-1", is_admin=False)
        assert result["config"] == {}
        assert result["inherited"] is False


# ---------------------------------------------------------------------------
# resolve_oauth_configs
# ---------------------------------------------------------------------------


class TestResolveOauthConfigs:
    async def test_returns_configs(self) -> None:
        from app.connectors.api.connector_resolvers import resolve_oauth_configs

        expected = [{"_id": "c1", "config": {}}]
        container = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=expected)

        result = await resolve_oauth_configs(
            container, "/services/oauths/google", "org-1", config_service, scope="org"
        )
        assert isinstance(result, list)

    async def test_returns_empty_on_no_data(self) -> None:
        from app.connectors.api.connector_resolvers import resolve_oauth_configs

        container = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])

        result = await resolve_oauth_configs(
            container, "/services/oauths/google", "org-1", config_service, scope="org"
        )
        assert result == []


# ---------------------------------------------------------------------------
# resolve_oauth_config
# ---------------------------------------------------------------------------


class TestResolveOauthConfig:
    async def test_finds_by_id_and_org(self) -> None:
        from app.connectors.api.connector_resolvers import resolve_oauth_config

        expected = {"_id": "cfg-1", "orgId": "org-1"}
        container = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[expected])

        result = await resolve_oauth_config(
            container, "/services/oauths/google", "org-1", "cfg-1", config_service
        )
        assert result is not None
        assert result["_id"] == "cfg-1"

    async def test_returns_none_when_not_found(self) -> None:
        from app.connectors.api.connector_resolvers import resolve_oauth_config

        container = MagicMock()
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "other", "orgId": "org-1"},
        ])

        result = await resolve_oauth_config(
            container, "/services/oauths/google", "org-1", "missing", config_service
        )
        assert result is None


# ---------------------------------------------------------------------------
# forbid_inherited_oauth_mutation (OSS no-op)
# ---------------------------------------------------------------------------


class TestForbidInheritedOauthMutation:
    async def test_no_op(self) -> None:
        from app.connectors.api.connector_resolvers import forbid_inherited_oauth_mutation

        await forbid_inherited_oauth_mutation(MagicMock(), "/path", "org-1", "cfg-1")


# ---------------------------------------------------------------------------
# default_connector_scope
# ---------------------------------------------------------------------------


class TestDefaultConnectorScope:
    def test_returns_cleaned_scope(self) -> None:
        from app.connectors.api.connector_resolvers import default_connector_scope

        connector_registry = MagicMock()
        result = default_connector_scope("google", connector_registry, "org")
        assert isinstance(result, str)

    def test_with_body_scope(self) -> None:
        from app.connectors.api.connector_resolvers import default_connector_scope

        connector_registry = MagicMock()
        result = default_connector_scope("google", connector_registry, "personal")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# resolve_shared_oauth_config_for_flow
# ---------------------------------------------------------------------------


class TestResolveSharedOauthConfigForFlow:
    async def test_local_lookup_found(self) -> None:
        from app.connectors.api.connector_resolvers import resolve_shared_oauth_config_for_flow

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "cfg-1", "orgId": "org-1", "config": {"clientId": "cid"}},
        ])

        result = await resolve_shared_oauth_config_for_flow(
            {}, "cfg-1", "org-1", "/services/oauths/google", config_service
        )
        assert result is not None
        assert result["_id"] == "cfg-1"

    async def test_local_lookup_not_found(self) -> None:
        from app.connectors.api.connector_resolvers import resolve_shared_oauth_config_for_flow

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[
            {"_id": "other", "orgId": "org-1"},
        ])

        result = await resolve_shared_oauth_config_for_flow(
            {}, "missing", "org-1", "/services/oauths/google", config_service
        )
        assert result is None

    async def test_non_list_response(self) -> None:
        from app.connectors.api.connector_resolvers import resolve_shared_oauth_config_for_flow

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="bad")

        result = await resolve_shared_oauth_config_for_flow(
            {}, "cfg-1", "org-1", "/path", config_service
        )
        assert result is None


# ---------------------------------------------------------------------------
# schedule_token_refresh_kwargs (OSS stub)
# ---------------------------------------------------------------------------


class TestScheduleTokenRefreshKwargs:
    def test_returns_empty_dict(self) -> None:
        from app.connectors.api.connector_resolvers import schedule_token_refresh_kwargs

        result = schedule_token_refresh_kwargs("org-1")
        assert result == {}
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# oauth_create_extra_fields (OSS stub)
# ---------------------------------------------------------------------------


class TestOauthCreateExtraFields:
    def test_returns_empty_dict(self) -> None:
        from app.connectors.api.connector_resolvers import oauth_create_extra_fields

        result = oauth_create_extra_fields(connector_scope="org", oauth_instance_name="test")
        assert result == {}


# ---------------------------------------------------------------------------
# ensure_oauth_default (OSS no-op)
# ---------------------------------------------------------------------------


class TestEnsureOauthDefault:
    def test_no_op(self) -> None:
        from app.connectors.api.connector_resolvers import ensure_oauth_default

        ensure_oauth_default([], {"_id": "c1"}, "org-1")


# ---------------------------------------------------------------------------
# annotate_oauth_inheritance (OSS no-op)
# ---------------------------------------------------------------------------


class TestAnnotateOauthInheritance:
    def test_no_op(self) -> None:
        from app.connectors.api.connector_resolvers import annotate_oauth_inheritance

        annotate_oauth_inheritance({}, {"_id": "c1"}, "org-1")


# ---------------------------------------------------------------------------
# filter_oauth_configs_for_list (OSS passthrough)
# ---------------------------------------------------------------------------


class TestFilterOauthConfigsForList:
    def test_returns_list_as_is(self) -> None:
        from app.connectors.api.connector_resolvers import filter_oauth_configs_for_list

        configs = [{"_id": "c1"}, {"_id": "c2"}]
        result = filter_oauth_configs_for_list(configs, "org-1")
        assert result == configs

    def test_returns_empty_list(self) -> None:
        from app.connectors.api.connector_resolvers import filter_oauth_configs_for_list

        result = filter_oauth_configs_for_list([], "org-1")
        assert result == []
