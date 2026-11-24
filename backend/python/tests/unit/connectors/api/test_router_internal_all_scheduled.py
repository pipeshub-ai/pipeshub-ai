"""
Unit tests for the internal /api/v1/connectors/internal/all-scheduled endpoint
and the _fetch_connector_sync_block helper.

Covered:
- _fetch_connector_sync_block: missing config, non-dict config, non-dict sync, success
- get_all_scheduled_connector_instances_internal:
    empty org list → empty listing
    each org read under its own context; a failed org is named and the others still listed
    no active connectors → empty items
    connector missing connectorId/type → skipped
    connector with MANUAL strategy → filtered out
    connector with SCHEDULED strategy → included in response
    pagination: hasMore=True when more candidates follow the page
    pagination: hasMore=False on the last page
    exception listing the orgs → 500
"""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api.router import (
    _fetch_connector_sync_block,
    get_all_scheduled_connector_instances_internal,
)
from app.core import org_context
from app.core.org_context import OrgContext, bind, current_or_system


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    orgs=None,
    page_docs=None,
    sync_configs=None,
) -> MagicMock:
    """
    Build a minimal FastAPI Request mock that wires up:
    - request.app.container.logger()
    - request.app.container.config_service()
    - request.app.state.graph_provider
    """
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()

    config_service = AsyncMock()
    if sync_configs is not None:
        config_service.get_config = AsyncMock(side_effect=sync_configs)
    else:
        config_service.get_config = AsyncMock(return_value=None)

    graph_provider = AsyncMock()
    graph_provider.get_all_orgs = AsyncMock(return_value=orgs if orgs is not None else [{"_key": "org-1"}])
    graph_provider.get_documents_paginated = AsyncMock(return_value=page_docs if page_docs is not None else [])
    graph_provider.get_document = AsyncMock(return_value=None)      # no store records an org for an app without orgId

    container = MagicMock()
    container.logger = MagicMock(return_value=logger)
    container.config_service = MagicMock(return_value=config_service)

    state = SimpleNamespace(graph_provider=graph_provider)

    app = MagicMock()
    app.container = container
    app.state = state

    request = MagicMock()
    request.app = app
    return request


def _active_doc(connector_id: str, connector_type: str, created_by: str = "user-1") -> dict:
    return {
        "_key": connector_id,
        "type": connector_type,
        "isActive": True,
        "createdBy": created_by,
    }


def _scheduled_sync(interval_minutes: int = 30) -> dict:
    return {
        "sync": {
            "selectedStrategy": "SCHEDULED",
            "scheduledConfig": {"intervalMinutes": interval_minutes, "timezone": "UTC"},
        }
    }


def _manual_sync() -> dict:
    return {"sync": {"selectedStrategy": "MANUAL"}}


# ---------------------------------------------------------------------------
# _fetch_connector_sync_block
# ---------------------------------------------------------------------------


class TestFetchConnectorSyncBlock:
    @pytest.mark.asyncio
    async def test_missing_config_returns_none(self) -> None:
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        logger = MagicMock()

        sync, read_failed = await _fetch_connector_sync_block("conn-1", config_service, logger)

        assert sync is None
        assert read_failed is False
        logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_non_dict_config_returns_none(self) -> None:
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value="not a dict")
        logger = MagicMock()

        sync, read_failed = await _fetch_connector_sync_block("conn-1", config_service, logger)

        assert sync is None
        assert read_failed is False

    @pytest.mark.asyncio
    async def test_config_with_no_sync_key_returns_empty_dict(self) -> None:
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"auth": {}})
        logger = MagicMock()

        sync, read_failed = await _fetch_connector_sync_block("conn-1", config_service, logger)

        # sync = config.get("sync") or {} → empty dict, but isinstance({}, dict) is True
        assert sync == {}
        assert read_failed is False

    @pytest.mark.asyncio
    async def test_non_dict_sync_returns_none(self) -> None:
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": "not-a-dict"})
        logger = MagicMock()

        sync, read_failed = await _fetch_connector_sync_block("conn-1", config_service, logger)

        assert sync is None
        assert read_failed is False
        logger.debug.assert_called()

    @pytest.mark.asyncio
    async def test_valid_sync_block_returned(self) -> None:
        sync_block = {"selectedStrategy": "SCHEDULED", "scheduledConfig": {"intervalMinutes": 5}}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": sync_block})
        logger = MagicMock()

        sync, read_failed = await _fetch_connector_sync_block("conn-1", config_service, logger)

        assert sync == sync_block
        assert read_failed is False

    @pytest.mark.asyncio
    async def test_config_service_exception_is_a_failed_read(self) -> None:
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        logger = MagicMock()

        sync, read_failed = await _fetch_connector_sync_block("conn-1", config_service, logger)

        assert sync is None
        assert read_failed is True
        logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_asks_the_store_to_raise_instead_of_answering_none(self) -> None:
        # ConfigurationService swallows an unreadable value into None unless
        # asked, which would read as "not scheduled" rather than a failed read.
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"sync": {}})

        await _fetch_connector_sync_block("conn-1", config_service, MagicMock())

        assert config_service.get_config.await_args.kwargs.get("raise_on_error") is True


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — no org
# ---------------------------------------------------------------------------


