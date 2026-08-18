"""Unit tests for `FilterAdapterRegistry` — normalization is the whole
reason `Connectors` enum values with spaces ("JIRA DATA CENTER") and
underscore-style registration keys ("JIRA_DATA_CENTER") resolve to the
same adapter."""

from app.agents.actions.filtered_search.adapter import NativeFilterAdapter
from app.agents.actions.filtered_search.registry import FilterAdapterRegistry


class _DummyAdapter(NativeFilterAdapter):
    @classmethod
    def capabilities(cls):  # pragma: no cover - not exercised here
        raise NotImplementedError

    @classmethod
    def validate_query(cls, query):  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def has_self_reference(cls, query):  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def substitute_identity(cls, query, source_user_id):  # pragma: no cover
        raise NotImplementedError

    async def execute(self, query, client, page):  # pragma: no cover
        raise NotImplementedError


_original_adapters: dict = {}


def setup_function() -> None:
    # Snapshot/restore rather than `reset()` + leave empty: the real
    # Jira/Confluence/Slack adapters register themselves as an import-time
    # side effect (`filtered_search/adapters/__init__.py`) that only runs
    # once per process — wiping the registry here would starve every other
    # test module that imports later in the same session.
    _original_adapters.clear()
    _original_adapters.update(FilterAdapterRegistry._adapters)
    FilterAdapterRegistry.reset()


def teardown_function() -> None:
    FilterAdapterRegistry.reset()
    FilterAdapterRegistry._adapters.update(_original_adapters)


def test_register_and_get_exact_match() -> None:
    FilterAdapterRegistry.register("JIRA", _DummyAdapter)
    assert FilterAdapterRegistry.get("JIRA") is _DummyAdapter
    assert FilterAdapterRegistry.is_registered("JIRA") is True


def test_get_is_case_insensitive() -> None:
    FilterAdapterRegistry.register("JIRA", _DummyAdapter)
    assert FilterAdapterRegistry.get("jira") is _DummyAdapter


def test_spaces_and_underscores_are_equivalent() -> None:
    """`Connectors.JIRA_DATA_CENTER.value` is "JIRA DATA CENTER" (a space);
    registration commonly uses the underscore spelling — both must resolve
    to the same adapter."""
    FilterAdapterRegistry.register("JIRA_DATA_CENTER", _DummyAdapter)
    assert FilterAdapterRegistry.get("JIRA DATA CENTER") is _DummyAdapter
    assert FilterAdapterRegistry.is_registered("jira data center") is True


def test_unregistered_type_returns_none() -> None:
    assert FilterAdapterRegistry.get("NOT_A_CONNECTOR") is None
    assert FilterAdapterRegistry.is_registered("NOT_A_CONNECTOR") is False


def test_all_connector_types_lists_normalized_keys() -> None:
    FilterAdapterRegistry.register("JIRA DATA CENTER", _DummyAdapter)
    assert "JIRA_DATA_CENTER" in FilterAdapterRegistry.all_connector_types()
