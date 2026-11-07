"""The connector token refresh scan runs org by org: the org list under ``system``, each org's connectors under ``for_org``.

Two in-memory graphs stand in for the providers: ``PgLikeGraph`` reads the bound org as ``services/graph_db/postgres`` does (an unbound
read raises), ``ArangoLikeGraph`` ignores it and reads across orgs as Arango and Neo4j do. The scan runs in an empty context, as
``spawn_detached`` starts it.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.token_service.oauth_service import (
    RefreshTokenInvalidError,
)
from app.connectors.core.base.token_service.token_refresh_service import (
    MAX_REFRESH_TOKEN_INVALID_FAILURES,
    TokenRefreshService,
)
from app.core import org_context
from app.core.org_context import MissingOrgContext, current_or_system, current_org_id

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_graph_log = logging.getLogger("fake-graph")


def _ambient_org() -> str | None:
    try:
        return current_org_id()
    except MissingOrgContext:
        return None


def _matches(doc: dict, filters: dict) -> bool:
    return all(doc.get(k) == v for k, v in filters.items())


def _org_apps(org: str) -> list[dict]:
    """Two OAuth connectors (one inactive) and two that are not scanned: an API-token one and an unauthenticated one."""
    return [
        {"_key": f"oauth-{org}", "type": "GITLAB", "authType": "OAUTH", "isActive": True, "orgId": org},
        {"_key": f"consent-{org}", "type": "OUTLOOK", "authType": "OAUTH_ADMIN_CONSENT", "isActive": False, "orgId": org},
        {"_key": f"api-{org}", "type": "JIRA", "authType": "API_TOKEN", "isActive": True, "orgId": org},
        {"_key": f"dead-{org}", "type": "DRIVE", "authType": "OAUTH", "isAuthenticated": False, "orgId": org},
    ]


ORGS = [
    {"_key": "a", "isActive": True},
    {"_key": "b", "isActive": False},       # a deactivated org: its connectors still refresh, as on main
    {"_key": "x", "isActive": True, "isExternal": True},     # an extracted organisation: owns no connectors, never listed
]
OAUTH_OF_A_AND_B = [("consent-a", "OUTLOOK", "a"), ("consent-b", "OUTLOOK", "b"), ("oauth-a", "GITLAB", "a"), ("oauth-b", "GITLAB", "b")]


class _Graph:
    def __init__(self, orgs: list[dict], apps: dict[str, list[dict]], *, orgs_error: Exception | None = None,
                 failing_orgs: frozenset[str] = frozenset(), raising_orgs: frozenset[str] = frozenset()) -> None:
        self.orgs, self.orgs_error = orgs, orgs_error
        self.failing_orgs = failing_orgs        # a read error the provider logs and answers with []
        self.raising_orgs = raising_orgs        # an unexpected exception out of the org's pass
        self.apps = [(org, doc) for org, docs in apps.items() for doc in docs]       # (partition org, document)
        self.org_list_calls: list[dict] = []
        self.updates: list[tuple[str | None, str, str, dict]] = []

    def _record_org_list(self, active: bool, is_external: bool, raise_on_error: bool) -> None:
        ctx = org_context._ctx.get()          # what the caller bound, whether or not this store reads it
        self.org_list_calls.append({"kind": ctx.kind if ctx else None, "active": active, "is_external": is_external,
                                    "raise_on_error": raise_on_error})

    def _read(self, org: str | None) -> None:
        if org in self.raising_orgs:
            raise RuntimeError("unexpected")

    def _orgs(self, active: bool, is_external: bool) -> list[dict]:
        return [o for o in self.orgs if (not active or o.get("isActive")) and bool(o.get("isExternal")) == is_external]


class PgLikeGraph(_Graph):
    """As ``PostgresGraphProvider``: every read is the bound org's partition; the org list is all orgs only under ``system``."""

    async def get_all_orgs(self, *, active: bool = True, is_external: bool = False, transaction: str | None = None,
                           raise_on_error: bool = False) -> list[dict]:
        ctx = current_or_system()          # nothing bound: MissingOrgContext, as identity.py
        self._record_org_list(active, is_external, raise_on_error)
        if self.orgs_error:
            raise self.orgs_error          # the system branch raises whatever raise_on_error says
        rows = self._orgs(active, is_external)
        return rows if ctx.kind == "system" else [o for o in rows if o["_key"] == ctx.org_id]

    async def get_nodes_by_filters(self, collection: str, filters: dict[str, Any], return_fields: list[str] | None = None,
                                   transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        org = current_org_id()             # before the try, as postgres/core.py
        self._read(org)
        if org in self.failing_orgs:
            _graph_log.error("get_nodes_by_filters failed")
            return []
        return [dict(d) for o, d in self.apps if o == org and _matches(d, filters)]      # orgId is an ordinary document filter

    async def get_document(self, key: str, collection: str, transaction: str | None = None) -> dict | None:
        org = current_org_id()
        return next((dict(d) for o, d in self.apps if o == org and d["_key"] == key), None)

    async def get_edges_to_node(self, node_id: str, edge_collection: str) -> list[dict]:
        current_org_id()
        return []

    async def update_node(self, key: str, collection: str, updates: dict, transaction: str | None = None) -> bool:
        self.updates.append((current_org_id(), key, collection, updates))
        return True


class ArangoLikeGraph(_Graph):
    """As the Arango and Neo4j providers: no context is read, a read spans every org, a failure is logged and answered empty."""

    async def get_all_orgs(self, *, active: bool = True, is_external: bool = False, transaction: str | None = None,
                           raise_on_error: bool = False) -> list[dict]:
        self._record_org_list(active, is_external, raise_on_error)
        if self.orgs_error:
            _graph_log.error(f"Get all orgs failed: {self.orgs_error}")
            if raise_on_error:
                raise self.orgs_error
            return []
        return self._orgs(active, is_external)

    async def get_nodes_by_filters(self, collection: str, filters: dict[str, Any], return_fields: list[str] | None = None,
                                   transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        self._read(filters.get("orgId"))
        if filters.get("orgId") in self.failing_orgs:
            _graph_log.error("get_nodes_by_filters failed")
            return []
        return [dict(d) for _, d in self.apps if _matches(d, filters)]


def _config(expires_in: int = 3600, age_s: int = 0) -> MagicMock:
    created = int(time.time()) - age_s       # epoch seconds, read back as the service's naive local time
    svc = MagicMock()
    svc.get_config = AsyncMock(return_value={"credentials": {
        "access_token": "at", "refresh_token": "rt", "token_type": "Bearer", "expires_in": expires_in, "created_at": created,
    }})
    svc.set_config = AsyncMock()
    return svc


def _spied(graph: _Graph) -> tuple[TokenRefreshService, list[tuple[str, str, str | None]]]:
    svc = TokenRefreshService(_config(), graph)
    seen: list[tuple[str, str, str | None]] = []

    async def record(connector_id: str, connector_type: str) -> None:
        seen.append((connector_id, connector_type, _ambient_org()))
    svc._refresh_connector_token = record
    return svc, seen


async def _detached_scan(svc: TokenRefreshService) -> org_context.OrgContext | None:
    """One scan as ``spawn_detached`` runs it (an empty context); whatever is still bound in that context afterwards."""
    async def scan() -> org_context.OrgContext | None:
        await svc._refresh_all_tokens()
        return org_context._ctx.get()
    return await asyncio.get_running_loop().create_task(scan(), context=contextvars.Context())


def _two_orgs(graph_cls: type[_Graph], **kw: object) -> _Graph:
    return graph_cls(ORGS, {"a": _org_apps("a"), "b": _org_apps("b"),
                            "x": [{"_key": "oauth-x", "type": "GITLAB", "authType": "OAUTH", "orgId": "x"}]}, **kw)


def _scan_errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "Error refreshing tokens" in r.getMessage()]


@pytest.fixture
async def services() -> AsyncIterator[list[TokenRefreshService]]:
    """Every service a test builds is stopped afterwards, so no armed timer outlives its test."""
    made: list[TokenRefreshService] = []
    yield made
    for svc in made:
        await svc.stop()


async def test_pg_detached_scan_refreshes_each_orgs_oauth_connectors_under_their_org(caplog) -> None:
    svc, seen = _spied(_two_orgs(PgLikeGraph))
    await _detached_scan(svc)
    assert not _scan_errors(caplog)
    assert sorted(seen) == OAUTH_OF_A_AND_B       # inactive org b and inactive connectors included; external org x never listed


async def test_arango_scan_processes_each_connector_once_under_its_own_org(caplog) -> None:
    svc, seen = _spied(_two_orgs(ArangoLikeGraph))
    await _detached_scan(svc)
    assert not _scan_errors(caplog)
    assert sorted(seen) == OAUTH_OF_A_AND_B       # once each: the orgId filter keeps org b's connectors out of org a's pass


@pytest.mark.parametrize("graph_cls", [PgLikeGraph, ArangoLikeGraph])
async def test_scan_lists_every_internal_org_once(graph_cls: type[_Graph]) -> None:
    graph = _two_orgs(graph_cls)
    svc, _ = _spied(graph)
    await _detached_scan(svc)
    assert len(graph.org_list_calls) == 1
    call = graph.org_list_calls[0]
    assert (call["active"], call["is_external"], call["raise_on_error"]) == (False, False, False)
    assert call["kind"] == "system"


async def test_org_without_id_is_skipped_and_the_rest_still_run(caplog) -> None:
    graph = PgLikeGraph([{"name": "no id"}, {"id": "b"}], {"b": _org_apps("b")})
    svc, seen = _spied(graph)
    await _detached_scan(svc)
    assert not _scan_errors(caplog)
    assert sorted(seen) == [("consent-b", "OUTLOOK", "b"), ("oauth-b", "GITLAB", "b")]


async def test_no_org_is_left_bound_after_the_scan() -> None:
    svc, seen = _spied(_two_orgs(PgLikeGraph))
    assert await _detached_scan(svc) is None
    assert seen


@pytest.mark.parametrize(("graph_cls", "expected"), [
    (PgLikeGraph, "Error refreshing tokens: db down"),
    (ArangoLikeGraph, "Get all orgs failed: db down"),
])
async def test_org_list_failure_is_one_error_on_each_backend(graph_cls: type[_Graph], expected: str, caplog) -> None:
    svc, seen = _spied(_two_orgs(graph_cls, orgs_error=RuntimeError("db down")))
    await _detached_scan(svc)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and expected in errors[0]
    assert seen == []


@pytest.mark.parametrize("graph_cls", [PgLikeGraph, ArangoLikeGraph])
async def test_one_orgs_failed_read_does_not_stop_the_others(graph_cls: type[_Graph], caplog) -> None:
    svc, seen = _spied(_two_orgs(graph_cls, failing_orgs=frozenset({"a"})))
    await _detached_scan(svc)
    assert not _scan_errors(caplog)
    assert sorted(seen) == [("consent-b", "OUTLOOK", "b"), ("oauth-b", "GITLAB", "b")]


@pytest.mark.parametrize("graph_cls", [PgLikeGraph, ArangoLikeGraph])
async def test_one_orgs_unexpected_error_is_one_warning_and_the_next_org_still_runs(graph_cls: type[_Graph], caplog) -> None:
    svc, seen = _spied(_two_orgs(graph_cls, raising_orgs=frozenset({"a"})))
    await _detached_scan(svc)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == ["token refresh failed org=a error=RuntimeError"]
    assert sorted(seen) == [("consent-b", "OUTLOOK", "b"), ("oauth-b", "GITLAB", "b")]


@pytest.mark.parametrize("graph_cls", [PgLikeGraph, ArangoLikeGraph])
async def test_an_app_without_org_id_is_not_scanned(graph_cls: type[_Graph]) -> None:
    legacy = {"_key": "legacy-a", "type": "GITLAB", "authType": "OAUTH"}     # a document that never got its orgId
    svc, seen = _spied(graph_cls([{"_key": "a", "isActive": True}], {"a": [*_org_apps("a"), legacy]}))
    await _detached_scan(svc)
    assert sorted(seen) == [("consent-a", "OUTLOOK", "a"), ("oauth-a", "GITLAB", "a")]


async def test_timer_armed_by_the_scan_runs_under_the_connector_org(services: list) -> None:
    svc = TokenRefreshService(_config(expires_in=3600), _two_orgs(PgLikeGraph))
    services.append(svc)
    fired: list[tuple[str, str | None]] = []

    async def delayed(connector_id: str, connector_type: str, delay: float) -> None:
        fired.append((connector_id, _ambient_org()))
    svc._delayed_refresh = delayed

    await _detached_scan(svc)
    await asyncio.gather(*svc._refresh_tasks.values())
    assert sorted(fired) == [("consent-a", "a"), ("consent-b", "b"), ("oauth-a", "a"), ("oauth-b", "b")]


@pytest.mark.parametrize("with_producer", [True, False])
async def test_pg_deauth_from_a_scan_item_writes_under_the_connector_org(with_producer: bool, services: list) -> None:
    graph = PgLikeGraph([{"_key": "a", "isActive": True}], {"a": [_org_apps("a")[0]]})
    svc = TokenRefreshService(_config(expires_in=100, age_s=200), graph)       # expired: every scan refreshes now
    services.append(svc)
    svc._perform_token_refresh = AsyncMock(side_effect=RefreshTokenInvalidError("invalid_grant"))
    producer = MagicMock()
    producer.send_message = AsyncMock()
    if with_producer:
        svc.set_messaging_producer(producer)

    for _ in range(MAX_REFRESH_TOKEN_INVALID_FAILURES):
        await _detached_scan(svc)

    assert len(graph.updates) == 1
    org, key, collection, updates = graph.updates[0]
    assert (org, key, collection, updates["isAuthenticated"]) == ("a", "oauth-a", "apps", False)
    if with_producer:
        producer.send_message.assert_awaited_once()
        assert producer.send_message.await_args.kwargs["message"]["payload"]["orgId"] == "a"
        assert "isActive" not in updates          # the appDisabled consumer owns isActive
    else:
        assert updates["isActive"] is False
