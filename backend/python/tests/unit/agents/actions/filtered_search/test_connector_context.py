"""Unit tests for `connector_context`: resolving a connector_id to its
graph type and to an authenticated DataSource built from the connector's
org-level credentials via the adapter's ``build_datasource``."""

from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.actions.filtered_search import connector_context
from app.agents.actions.filtered_search.adapter import NativeFilterAdapter
from app.agents.actions.knowledge_graph.catalog import ConnectorInfo


class TestGetUserKey:
    async def test_returns_key_field_when_present(self) -> None:
        graph = MagicMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"_key": "k1", "id": "other"})
        assert await connector_context.get_user_key(graph, "u1") == "k1"

    async def test_falls_back_to_id_when_no_key(self) -> None:
        graph = MagicMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"id": "fallback"})
        assert await connector_context.get_user_key(graph, "u1") == "fallback"

    async def test_returns_none_when_user_missing(self) -> None:
        graph = MagicMock()
        graph.get_user_by_user_id = AsyncMock(return_value=None)
        assert await connector_context.get_user_key(graph, "u1") is None

    async def test_returns_none_on_exception(self) -> None:
        graph = MagicMock()
        graph.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("boom"))
        assert await connector_context.get_user_key(graph, "u1") is None


class TestResolveConnectorType:
    async def test_no_graph_provider_returns_none(self) -> None:
        assert await connector_context.resolve_connector_type({}, "c1") is None

    async def test_resolves_type_from_catalog(self, monkeypatch) -> None:
        monkeypatch.setattr(connector_context, "get_user_key", AsyncMock(return_value="k1"))
        catalog = MagicMock()
        catalog.connectors = [ConnectorInfo(id="c1", name="Jira", type="JIRA")]
        monkeypatch.setattr(
            "app.agents.actions.knowledge_graph.catalog.ConnectorCatalog.build",
            AsyncMock(return_value=catalog),
        )
        state = {"graph_provider": MagicMock(), "org_id": "o1", "user_id": "u1"}
        result = await connector_context.resolve_connector_type(state, "c1")
        assert result == "JIRA"

    async def test_unknown_connector_id_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(connector_context, "get_user_key", AsyncMock(return_value="k1"))
        catalog = MagicMock()
        catalog.connectors = [ConnectorInfo(id="other", name="Jira", type="JIRA")]
        monkeypatch.setattr(
            "app.agents.actions.knowledge_graph.catalog.ConnectorCatalog.build",
            AsyncMock(return_value=catalog),
        )
        state = {"graph_provider": MagicMock(), "org_id": "o1", "user_id": "u1"}
        assert await connector_context.resolve_connector_type(state, "c1") is None

    async def test_unresolvable_user_key_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(connector_context, "get_user_key", AsyncMock(return_value=None))
        state = {"graph_provider": MagicMock(), "org_id": "o1", "user_id": "u1"}
        assert await connector_context.resolve_connector_type(state, "c1") is None


class TestResolveSelfIdentity:
    async def test_no_graph_provider_returns_none(self) -> None:
        assert await connector_context.resolve_self_identity({}, "c1") is None

    async def test_resolves_source_user_id_via_email(self) -> None:
        graph = MagicMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"email": "a@x.com"})
        app_user = MagicMock()
        app_user.source_user_id = "acc-123"
        graph.get_app_user_by_email = AsyncMock(return_value=app_user)
        state = {"graph_provider": graph, "user_id": "u1"}

        result = await connector_context.resolve_self_identity(state, "c1")

        assert result == "acc-123"
        graph.get_app_user_by_email.assert_awaited_once_with(email="a@x.com", connector_id="c1")

    async def test_returns_none_when_session_user_has_no_email(self) -> None:
        graph = MagicMock()
        graph.get_user_by_user_id = AsyncMock(return_value={})
        state = {"graph_provider": graph, "user_id": "u1"}
        assert await connector_context.resolve_self_identity(state, "c1") is None

    async def test_returns_none_when_no_app_user_on_connector(self) -> None:
        graph = MagicMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"email": "a@x.com"})
        graph.get_app_user_by_email = AsyncMock(return_value=None)
        state = {"graph_provider": graph, "user_id": "u1"}
        assert await connector_context.resolve_self_identity(state, "c1") is None

    async def test_returns_none_on_user_lookup_exception(self) -> None:
        graph = MagicMock()
        graph.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("boom"))
        state = {"graph_provider": graph, "user_id": "u1"}
        assert await connector_context.resolve_self_identity(state, "c1") is None

    async def test_returns_none_on_app_user_lookup_exception(self) -> None:
        graph = MagicMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"email": "a@x.com"})
        graph.get_app_user_by_email = AsyncMock(side_effect=RuntimeError("boom"))
        state = {"graph_provider": graph, "user_id": "u1"}
        assert await connector_context.resolve_self_identity(state, "c1") is None


