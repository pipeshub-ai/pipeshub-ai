"""Tests for the knowledge-graph endpoints in app.connectors.api.router.

`knowledge_graph_navigate` and `knowledge_graph_lookup` are the HTTP
counterparts of the agent's knowledgegraph__navigate / __lookup_record tools.
Handlers are called directly with `graph_provider` supplied as a kwarg, which
bypasses `Depends(get_graph_provider)` — the same style as the sibling
test_router_part*.py files.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.agents.actions.knowledge_graph.models import (
    LookupMatch,
    LookupResult,
    NavigationView,
    NodeRef,
    NodeRow,
    PaginationInfo,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import (
    knowledge_graph_lookup,
    knowledge_graph_navigate,
)

ROUTER = "app.connectors.api.router"


# ============================================================================
# Helpers
# ============================================================================


async def _call_navigate(*, graph_provider: Any, request: Any | None = None, **overrides):
    """Invoke the handler with every Query default filled in.

    Calling it directly bypasses FastAPI, so unsupplied parameters would
    otherwise arrive as raw `Query` objects rather than their defaults.
    """
    kwargs: dict[str, Any] = {
        "node_id": None,
        "page": 1,
        "limit": 50,
        "depth": 1,
        "node_types": None,
        "created_after": None,
        "created_before": None,
        "modified_after": None,
        "modified_before": None,
    }
    kwargs.update(overrides)
    return await knowledge_graph_navigate(
        request=request or _mock_request(),
        graph_provider=graph_provider,
        **kwargs,
    )


async def _call_lookup(
    *,
    graph_provider: Any,
    identifiers: list[str],
    request: Any | None = None,
    **overrides,
):
    kwargs: dict[str, Any] = {"connector_name": None}
    kwargs.update(overrides)
    return await knowledge_graph_lookup(
        request=request or _mock_request(),
        graph_provider=graph_provider,
        identifiers=identifiers,
        **kwargs,
    )


def _mock_request(*, user: dict | None = None, container: Any | None = None):
    """Minimal mock FastAPI request carrying state.user and app.container."""
    req = MagicMock()
    user_data = user or {"userId": "user-1", "orgId": "org-1"}
    req.state = MagicMock()
    req.state.user = MagicMock()
    req.state.user.get = lambda k, default=None: user_data.get(k, default)
    req.app = MagicMock()
    req.app.container = container or MagicMock()
    return req


_FOUND_USER = {"_key": "ukey-1"}


def _mock_graph_provider(user_doc: dict | None = _FOUND_USER) -> AsyncMock:
    gp = AsyncMock()
    gp.get_user_by_user_id = AsyncMock(return_value=user_doc)
    return gp


def _mock_catalog(*, connector_ids: list[str] | None = None, empty: bool = False):
    catalog = MagicMock()
    ids = connector_ids if connector_ids is not None else ["conn-1"]
    catalog.connector_ids = MagicMock(return_value=ids)
    catalog.is_empty = MagicMock(return_value=empty)
    catalog.connectors = [MagicMock(id=cid, name=f"App {cid}") for cid in ids]
    return catalog


def _node_row(**overrides) -> NodeRow:
    defaults = {
        "id": "rec-1",
        "name": "Design doc",
        "node_type": "record",
        "sub_type": "FILE",
        "is_record": True,
        "has_children": False,
        "detail": None,
    }
    defaults.update(overrides)
    return NodeRow(**defaults)


def _navigation_view(**overrides) -> NavigationView:
    defaults = {
        "current": NodeRef(
            id="grp-1", name="Project A", node_type="recordGroup",
            sub_type="PROJECT", is_record=False,
        ),
        "breadcrumbs": [],
        "rows": [_node_row()],
        "related": [],
        "pagination": PaginationInfo(page=1, limit=50, total=1, has_next=False, has_prev=False),
        "web_url": None,
        "indexing_status": None,
        "connector": "JIRA",
    }
    defaults.update(overrides)
    return NavigationView(**defaults)


def _lookup_result(**overrides) -> LookupResult:
    defaults = {
        "matches": [
            LookupMatch(
                id="rec-1", name="PA-1787", record_type="TICKET",
                connector_name="JIRA", web_url=None, indexing_status="COMPLETED",
                identifier_used="PA-1787",
            )
        ],
        "ambiguous": False,
        "not_found_identifiers": [],
        "searched_connectors": {},
    }
    defaults.update(overrides)
    return LookupResult(**defaults)


# ============================================================================
# knowledge_graph_navigate
# ============================================================================


class TestKnowledgeGraphNavigate:
    @pytest.mark.asyncio
    async def test_returns_structured_fields_and_rendered_text(self) -> None:
        view = _navigation_view()
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=_mock_catalog())), \
             patch(f"{ROUTER}.GraphNavigator") as nav_cls:
            nav_cls.return_value.navigate = AsyncMock(return_value=view)
            result = await _call_navigate(
                graph_provider=_mock_graph_provider(), node_id="grp-1",
            )

        assert result["rows"][0]["id"] == "rec-1"
        assert result["pagination"]["total"] == 1
        assert isinstance(result["text"], str) and result["text"].strip()

    @pytest.mark.asyncio
    async def test_catalog_is_built_from_a_fresh_empty_state(self) -> None:
        """Guards cross-request isolation: ConnectorCatalog.build caches into the
        dict it is given, so it must never receive a shared/populated one."""
        build = AsyncMock(return_value=_mock_catalog())
        with patch(f"{ROUTER}.ConnectorCatalog.build", build), \
             patch(f"{ROUTER}.GraphNavigator") as nav_cls:
            nav_cls.return_value.navigate = AsyncMock(return_value=_navigation_view())
            await _call_navigate(graph_provider=_mock_graph_provider())

        assert build.call_args.args[0] == {}
        assert build.call_args.kwargs["user_key"] == "ukey-1"
        assert build.call_args.kwargs["org_id"] == "org-1"

    @pytest.mark.asyncio
    async def test_naive_datetime_is_rejected_with_plain_message(self) -> None:
        """The 400 detail must be the parser's `message`, not its raw JSON envelope."""
        with pytest.raises(HTTPException) as exc:
            await _call_navigate(
                graph_provider=_mock_graph_provider(),
                created_after="2026-01-15T00:00:00",
            )

        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "no timezone" in exc.value.detail
        assert not exc.value.detail.startswith("{")

    @pytest.mark.asyncio
    async def test_unknown_user_is_404_and_never_navigates(self) -> None:
        with patch(f"{ROUTER}.GraphNavigator") as nav_cls:
            with pytest.raises(HTTPException) as exc:
                await _call_navigate(graph_provider=_mock_graph_provider(user_doc=None))

        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value
        nav_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_issue_key_node_id_is_resolved_before_navigating(self) -> None:
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=_mock_catalog())), \
             patch(f"{ROUTER}.RecordResolver") as resolver_cls, \
             patch(f"{ROUTER}.GraphNavigator") as nav_cls:
            resolver_cls.return_value.resolve_many = AsyncMock(return_value=_lookup_result())
            navigate = AsyncMock(return_value=_navigation_view())
            nav_cls.return_value.navigate = navigate

            await _call_navigate(
                graph_provider=_mock_graph_provider(), node_id="PA-1787",
            )

        resolver_cls.return_value.resolve_many.assert_awaited_once_with(["PA-1787"])
        assert navigate.call_args.kwargs["node_id"] == "rec-1"

    @pytest.mark.asyncio
    async def test_plain_node_id_skips_resolution(self) -> None:
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=_mock_catalog())), \
             patch(f"{ROUTER}.RecordResolver") as resolver_cls, \
             patch(f"{ROUTER}.GraphNavigator") as nav_cls:
            navigate = AsyncMock(return_value=_navigation_view())
            nav_cls.return_value.navigate = navigate

            await _call_navigate(
                graph_provider=_mock_graph_provider(), node_id="  grp-1  ",
            )

        resolver_cls.assert_not_called()
        assert navigate.call_args.kwargs["node_id"] == "grp-1"

    @pytest.mark.asyncio
    async def test_time_filters_reach_the_navigator_as_epoch_bounds(self) -> None:
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=_mock_catalog())), \
             patch(f"{ROUTER}.GraphNavigator") as nav_cls:
            navigate = AsyncMock(return_value=_navigation_view())
            nav_cls.return_value.navigate = navigate

            await _call_navigate(
                graph_provider=_mock_graph_provider(),
                created_after="2026-01-01",
                modified_before="2026-02-01",
            )

        kwargs = navigate.call_args.kwargs
        assert kwargs["created_at"]["gte"] is not None
        assert kwargs["created_at"]["lte"] is None
        assert kwargs["updated_at"]["lte"] is not None
        assert kwargs["node_types"] is None

    @pytest.mark.asyncio
    async def test_navigator_failure_becomes_500(self) -> None:
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=_mock_catalog())), \
             patch(f"{ROUTER}.GraphNavigator") as nav_cls:
            nav_cls.return_value.navigate = AsyncMock(side_effect=RuntimeError("arango down"))
            with pytest.raises(HTTPException) as exc:
                await _call_navigate(graph_provider=_mock_graph_provider())

        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "arango down" not in exc.value.detail