class TestAllScheduledNoOrg:
    @pytest.mark.asyncio
    async def test_no_orgs_is_an_empty_listing(self) -> None:
        # A 404 aborts the caller's whole sweep; no org has no scheduled connector.
        request = _make_request(orgs=[])

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["items"] == [] and result["hasMore"] is False and result["partial"] is False


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — empty / filtered pages
# ---------------------------------------------------------------------------


class TestAllScheduledEmptyPage:
    @pytest.mark.asyncio
    async def test_no_active_connectors_returns_empty_items(self) -> None:
        request = _make_request(page_docs=[])

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["success"] is True
        assert result["items"] == []
        assert result["hasMore"] is False

    @pytest.mark.asyncio
    async def test_connector_without_key_is_skipped(self) -> None:
        # Document missing _key and id
        bad_doc = {"type": "Confluence", "isActive": True}
        request = _make_request(page_docs=[bad_doc], sync_configs=[None])

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_connector_without_type_is_skipped(self) -> None:
        bad_doc = {"_key": "conn-1", "isActive": True}
        request = _make_request(page_docs=[bad_doc], sync_configs=[None])

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["items"] == []


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — strategy filtering
# ---------------------------------------------------------------------------


class TestAllScheduledStrategyFiltering:
    @pytest.mark.asyncio
    async def test_manual_strategy_is_excluded(self) -> None:
        doc = _active_doc("conn-1", "Confluence")
        request = _make_request(
            page_docs=[doc],
            sync_configs=[_manual_sync()],
        )

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_scheduled_strategy_is_included(self) -> None:
        doc = _active_doc("conn-1", "Confluence", created_by="user-42")
        request = _make_request(
            page_docs=[doc],
            sync_configs=[_scheduled_sync(interval_minutes=15)],
        )

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["connectorId"] == "conn-1"
        assert item["type"] == "Confluence"
        assert item["orgId"] == "org-1"
        assert item["ownerUserId"] == "user-42"
        assert item["isActive"] is True
        assert item["sync"]["selectedStrategy"] == "SCHEDULED"

    @pytest.mark.asyncio
    async def test_missing_sync_config_excluded(self) -> None:
        doc = _active_doc("conn-1", "Slack")
        request = _make_request(
            page_docs=[doc],
            sync_configs=[None],
        )

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["items"] == []

    @pytest.mark.asyncio
    async def test_mixed_connectors_only_scheduled_returned(self) -> None:
        docs = [
            _active_doc("conn-1", "Confluence"),
            _active_doc("conn-2", "Slack"),
            _active_doc("conn-3", "Jira"),
        ]
        # conn-1: SCHEDULED, conn-2: MANUAL, conn-3: no config
        sync_configs = [
            _scheduled_sync(),
            _manual_sync(),
            None,
        ]
        request = _make_request(page_docs=docs, sync_configs=sync_configs)

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert len(result["items"]) == 1
        assert result["items"][0]["connectorId"] == "conn-1"
        assert result["partial"] is False

    @pytest.mark.asyncio
    async def test_config_read_failure_marks_the_page_partial(self) -> None:
        docs = [
            _active_doc("conn-1", "Confluence"),
            _active_doc("conn-2", "Slack"),
        ]
        request = _make_request(
            page_docs=docs,
            sync_configs=[_scheduled_sync(), RuntimeError("etcd down")],
        )

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["partial"] is True
        assert [item["connectorId"] for item in result["items"]] == ["conn-1"]


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — pagination
# ---------------------------------------------------------------------------


