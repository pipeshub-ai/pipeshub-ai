"""Access-check failures raise ``PermissionVerificationUnavailableError`` with a
fixed message: the driver's text (host, query, ids, record content) stays on
``__cause__``, never in the error's own message, which handlers log with ``%s``
and agent tools put in their results.

Every site that wraps a lower failure (``IGraphDBProvider``, ``RetrievalService``,
``chat_helpers``) is driven with a driver error carrying ``SENTINEL``. The
provider sites live on ``IGraphDBProvider``, so each runs on all three backends.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.modules.retrieval.retrieval_service as retrieval_mod
from app.config.constants.arangodb import Connectors, OriginTypes
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.provider import PostgresGraphProvider
from app.utils import chat_helpers
from app.utils.error_class import log_failure, mark_error
from tests.support.graph_read_errors import DRIVER_TEXT as SENTINEL
from tests.support.graph_read_errors import SENTINELS
from tests.support.log_capture import assert_not_logged, assert_text_free

FIXED = "The graph could not verify access."
BACKENDS = [Neo4jProvider, ArangoHTTPProvider, PostgresGraphProvider]


def _name(cls) -> str:
    return cls.__name__


class DriverError(Exception):
    """Stands in for a driver's error; its text is what must not travel."""


def _provider(cls, *, user_error=None, rows_error=None, hint_error=None) -> IGraphDBProvider:
    provider = cls.__new__(cls)
    provider.logger = MagicMock()

    async def get_user_by_user_id(user_id, *, raise_on_error=False) -> dict:
        if user_error:
            raise user_error
        return {"_key": "ukey", "id": "ukey"}

    async def kh_rows(*_a, **_k) -> list:
        if rows_error:
            raise rows_error
        return []

    async def ref_link_rows(_ids) -> list[dict]:
        return [{"docId": "d1", "recordId": "r1", "kind": "externalRecordId", "origin": OriginTypes.UPLOAD.value,
                 "connectorName": Connectors.KNOWLEDGE_BASE.value, "connectorId": "kb-1"}]

    async def ref_access_hint(*_a, **_k) -> None:
        raise hint_error

    provider.get_user_by_user_id = get_user_by_user_id
    provider._kh_v3_accessible_rows = kh_rows
    provider._ref_link_rows = ref_link_rows
    if hint_error:
        provider._ref_access_hint = ref_access_hint
    return provider


def _assert_fixed(err: PermissionVerificationUnavailableError, cause: BaseException) -> None:
    assert err.args == (FIXED,)
    assert SENTINEL not in str(err)
    assert err.__cause__ is cause  # kept whole for debugging, never re-built from its text
    assert str(cause) == SENTINEL


def _rendered(log_call) -> str:
    return log_call.args[0] % log_call.args[1:]


PROVIDER_SITES = {
    "check_access": ("rows_error", lambda p: p.check_access("ukey", "org-1", node_ids=["n1"])),
    "get_ref_access.user": ("user_error", lambda p: p.get_ref_access("u1", "org-1", ["d1"])),
    "get_ref_access.hint": ("hint_error", lambda p: p.get_ref_access("u1", "org-1", ["d1"])),
    "filter_accessible_record_ids.user": (
        "user_error", lambda p: p.filter_accessible_record_ids(["r1"], "u1", "org-1")),
    "filter_accessible_record_ids.rows": (
        "rows_error", lambda p: p.filter_accessible_record_ids(["r1"], "u1", "org-1")),
    "filter_accessible_virtual_record_ids.user": (
        "user_error", lambda p: p.filter_accessible_virtual_record_ids(["v1"], "u1", "org-1")),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS, ids=_name)
@pytest.mark.parametrize("site", PROVIDER_SITES)
async def test_provider_sites_raise_a_fixed_message(backend, site) -> None:
    failing, call = PROVIDER_SITES[site]
    cause = DriverError(SENTINEL)
    with pytest.raises(PermissionVerificationUnavailableError) as err:
        await call(_provider(backend, **{failing: cause}))
    _assert_fixed(err.value, cause)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS, ids=_name)
async def test_check_access_error_line_names_the_class_not_the_text(backend) -> None:
    provider = _provider(backend, rows_error=DriverError(SENTINEL))
    with pytest.raises(PermissionVerificationUnavailableError):
        await provider.check_access("ukey", "org-1", node_ids=["n1"], virtual_record_ids=["v1", "v2"])
    line = _rendered(provider.logger.error.call_args)
    assert SENTINEL not in line
    assert line == "check_access failed for 1 nodes and 2 vrids: DriverError"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS, ids=_name)
