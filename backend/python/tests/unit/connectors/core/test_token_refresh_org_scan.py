"""The connector token refresh scan runs org by org: the org list under ``system``, each org's connectors under ``for_org``.

The two in-memory graphs of ``tests/support/org_partitioned_graph.py`` stand in for the providers. The scan runs in an empty context,
as ``spawn_detached`` starts it.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from typing import TYPE_CHECKING
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
from tests.support.org_partitioned_graph import (
    ArangoLikeGraph,
    FakeGraph,
    PgLikeGraph,
    ambient_org,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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


def _config(expires_in: int = 3600, age_s: int = 0) -> MagicMock:
    created = int(time.time()) - age_s       # epoch seconds, read back as the service's naive local time
    svc = MagicMock()
    svc.get_config = AsyncMock(return_value={"credentials": {
        "access_token": "at", "refresh_token": "rt", "token_type": "Bearer", "expires_in": expires_in, "created_at": created,
    }})
    svc.set_config = AsyncMock()
    return svc


def _spied(graph: FakeGraph) -> tuple[TokenRefreshService, list[tuple[str, str, str | None]]]:
    svc = TokenRefreshService(_config(), graph)
    seen: list[tuple[str, str, str | None]] = []

    async def record(connector_id: str, connector_type: str) -> None:
        seen.append((connector_id, connector_type, ambient_org()))
    svc._refresh_connector_token = record
    return svc, seen


async def _detached_scan(svc: TokenRefreshService) -> org_context.OrgContext | None:
    """One scan as ``spawn_detached`` runs it (an empty context); whatever is still bound in that context afterwards."""
    async def scan() -> org_context.OrgContext | None:
        await svc._refresh_all_tokens()
        return org_context._ctx.get()
    return await asyncio.get_running_loop().create_task(scan(), context=contextvars.Context())


def _two_orgs(graph_cls: type[FakeGraph], **kw: object) -> FakeGraph:
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
async def test_scan_lists_every_internal_org_once(graph_cls: type[FakeGraph]) -> None:
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
async def test_org_list_failure_is_one_error_on_each_backend(graph_cls: type[FakeGraph], expected: str, caplog) -> None:
    svc, seen = _spied(_two_orgs(graph_cls, orgs_error=RuntimeError("db down")))
    await _detached_scan(svc)
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and expected in errors[0]
    assert seen == []


@pytest.mark.parametrize("graph_cls", [PgLikeGraph, ArangoLikeGraph])
async def test_one_orgs_failed_read_does_not_stop_the_others(graph_cls: type[FakeGraph], caplog) -> None:
    svc, seen = _spied(_two_orgs(graph_cls, failing_orgs=frozenset({"a"})))
    await _detached_scan(svc)
    assert not _scan_errors(caplog)
    assert sorted(seen) == [("consent-b", "OUTLOOK", "b"), ("oauth-b", "GITLAB", "b")]


@pytest.mark.parametrize("graph_cls", [PgLikeGraph, ArangoLikeGraph])
async def test_one_orgs_unexpected_error_is_one_warning_and_the_next_org_still_runs(graph_cls: type[FakeGraph], caplog) -> None:
    svc, seen = _spied(_two_orgs(graph_cls, raising_orgs=frozenset({"a"})))
    await _detached_scan(svc)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == ["token_refresh failed org=a error=RuntimeError"]
    assert sorted(seen) == [("consent-b", "OUTLOOK", "b"), ("oauth-b", "GITLAB", "b")]


@pytest.mark.parametrize("graph_cls", [PgLikeGraph, ArangoLikeGraph])
async def test_an_app_without_org_id_is_not_scanned(graph_cls: type[FakeGraph]) -> None:
    legacy = {"_key": "legacy-a", "type": "GITLAB", "authType": "OAUTH"}     # a document that never got its orgId
    svc, seen = _spied(graph_cls([{"_key": "a", "isActive": True}], {"a": [*_org_apps("a"), legacy]}))
    await _detached_scan(svc)
    assert sorted(seen) == [("consent-a", "OUTLOOK", "a"), ("oauth-a", "GITLAB", "a")]


async def test_timer_armed_by_the_scan_runs_under_the_connector_org(services: list) -> None:
    svc = TokenRefreshService(_config(expires_in=3600), _two_orgs(PgLikeGraph))
    services.append(svc)
    fired: list[tuple[str, str | None]] = []

    async def delayed(connector_id: str, connector_type: str, delay: float) -> None:
        fired.append((connector_id, ambient_org()))
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