class TestAllScheduledPagination:
    @pytest.mark.asyncio
    async def test_has_more_true_when_more_candidates_follow(self) -> None:
        # limit=2 and 3 candidates → has_more=True, items trimmed to 2
        docs = [
            _active_doc("conn-1", "Confluence"),
            _active_doc("conn-2", "Slack"),
            _active_doc("conn-3", "Jira"),  # the probe doc
        ]
        sync_configs = [_scheduled_sync(), _scheduled_sync(), _scheduled_sync()]
        request = _make_request(page_docs=docs, sync_configs=sync_configs)

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=2)

        assert result["hasMore"] is True
        assert len(result["items"]) <= 2

    @pytest.mark.asyncio
    async def test_has_more_false_on_last_page(self) -> None:
        docs = [_active_doc("conn-1", "Confluence")]
        request = _make_request(page_docs=docs, sync_configs=[_scheduled_sync()])

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["hasMore"] is False

    @pytest.mark.asyncio
    async def test_pages_step_through_the_candidates_in_key_order(self) -> None:
        docs = [_active_doc(f"conn-{i}", "Jira") for i in (3, 1, 2)]

        pages = []
        for page in (1, 2):
            request = _make_request(page_docs=docs, sync_configs=[_scheduled_sync()] * 3)
            pages.append(await get_all_scheduled_connector_instances_internal(request, page=page, limit=2))

        assert [[i["connectorId"] for i in p["items"]] for p in pages] == [["conn-1", "conn-2"], ["conn-3"]]
        assert [p["hasMore"] for p in pages] == [True, False]

    @pytest.mark.asyncio
    async def test_filters_isactive_true_passed_to_provider(self) -> None:
        request = _make_request(page_docs=[])

        await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        call = request.app.state.graph_provider.get_documents_paginated.call_args
        assert call[1]["filters"] == {"isActive": True}

    @pytest.mark.asyncio
    async def test_db_errors_raise_and_pages_are_stably_sorted(self) -> None:
        request = _make_request(page_docs=[])

        await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        call = request.app.state.graph_provider.get_documents_paginated.call_args
        assert call[1]["raise_on_error"] is True
        assert call[1]["sort_field"] == "_key"


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — org ownership
# ---------------------------------------------------------------------------


class TestAllScheduledOrgOwnership:
    @pytest.mark.asyncio
    async def test_each_connector_keeps_its_own_org(self) -> None:
        docs = [
            {**_active_doc("conn-a", "Confluence"), "orgId": "org-a"},
            {**_active_doc("conn-b", "Slack"), "orgId": "org-b"},
        ]
        request = _make_request(
            orgs=[{"_key": "org-a"}, {"_key": "org-b"}],
            page_docs=docs,
            sync_configs=[_scheduled_sync(), _scheduled_sync()],
        )

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert {i["connectorId"]: i["orgId"] for i in result["items"]} == {
            "conn-a": "org-a",
            "conn-b": "org-b",
        }

    @pytest.mark.asyncio
    async def test_legacy_doc_without_org_falls_back_to_first_org(self) -> None:
        request = _make_request(
            orgs=[{"_key": "org-1"}],
            page_docs=[_active_doc("conn-1", "Confluence")],
            sync_configs=[_scheduled_sync()],
        )

        result = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert result["items"][0]["orgId"] == "org-1"


# ---------------------------------------------------------------------------
# get_all_scheduled_connector_instances_internal — error handling
# ---------------------------------------------------------------------------