def _fake_adapter_cls(datasource_stub):
    """Return a minimal NativeFilterAdapter subclass whose build_datasource
    returns *datasource_stub* — avoids importing real client/datasource
    classes in a unit test."""

    class _Adapter(NativeFilterAdapter):
        @classmethod
        def capabilities(cls):
            return MagicMock(connector_type="JIRA")

        @classmethod
        def validate_query(cls, query):
            return None

        @classmethod
        def has_self_reference(cls, query):
            return False

        @classmethod
        def substitute_identity(cls, query, source_user_id):
            return query

        async def execute(self, query, client, page):
            raise NotImplementedError

        @classmethod
        async def build_datasource(cls, config_service, connector_id, logger):
            return datasource_stub

    return _Adapter


class TestResolveClientForConnector:
    async def test_returns_none_when_connector_type_unresolved(self, monkeypatch) -> None:
        monkeypatch.setattr(connector_context, "resolve_connector_type", AsyncMock(return_value=None))
        assert await connector_context.resolve_client_for_connector({}, "c1") is None

    async def test_builds_datasource_from_connector_credentials(self, monkeypatch) -> None:
        monkeypatch.setattr(connector_context, "resolve_connector_type", AsyncMock(return_value="JIRA"))
        datasource = object()
        adapter_cls = _fake_adapter_cls(datasource)
        with patch("app.agents.actions.filtered_search.registry.FilterAdapterRegistry.get", return_value=adapter_cls):
            state = {"config_service": MagicMock()}
            result = await connector_context.resolve_client_for_connector(state, "c1")
        assert result == ("JIRA", datasource)

    async def test_returns_none_when_no_adapter_registered(self, monkeypatch) -> None:
        monkeypatch.setattr(connector_context, "resolve_connector_type", AsyncMock(return_value="UNKNOWN"))
        with patch("app.agents.actions.filtered_search.registry.FilterAdapterRegistry.get", return_value=None):
            state = {"config_service": MagicMock()}
            assert await connector_context.resolve_client_for_connector(state, "c1") is None

    async def test_returns_none_when_config_service_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(connector_context, "resolve_connector_type", AsyncMock(return_value="JIRA"))
        adapter_cls = _fake_adapter_cls(object())
        with patch("app.agents.actions.filtered_search.registry.FilterAdapterRegistry.get", return_value=adapter_cls):
            state = {}
            assert await connector_context.resolve_client_for_connector(state, "c1") is None

    async def test_returns_none_on_build_datasource_exception(self, monkeypatch) -> None:
        monkeypatch.setattr(connector_context, "resolve_connector_type", AsyncMock(return_value="JIRA"))

        class _FailingAdapter(NativeFilterAdapter):
            @classmethod
            def capabilities(cls):
                return MagicMock(connector_type="JIRA")
            @classmethod
            def validate_query(cls, query):
                return None
            @classmethod
            def has_self_reference(cls, query):
                return False
            @classmethod
            def substitute_identity(cls, query, source_user_id):
                return query
            async def execute(self, query, client, page):
                raise NotImplementedError
            @classmethod
            async def build_datasource(cls, config_service, connector_id, logger):
                raise RuntimeError("auth failed")

        with patch("app.agents.actions.filtered_search.registry.FilterAdapterRegistry.get", return_value=_FailingAdapter):
            state = {"config_service": MagicMock()}
            assert await connector_context.resolve_client_for_connector(state, "c1") is None

    async def test_caches_datasource_per_connector_id(self, monkeypatch) -> None:
        """Second call for the same connector_id reuses the cached DataSource
        without calling build_datasource again."""
        monkeypatch.setattr(connector_context, "resolve_connector_type", AsyncMock(return_value="JIRA"))
        datasource = object()
        call_count = 0

        class _CountingAdapter(NativeFilterAdapter):
            @classmethod
            def capabilities(cls):
                return MagicMock(connector_type="JIRA")
            @classmethod
            def validate_query(cls, query):
                return None
            @classmethod
            def has_self_reference(cls, query):
                return False
            @classmethod
            def substitute_identity(cls, query, source_user_id):
                return query
            async def execute(self, query, client, page):
                raise NotImplementedError
            @classmethod
            async def build_datasource(cls, config_service, connector_id, logger):
                nonlocal call_count
                call_count += 1
                return datasource

        with patch("app.agents.actions.filtered_search.registry.FilterAdapterRegistry.get", return_value=_CountingAdapter):
            state = {"config_service": MagicMock()}
            r1 = await connector_context.resolve_client_for_connector(state, "c1")
            r2 = await connector_context.resolve_client_for_connector(state, "c1")

        assert r1 == r2 == ("JIRA", datasource)
        assert call_count == 1
