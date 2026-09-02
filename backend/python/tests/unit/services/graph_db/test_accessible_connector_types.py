"""`get_accessible_connector_types` across both graph backends.

Parametrized over Arango and Neo4j deliberately: the query path calls it
through ``IGraphDBProvider``, so the two must agree on shape and on failure
behaviour or a deployment on one backend silently searches differently from
the other.

It narrows a multi-collection search; it does not gate results. That makes its
failure mode asymmetric — returning too few types would hide data, so every
uncertain case widens instead.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.interface.graph_db_provider import (
    IGraphDBProvider,
    _distinct_connector_types,
)

pytestmark = pytest.mark.asyncio

_UNSET = object()


def _arango_provider(apps, *, user=_UNSET, raises=None):
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()
    if user is _UNSET:
        user = {"_key": "user-key"}
    provider.get_user_by_user_id = AsyncMock(return_value=user)
    provider.get_user_apps = (
        AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=apps)
    )
    return provider


def _neo4j_provider(apps, *, raises=None):
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    provider.get_user_apps = (
        AsyncMock(side_effect=raises) if raises else AsyncMock(return_value=apps)
    )
    return provider


PROVIDERS = {"arango": _arango_provider, "neo4j": _neo4j_provider}


@pytest.fixture(params=sorted(PROVIDERS), ids=sorted(PROVIDERS))
def make_provider(request):
    return PROVIDERS[request.param]


class TestBothBackendsAgree:
    async def test_returns_distinct_connector_types(self, make_provider):
        provider = make_provider(
            [
                {"_key": "a1", "type": "DRIVE"},
                {"_key": "a2", "type": "SLACK"},
            ]
        )
        assert set(await provider.get_accessible_connector_types("u", "org")) == {
            "DRIVE",
            "SLACK",
        }

    async def test_two_instances_of_one_type_collapse(self, make_provider):
        """Two Drive connections are one collection to search."""
        provider = make_provider(
            [
                {"_key": "a1", "type": "DRIVE"},
                {"_key": "a2", "type": "DRIVE"},
            ]
        )
        assert await provider.get_accessible_connector_types("u", "org") == ["DRIVE"]

    async def test_knowledge_base_is_included(self, make_provider):
        """KB is a connector type like any other and holds a collection of its
        own — excluding it would hide every uploaded document."""
        provider = make_provider([{"_key": "kb1", "type": "KB"}])
        assert await provider.get_accessible_connector_types("u", "org") == ["KB"]

    async def test_no_apps_yields_nothing(self, make_provider):
        provider = make_provider([])
        assert await provider.get_accessible_connector_types("u", "org") == []

    async def test_apps_without_a_type_are_skipped(self, make_provider):
        """An untyped app cannot narrow anything, and guessing would narrow to
        a collection that does not exist."""
        provider = make_provider(
            [{"_key": "a1"}, {"_key": "a2", "type": None}, {"_key": "a3", "type": "JIRA"}]
        )
        assert await provider.get_accessible_connector_types("u", "org") == ["JIRA"]

    async def test_a_graph_failure_widens_rather_than_raising(self, make_provider):
        """Narrowing is an optimization: a failure must cost a wider search,
        never the search itself."""
        provider = make_provider([], raises=ConnectionError("graph down"))
        assert await provider.get_accessible_connector_types("u", "org") == []
        provider.logger.warning.assert_called()

    async def test_result_order_is_stable(self, make_provider):
        provider = make_provider(
            [{"_key": f"a{i}", "type": t} for i, t in enumerate(["SLACK", "DRIVE", "JIRA"])]
        )
        first = await provider.get_accessible_connector_types("u", "org")
        assert first == await provider.get_accessible_connector_types("u", "org")


class TestArangoUserResolution:
    """Arango's get_user_apps takes the user document _key, not the userId."""

    async def test_resolves_the_user_key_before_the_lookup(self):
        provider = _arango_provider(
            [{"_key": "a1", "type": "DRIVE"}], user={"_key": "doc-key-42"}
        )
        await provider.get_accessible_connector_types("external-user-id", "org")
        provider.get_user_apps.assert_awaited_once_with("doc-key-42")

    async def test_an_unknown_user_yields_nothing(self):
        provider = _arango_provider([], user=None)
        assert await provider.get_accessible_connector_types("nobody", "org") == []
        provider.get_user_apps.assert_not_awaited()

    async def test_a_user_without_a_key_yields_nothing(self):
        provider = _arango_provider([], user={"name": "no key here"})
        assert await provider.get_accessible_connector_types("u", "org") == []


class TestInterfaceContract:
    def test_both_providers_implement_the_abstract_method(self):
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        for cls in (ArangoHTTPProvider, Neo4jProvider):
            assert "get_accessible_connector_types" in vars(cls), cls.__name__

    def test_the_method_is_declared_on_the_interface(self):
        """Callers reach it through IGraphDBProvider, so a provider that
        forgot it must fail at construction rather than at query time."""
        assert hasattr(IGraphDBProvider, "get_accessible_connector_types")
        assert getattr(
            IGraphDBProvider.get_accessible_connector_types,
            "__isabstractmethod__",
            False,
        )


class TestDistinctConnectorTypesHelper:
    """Shared by both backends, so its edge cases are tested once."""

    def test_preserves_first_seen_order(self):
        apps = [{"type": "B"}, {"type": "A"}, {"type": "B"}]
        assert _distinct_connector_types(apps) == ["B", "A"]

    def test_tolerates_none(self):
        assert _distinct_connector_types(None) == []

    def test_skips_non_dict_entries(self):
        assert _distinct_connector_types([None, "nope", {"type": "OK"}]) == ["OK"]

    def test_coerces_to_string(self):
        assert _distinct_connector_types([{"type": 7}]) == ["7"]