class TestAllScheduledErrorHandling:
    @pytest.mark.asyncio
    async def test_org_listing_exception_raises_500(self) -> None:
        request = _make_request()
        request.app.state.graph_provider.get_all_orgs = AsyncMock(
            side_effect=RuntimeError("DB unreachable")
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_http_exception_propagated_unchanged(self) -> None:
        request = _make_request()
        request.app.state.graph_provider.get_all_orgs = AsyncMock(
            side_effect=HTTPException(status_code=503, detail="graph DB down")
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)

        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# connectorIds: one connector's state, for a caller that must not read a gap as "sync is off"
# ---------------------------------------------------------------------------


class TestListedConnectors:
    @staticmethod
    def _request(docs: dict, configs: dict) -> MagicMock:
        request = _make_request()
        gp = request.app.state.graph_provider

        async def get_document(
            key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False,
        ) -> dict | None:
            doc = docs.get(key)
            if isinstance(doc, Exception):
                raise doc
            return doc

        async def get_config(path: str, *, use_cache: bool = True, raise_on_error: bool = False) -> dict | None:
            # Like ConfigurationService: a store error is None unless the caller asks it to raise.
            for cid, cfg in configs.items():
                if cid in path:
                    if isinstance(cfg, Exception):
                        if raise_on_error:
                            raise cfg
                        return None
                    return cfg
            return None

        gp.get_document = AsyncMock(side_effect=get_document)
        request.app.container.config_service().get_config = AsyncMock(side_effect=get_config)
        return request

    @pytest.mark.asyncio
    async def test_each_listed_connector_is_classified_and_none_is_left_out(self) -> None:
        off = {**_active_doc("c-off", "Web"), "isActive": False}
        request = self._request(
            docs={"c-sched": _active_doc("c-sched", "Web"), "c-off": off, "c-unread": _active_doc("c-unread", "Web"),
                  "c-dberr": RuntimeError("graph down")},
            configs={"c-sched": _scheduled_sync(15), "c-off": _scheduled_sync(), "c-unread": RuntimeError("kv down")},
        )

        result = await get_all_scheduled_connector_instances_internal(
            request, page=1, limit=50, connector_ids="c-sched,c-off,c-unread,c-gone,c-dberr",
        )

        items = {i["connectorId"]: i for i in result["items"]}
        assert result["hasMore"] is False and len(items) == 5
        assert items["c-sched"]["isActive"] is True
        assert items["c-sched"]["sync"]["scheduledConfig"]["intervalMinutes"] == 15
        assert items["c-off"]["isActive"] is False and "unreadable" not in items["c-off"]
        assert items["c-unread"]["unreadable"] is True
        assert items["c-dberr"]["unreadable"] is True, "a failed read is not a deleted connector"
        assert items["c-gone"] == {"connectorId": "c-gone", "missing": True}


# ---------------------------------------------------------------------------
# Per-org reads, under the org-less service token the schedule sweep sends
# ---------------------------------------------------------------------------


ORG_A, ORG_B = "org-a", "org-b"
SWEEP_TOKEN = OrgContext(org_context.SYSTEM_ORG_ID, "system", "service", "scoped_jwt", "t")


class _Graph:
    """Two orgs' connectors. ``scoped`` answers as PostgreSQL does (the orgs list only under a system scope, a connector read only
    the bound org's); otherwise every org's, as Neo4j and ArangoDB do."""

    def __init__(self, *, scoped: bool, apps: list[dict] | None = None) -> None:
        self.scoped = scoped
        self.apps = apps if apps is not None else [
            {**_active_doc("conn-a", "Jira"), "orgId": ORG_A},
            {**_active_doc("conn-b", "Confluence"), "orgId": ORG_B},
        ]
        self.broken: set[str] = set()
        self.inactive: set[str] = set()
        self.holder: dict[str, str] = {}      # scoped only: the org an app without orgId is stored in, which a lookup by id gives
        self.reads: list[str] = []

    async def get_all_orgs(self, *, active: bool = True, **_kw: object) -> list[dict]:
        ctx = current_or_system()
        orgs = [{"_key": o} for o in (ORG_A, ORG_B) if not active or o not in self.inactive]
        return orgs if not self.scoped or ctx.kind == "system" else [o for o in orgs if o["_key"] == ctx.org_id]

    async def get_documents_paginated(self, collection: str, **kw: object) -> list[dict]:
        assert kw["raise_on_error"] is True and kw["filters"] == {"isActive": True}
        org = org_context.current_org_id()
        self.reads.append(org)
        if org in self.broken:
            raise RuntimeError("graph did not answer")
        return [dict(a) for a in self.apps if not self.scoped or a.get("orgId", self.holder.get(a["_key"], org)) == org]

    async def get_document(self, key: str, collection: str, transaction: object = None, *, raise_on_error: bool = False) -> dict | None:
        assert current_or_system().kind == "system", "a lookup by id names no org"
        doc = next((dict(a) for a in self.apps if a["_key"] == key), None)
        return {**doc, "orgId": self.holder[key]} if doc and self.scoped and key in self.holder else doc


def _graph_request(graph: _Graph) -> MagicMock:
    request = _make_request()
    request.app.state.graph_provider = graph

    async def get_config(path: str, **_kw: object) -> dict:
        return _scheduled_sync()

    request.app.container.config_service().get_config = AsyncMock(side_effect=get_config)
    return request


class TestAllScheduledPerOrg:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("scoped", [True, False], ids=["one-org-reads", "every-org-reads"])
    async def test_every_orgs_connectors_are_listed_each_read_under_its_org(self, scoped: bool) -> None:
        graph = _Graph(scoped=scoped)

        with bind(SWEEP_TOKEN):
            result = await get_all_scheduled_connector_instances_internal(_graph_request(graph), page=1, limit=50)

        assert {i["connectorId"]: i["orgId"] for i in result["items"]} == {"conn-a": ORG_A, "conn-b": ORG_B}
        assert graph.reads == [ORG_A, ORG_B]
        assert result["partial"] is False and result["failedOrgs"] == []

    @pytest.mark.asyncio
    async def test_a_failing_org_is_named_and_the_others_are_still_listed(self) -> None:
        graph = _Graph(scoped=True)
        graph.broken.add(ORG_A)

        with bind(SWEEP_TOKEN):
            result = await get_all_scheduled_connector_instances_internal(_graph_request(graph), page=1, limit=50)

        assert [i["connectorId"] for i in result["items"]] == ["conn-b"]
        assert result["partial"] is True and result["failedOrgs"] == [ORG_A]

    @pytest.mark.asyncio
    async def test_an_app_without_an_org_is_listed_once_under_the_first_org(self) -> None:
        graph = _Graph(scoped=False, apps=[_active_doc("legacy", "Jira")])

        with bind(SWEEP_TOKEN):
            result = await get_all_scheduled_connector_instances_internal(_graph_request(graph), page=1, limit=50)

        assert [(i["connectorId"], i["orgId"]) for i in result["items"]] == [("legacy", ORG_A)]

    @pytest.mark.asyncio
    async def test_listed_ids_are_read_across_orgs(self) -> None:
        graph = _Graph(scoped=True)

        with bind(SWEEP_TOKEN):
            result = await get_all_scheduled_connector_instances_internal(
                _graph_request(graph), page=1, limit=50, connector_ids="conn-a,conn-b",
            )

        assert {i["connectorId"]: i["orgId"] for i in result["items"]} == {"conn-a": ORG_A, "conn-b": ORG_B}

    @pytest.mark.asyncio
    async def test_an_app_without_an_org_waits_while_its_org_cannot_be_read(self) -> None:
        # Listed under the next org instead, a repair would move its schedule to an org a lookup by id does not give it.
        graph = _Graph(scoped=False, apps=[_active_doc("legacy", "Jira")])
        graph.broken.add(ORG_A)

        with bind(SWEEP_TOKEN):
            result = await get_all_scheduled_connector_instances_internal(_graph_request(graph), page=1, limit=50)

        assert result["items"] == []
        assert result["partial"] is True and result["failedOrgs"] == [ORG_A]

    @pytest.mark.asyncio
    async def test_an_app_without_an_org_gets_the_same_org_listed_and_looked_up(self) -> None:
        graph = _Graph(scoped=False, apps=[_active_doc("legacy", "Jira")])
        graph.inactive.add(ORG_A)
        request = _graph_request(graph)

        with bind(SWEEP_TOKEN):
            listed = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50)
            by_id = await get_all_scheduled_connector_instances_internal(request, page=1, limit=50, connector_ids="legacy")

        assert [i["orgId"] for i in listed["items"]] == [i["orgId"] for i in by_id["items"]] == [ORG_A]

    @pytest.mark.asyncio
    async def test_an_app_without_an_org_is_listed_under_the_org_holding_it_while_another_org_fails(self) -> None:
        # A partitioned store answers it for its own org only: an earlier org's failure is no reason to hold it back.
        graph = _Graph(scoped=True, apps=[_active_doc("legacy", "Jira")])
        graph.holder["legacy"] = ORG_B
        graph.broken.add(ORG_A)

        with bind(SWEEP_TOKEN):
            result = await get_all_scheduled_connector_instances_internal(_graph_request(graph), page=1, limit=50)

        assert [(i["connectorId"], i["orgId"]) for i in result["items"]] == [("legacy", ORG_B)]
        assert result["partial"] is True and result["failedOrgs"] == [ORG_A]