@pytest.mark.parametrize("site", ["check_access", "filter_accessible_record_ids.rows"])
async def test_a_backend_that_cannot_answer_is_not_wrapped(backend, site) -> None:
    _, call = PROVIDER_SITES[site]
    with pytest.raises(NotImplementedError):
        await call(_provider(backend, rows_error=NotImplementedError("no such query")))


@pytest.mark.asyncio
async def test_accessible_virtual_ids_task_raises_a_fixed_message() -> None:
    cause = DriverError(SENTINEL)
    graph = MagicMock()
    graph.get_accessible_virtual_record_ids = AsyncMock(side_effect=cause)
    service = RetrievalService.__new__(RetrievalService)
    with pytest.raises(PermissionVerificationUnavailableError) as err:
        await service._get_accessible_virtual_ids_task("u1", "org-1", {}, graph)
    _assert_fixed(err.value, cause)


@pytest.mark.asyncio
async def test_cached_user_read_raises_a_fixed_message(monkeypatch) -> None:
    monkeypatch.setattr(retrieval_mod, "_user_cache", {})
    cause = DriverError(SENTINEL)
    service = RetrievalService.__new__(RetrievalService)
    service.logger = MagicMock()
    service.graph_provider = MagicMock()
    service.graph_provider.get_user_by_user_id = AsyncMock(side_effect=cause)
    with pytest.raises(PermissionVerificationUnavailableError) as err:
        await service._get_user_cached("u1")
    _assert_fixed(err.value, cause)


@pytest.mark.asyncio
async def test_chat_access_check_raises_a_fixed_message() -> None:
    cause = DriverError(SENTINEL)
    graph = MagicMock()
    graph.get_user_by_user_id = AsyncMock(side_effect=cause)
    with pytest.raises(PermissionVerificationUnavailableError) as err:
        await chat_helpers.accessible_node_ids(graph, {"n1"}, "u1", "org-1")
    _assert_fixed(err.value, cause)


def test_the_message_is_fixed_unless_one_is_given() -> None:
    assert str(PermissionVerificationUnavailableError()) == FIXED
    assert PermissionVerificationUnavailableError("reader text").args == ("reader text",)


@pytest.mark.asyncio
async def test_the_cause_still_decides_class_and_hides_its_text_in_log_failure(caplog) -> None:
    cause = DriverError(SENTINEL)
    mark_error(cause, "timeout", hide_text=True)  # as the graph client leaves it
    provider = _provider(Neo4jProvider, rows_error=cause)
    with pytest.raises(PermissionVerificationUnavailableError) as err:
        await provider.check_access("ukey", "org-1", node_ids=["n1"])
    with caplog.at_level(logging.DEBUG, logger="test.pvue"):
        log_failure(logging.getLogger("test.pvue"), "search.access_unreadable", err.value)
    (record,) = caplog.records
    assert record.error_class == "timeout" and record.levelno == logging.WARNING
    assert_text_free([record], *SENTINELS)


# Where the copied text landed: handlers that log the error with ``%s``.

@pytest.mark.asyncio
async def test_enrichment_log_line_does_not_carry_the_driver_text(caplog) -> None:
    graph = MagicMock()
    graph.get_user_by_user_id = AsyncMock(side_effect=DriverError(SENTINEL))
    with caplog.at_level(logging.WARNING):
        assert await chat_helpers.accessible_node_ids_for_enrichment(graph, {"n1"}, "u1", "org-1") == set()
    lines = [r.getMessage() for r in caplog.records]
    assert f"Enrichment access check unavailable; naming no neighbours: {FIXED}" in lines
    assert_not_logged(caplog.records, *SENTINELS)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS, ids=_name)
async def test_permission_role_filter_warning_does_not_carry_the_driver_text(backend) -> None:
    provider = _provider(backend, rows_error=DriverError(SENTINEL))
    assert await provider.filter_nodes_with_permission_role([{"id": "n1"}], "ukey", "org-1") == set()
    assert _rendered(provider.logger.warning.call_args) == f"filter_nodes_with_permission_role: access check failed: {FIXED}"