# ============================================================================
# knowledge_graph_lookup
# ============================================================================


class TestKnowledgeGraphLookup:
    @pytest.mark.asyncio
    async def test_returns_structured_matches_and_rendered_text(self) -> None:
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=_mock_catalog())), \
             patch(f"{ROUTER}.RecordResolver") as resolver_cls:
            resolver_cls.return_value.resolve_many = AsyncMock(return_value=_lookup_result())
            result = await _call_lookup(
                graph_provider=_mock_graph_provider(), identifiers=["PA-1787"],
            )

        assert result["matches"][0]["id"] == "rec-1"
        assert result["ambiguous"] is False
        assert isinstance(result["text"], str) and result["text"].strip()

    @pytest.mark.asyncio
    async def test_empty_catalog_short_circuits_to_200(self) -> None:
        """A miss is a legitimate outcome, not an error — and with no accessible
        connectors there is nothing to resolve against."""
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=_mock_catalog(empty=True))), \
             patch(f"{ROUTER}.RecordResolver") as resolver_cls:
            result = await _call_lookup(
                graph_provider=_mock_graph_provider(), identifiers=["PA-1787"],
            )

        assert result["matches"] == []
        assert result["not_found_identifiers"] == ["PA-1787"]
        resolver_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_blank_identifiers_are_rejected(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await _call_lookup(
                graph_provider=_mock_graph_provider(), identifiers=["   ", ""],
            )

        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value

    @pytest.mark.asyncio
    async def test_more_than_ten_identifiers_is_rejected_not_truncated(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await _call_lookup(
                graph_provider=_mock_graph_provider(),
                identifiers=[f"PA-{i}" for i in range(11)],
            )

        assert exc.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert "10" in exc.value.detail

    @pytest.mark.asyncio
    async def test_connector_name_hint_is_forwarded(self) -> None:
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=_mock_catalog())), \
             patch(f"{ROUTER}.RecordResolver") as resolver_cls:
            resolver_cls.return_value.resolve_many = AsyncMock(return_value=_lookup_result())
            await _call_lookup(
                graph_provider=_mock_graph_provider(),
                identifiers=[" PA-1787 "],
                connector_name="JIRA",
            )

        assert resolver_cls.call_args.kwargs["connector_name_hint"] == "JIRA"
        resolver_cls.return_value.resolve_many.assert_awaited_once_with(["PA-1787"])

    @pytest.mark.asyncio
    async def test_resolver_failure_becomes_500(self) -> None:
        with patch(f"{ROUTER}.ConnectorCatalog.build", AsyncMock(return_value=_mock_catalog())), \
             patch(f"{ROUTER}.RecordResolver") as resolver_cls:
            resolver_cls.return_value.resolve_many = AsyncMock(side_effect=RuntimeError("boom"))
            with pytest.raises(HTTPException) as exc:
                await _call_lookup(
                    graph_provider=_mock_graph_provider(), identifiers=["PA-1787"],
                )

        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "boom" not in exc.value.detail


class TestKnowledgeGraphContext:
    @pytest.mark.asyncio
    async def test_provider_failure_resolving_user_becomes_500(self) -> None:
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("arango unreachable"))

        with pytest.raises(HTTPException) as exc:
            await _call_navigate(graph_provider=gp)

        assert exc.value.status_code == HttpStatusCode.INTERNAL_SERVER_ERROR.value
        assert "arango unreachable" not in exc.value.detail
