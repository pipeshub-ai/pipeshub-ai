"""R-03 / DF4-01: another user's chat attachment on every hooked read route.

Without consent the route answers 404 with the same body as a missing record
(no existence oracle); with consent the gate passes; a write/delete still
requires the existing ACL.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, RecordTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api import router as router_mod
from app.modules.authz.node_pdp_client import set_node_pdp_client
from tests.unit.modules.authz.pdp_fakes import ORG, B, C, FakeNodeRules, Graph

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

SENTINEL = HttpStatusCode.IM_A_TEAPOT.value if hasattr(HttpStatusCode, "IM_A_TEAPOT") else 418


def _entity(rid: str = "att-1") -> SimpleNamespace:
    return SimpleNamespace(
        id=rid, org_id=ORG, connector_name=Connectors.ATTACHMENTS, record_type=RecordTypes.FILE,
        connector_id="conn-att",
    )


@pytest.fixture
def world() -> Iterator[tuple[Graph, FakeNodeRules]]:
    graph = Graph()
    graph.user(B)
    graph.user(C)
    graph.attachment("att-1")
    graph.records_by_id["att-1"] = _entity()
    graph.docs["organizations"][ORG] = {"_key": ORG}
    node = FakeNodeRules()
    node.members = {"chat-b": {C}}
    set_node_pdp_client(node)
    yield graph, node
    set_node_pdp_client(None)


def _request(user_id: str = C) -> MagicMock:
    req = MagicMock()
    req.state.user = {"userId": user_id, "orgId": ORG}
    req.app.container.config_service.return_value = AsyncMock()
    return req


def _sentinel_gate() -> AbstractContextManager[object]:
    """Patch the step that follows the access gate so reaching it proves the gate passed."""
    return patch.object(
        router_mod, "_refuse_hidden_demo_record",
        new=AsyncMock(side_effect=HTTPException(status_code=SENTINEL, detail="past-the-gate")),
    )


async def _internal(graph, record_id) -> object:
    claims = {"token_type": "scoped", "orgId": ORG, "userId": C, "scopes": ["record:content"]}
    return await router_mod.get_record_content_internal(
        request=MagicMock(), record_id=record_id, version=None,
        graph_provider=graph, config_service=AsyncMock(), claims=claims,
    )


async def _stream(graph, record_id) -> object:
    return await router_mod.stream_record(
        _request(), record_id, graph_provider=graph, config_service=AsyncMock(),
    )


async def _by_id(graph, record_id) -> object:
    return await router_mod.get_record_by_id(record_id, _request(), graph_provider=graph)


async def _content(graph, record_id) -> object:
    return await router_mod.get_record_content(record_id, _request(), graph_provider=graph)


async def _signed_url(graph, record_id) -> object:
    handler = MagicMock()
    handler.get_signed_url = AsyncMock(return_value="https://signed")
    return await router_mod.get_signed_url(
        _request(), ORG, C, "googledrive", record_id, signed_url_handler=handler, graph_provider=graph,
    )


async def _download(graph, record_id) -> object:
    handler = MagicMock()
    handler.validate_token = MagicMock(return_value=SimpleNamespace(
        user_id=C, record_id=record_id, additional_claims={"org_id": ORG},
    ))
    return await router_mod.download_file(
        _request(), ORG, record_id, "googledrive", "tok", handler, graph,
    )


ROUTES = {
    "internal": _internal,
    "stream_record": _stream,
    "get_record_by_id": _by_id,
    "get_record_content": _content,
    "get_signed_url": _signed_url,
    "download_file": _download,
}


async def _outcome(call) -> tuple[int, object]:
    try:
        await call
    except HTTPException as exc:
        return exc.status_code, exc.detail
    return 200, None


@pytest.mark.parametrize("route", sorted(ROUTES))
class TestChatAttachmentReadRoutes:
    async def test_unconsented_attachment_is_404_identical_to_a_missing_record(self, world, route) -> None:
        graph, node = world
        node.files_shared[("chat-b", "att-1")] = False
        denied = await _outcome(ROUTES[route](graph, "att-1"))
        missing = await _outcome(ROUTES[route](graph, "does-not-exist"))
        assert denied[0] == HttpStatusCode.NOT_FOUND.value
        assert denied == missing

    async def test_pdp_down_denies(self, world, route) -> None:
        graph, node = world
        node.files_shared[("chat-b", "att-1")] = True
        node.down = True
        status, _ = await _outcome(ROUTES[route](graph, "att-1"))
        assert status == HttpStatusCode.NOT_FOUND.value

    async def test_consented_attachment_passes_the_gate(self, world, route) -> None:
        graph, node = world
        node.files_shared[("chat-b", "att-1")] = True
        with _sentinel_gate(), patch.object(
            router_mod, "_resolve_record_content_response", new=AsyncMock(side_effect=HTTPException(SENTINEL, "gate")),
        ), patch.object(
            router_mod, "_fetch_multiple_records_impl", new=AsyncMock(side_effect=HTTPException(SENTINEL, "gate")),
        ):
            status, _ = await _outcome(ROUTES[route](graph, "att-1"))
        assert status in (SENTINEL, 200)

    async def test_removal_is_immediate(self, world, route) -> None:
        graph, node = world
        node.files_shared[("chat-b", "att-1")] = True
        node.members["chat-b"].discard(C)
        status, _ = await _outcome(ROUTES[route](graph, "att-1"))
        assert status == HttpStatusCode.NOT_FOUND.value


class TestUploaderAndOtherAccessUnchanged:
    async def test_acl_grant_still_wins_without_asking_the_pdp(self, world) -> None:
        graph, node = world
        graph.acl.add((C, "att-1"))
        node.down = True
        with _sentinel_gate():
            status, _ = await _outcome(_stream(graph, "att-1"))
        assert status == SENTINEL

    async def test_get_record_by_id_returns_acl_details_when_granted(self, world) -> None:
        graph, node = world
        graph.acl.add((C, "att-1"))
        with patch.object(router_mod, "_refuse_hidden_demo_record", new=AsyncMock()):
            assert await _by_id(graph, "att-1") == {"record": {"id": "att-1"}}

    async def test_get_record_by_id_pdp_allow_returns_details(self, world) -> None:
        graph, node = world
        node.files_shared[("chat-b", "att-1")] = True
        with patch.object(router_mod, "_refuse_hidden_demo_record", new=AsyncMock()):
            details = await _by_id(graph, "att-1")
        assert details["viaChatContentPdp"] is True


class TestDeleteStillNeedsTheExistingAcl:
    async def test_delete_record_does_not_consult_the_pdp(self, world) -> None:
        graph, node = world
        node.files_shared[("chat-b", "att-1")] = True
        graph.delete_record = AsyncMock(return_value={"success": True})
        request = _request()
        request.app.container.messaging_producer = None
        with pytest.raises(HTTPException) as exc:
            await router_mod.delete_record("att-1", request, graph_provider=graph)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value
        assert exc.value.detail == "You do not have access to this record"
        graph.delete_record.assert_not_awaited()
