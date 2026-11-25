"""A record (or record group, or connector) read that gates a check refuses with 503 when it fails, instead of skipping the check.

Read without ``raise_on_error``, a provider answers a failed read with None. Read as "no connector", that skipped the
switched-off demo refusal (the record, or its content, was served), the content open's connector refusal, and the reindex
lock check. The fake graph honours the flag as the providers do: None without it, a raise with it.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.api.routes import records as records_mod
from app.connectors.api import router as router_mod
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.demo_data import access as demo_access
from app.utils.user_messages import DATABASE_UNREACHABLE
from tests.support.record_access import InMemoryRecordAccess

RECORD_ID = "rec-1"
ORG_ID = "org-1"
CONNECTOR_ID = "conn-1"
USER = {"orgId": ORG_ID, "userId": "u1", "token_type": "regular"}
LOCKED_APP = {"id": CONNECTOR_ID, "type": "DRIVE", "isLocked": True, "status": "SYNCING", "isActive": True}
DISABLED_APP = {"id": CONNECTOR_ID, "type": "DRIVE", "isActive": False, "name": "Drive"}
CONNECTOR_DOC = {"id": RECORD_ID, "origin": "CONNECTOR", "recordType": "FILE", "connectorName": "DRIVE",
                 "connectorId": CONNECTOR_ID}


class _Down(ConnectionError):
    pass


class _Graph:
    """Answers documents by collection; the collections in ``failing`` fail like an outage."""

    def __init__(self, docs: dict[str, dict | None], *, failing: frozenset[str] = frozenset()) -> None:
        self.docs, self.failing = docs, failing
        InMemoryRecordAccess().readable("u1", ORG_ID, RECORD_ID).serve(self)

    async def get_document(self, key: str, collection: str, transaction: str | None = None, *,
                           raise_on_error: bool = False) -> dict | None:
        if collection in self.failing:
            if raise_on_error:
                raise _Down("graph down")
            return None
        return self.docs.get(collection)


def _request(logger: MagicMock | None = None) -> SimpleNamespace:
    logger = logger or MagicMock()
    container = SimpleNamespace(logger=lambda: logger, config_service=lambda: MagicMock(), messaging_producer=None)
    return SimpleNamespace(state=SimpleNamespace(user=USER), app=SimpleNamespace(container=container), headers={})


def _hidden_demo(hidden: bool = True) -> object:
    """The demo setting hides ``CONNECTOR_ID``'s records: the shared refusal looks it up in ``demo_data.access``."""
    return patch.object(demo_access, "is_hidden_demo_record",
                        AsyncMock(side_effect=lambda *a: hidden and a[-1] == CONNECTOR_ID))


async def _answer(call: Any) -> object:  # noqa: ANN401
    try:
        return await call
    except HTTPException as exc:
        return exc


async def _details(graph: _Graph, *, open_: bool, request: SimpleNamespace | None = None) -> object:
    with patch.object(router_mod, "refuse_outside_connection", AsyncMock()), \
         patch.object(router_mod, "hide_record_parents_outside_connection", AsyncMock()), \
         patch.object(router_mod, "_stage_states_of", AsyncMock(return_value=[])):
        return await _answer(router_mod.get_record_by_id(
            RECORD_ID, request or _request(), graph_provider=graph, x_record_content_open="1" if open_ else None))


async def _content(graph: _Graph, *, request: SimpleNamespace | None = None) -> object:
    with patch.object(router_mod, "accessible_node_ids", AsyncMock(return_value={RECORD_ID})), \
         patch.object(router_mod, "refuse_outside_connection", AsyncMock()), \
         patch.object(router_mod, "optional_redis", AsyncMock(return_value=None)), \
         patch.object(router_mod, "ParseNow", MagicMock()), \
         patch.object(router_mod, "_fetch_multiple_records_impl",
                      AsyncMock(return_value={"ok": True, "records": [{"id": RECORD_ID}]})), \
         patch.object(router_mod, "record_to_text", MagicMock(return_value="the record's text")):
        return await _answer(router_mod.get_record_content(RECORD_ID, request or _request(), graph_provider=graph))


async def _read_gate(graph: _Graph, *, request: SimpleNamespace | None = None) -> object:
    with patch.object(records_mod, "accessible_node_ids", AsyncMock(return_value={RECORD_ID})), \
         patch.object(records_mod, "refuse_outside_connection", AsyncMock()):
        return await _answer(records_mod._check_access(request or _request(), graph, RECORD_ID))


def _assert_outage(answer: object) -> None:
    assert isinstance(answer, HTTPException), f"the route answered {answer!r}"
    assert (answer.status_code, answer.detail) == (503, DATABASE_UNREACHABLE)
    assert answer.headers["Retry-After"] == "5"
    assert isinstance(answer.__cause__, _Down), "the graph error is kept as the cause"


# Route name -> a call of it with a graph whose record read fails (a demo-hidden connector is configured).
GATES = {
    "record_details": lambda g: _details(g, open_=False),
    "record_details_open": lambda g: _details(g, open_=True),
    "record_content": _content,
    "query_record_read": _read_gate,
}


class TestTheRecordRead:
    @pytest.mark.parametrize("gate", GATES.values(), ids=GATES.keys())
    async def test_a_failed_record_read_is_503(self, gate: Any) -> None:  # noqa: ANN401
        with _hidden_demo():
            _assert_outage(await gate(_Graph({"apps": DISABLED_APP}, failing=frozenset({"records"}))))

    @pytest.mark.parametrize("gate", GATES.values(), ids=GATES.keys())
    async def test_a_hidden_demo_record_stays_not_found(self, gate: Any) -> None:  # noqa: ANN401
        with _hidden_demo():
            answer = await gate(_Graph({"records": {"id": RECORD_ID, "connectorId": CONNECTOR_ID}}))
        assert isinstance(answer, HTTPException) and (answer.status_code, answer.detail) == (404, "Record not found")

    async def test_an_open_of_a_disabled_connector_record_stays_409(self) -> None:
        with _hidden_demo(hidden=False):
            answer = await _details(_Graph({"records": CONNECTOR_DOC, "apps": DISABLED_APP}), open_=True)
        assert isinstance(answer, HTTPException) and answer.status_code == 409

    @pytest.mark.parametrize("gate", GATES.values(), ids=GATES.keys())
    async def test_a_record_shown_to_the_person_is_answered(self, gate: Any) -> None:  # noqa: ANN401
        with _hidden_demo(hidden=False):
            answer = await gate(_Graph({"records": {"id": RECORD_ID, "connectorId": "jira-1"}}))
        assert not isinstance(answer, HTTPException), answer

    @pytest.mark.parametrize("gate", GATES.values(), ids=GATES.keys())
    async def test_a_record_gone_after_the_check_keeps_its_answer(self, gate: Any) -> None:  # noqa: ANN401
        """Deleted between the access check and this read: absence, not an outage."""
        with _hidden_demo():
            answer = await gate(_Graph({"records": None}))
        assert not isinstance(answer, HTTPException), answer


class TestTheAccessCheckThatCouldNotBeMade:
    """``accessible_node_ids`` raises when the graph could not answer: the same 503 as a failed read, not a 500."""

    @pytest.mark.parametrize("route", [
        lambda: router_mod.get_record_content(RECORD_ID, _request(), graph_provider=_Graph({})),
        lambda: records_mod._check_access(_request(), _Graph({}), RECORD_ID),
    ], ids=["record_content", "query_record_read"])
    async def test_is_503(self, route: Any) -> None:  # noqa: ANN401
        unanswered = PermissionVerificationUnavailableError()
        with patch.object(router_mod, "accessible_node_ids", AsyncMock(side_effect=unanswered)), \
             patch.object(records_mod, "accessible_node_ids", AsyncMock(side_effect=unanswered)):
            answer = await _answer(route())
        assert isinstance(answer, HTTPException), f"the route answered {answer!r}"
        assert (answer.status_code, answer.detail) == (503, DATABASE_UNREACHABLE)
        assert answer.headers["Retry-After"] == "5"
        assert answer.__cause__ is unanswered


class TestTheUnexpectedFailure:
    """A failure that is no graph read answers 500, and the route writes no line of its own: the request log has it."""

    @pytest.mark.parametrize("gate", [
        lambda g, r: _details(g, open_=False, request=r),
        lambda g, r: _content(g, request=r),
        lambda g, r: _read_gate(g, request=r),
    ], ids=["record_details", "record_content", "query_record_read"])
    async def test_is_500_with_no_route_line(self, gate: Any) -> None:  # noqa: ANN401
        logger = MagicMock()
        with patch.object(router_mod, "refuse_hidden_demo_record", AsyncMock(side_effect=RuntimeError("bug"))), \
             patch.object(records_mod, "refuse_hidden_demo_record", AsyncMock(side_effect=RuntimeError("bug"))):
            answer = await gate(_Graph({"records": {"id": RECORD_ID}}), _request(logger))
        assert isinstance(answer, HTTPException) and answer.status_code == 500
        assert isinstance(answer.__cause__, RuntimeError)
        assert not logger.error.called and not logger.warning.called and not logger.exception.called


# ---- The reindex lock guards ------------------------------------------------------------------------------------

GUARDS = {
    "record": (router_mod.require_connector_not_locked_for_record, "records"),
    "record_group": (router_mod.require_connector_not_locked_for_record_group, "recordGroups"),
}


def _owner(collection: str, **doc: object) -> dict[str, dict | None]:
    return {collection: {"id": RECORD_ID, "origin": "CONNECTOR", "connectorId": CONNECTOR_ID, **doc}, "apps": LOCKED_APP}


class TestTheLockGuards:
    @pytest.mark.parametrize("read", ["own", "apps"])
    @pytest.mark.parametrize("guard", GUARDS)
    async def test_a_failed_read_is_503(self, guard: str, read: str) -> None:
        check, own = GUARDS[guard]
        failing = frozenset({own if read == "own" else "apps"})
        _assert_outage(await _answer(check(RECORD_ID, _Graph(_owner(own), failing=failing))))

    @pytest.mark.parametrize("guard", GUARDS)
    async def test_a_locked_connector_is_409(self, guard: str) -> None:
        check, own = GUARDS[guard]
        answer = await _answer(check(RECORD_ID, _Graph(_owner(own))))
        assert isinstance(answer, HTTPException) and answer.status_code == 409

    @pytest.mark.parametrize("guard", GUARDS)
    async def test_an_unlocked_connector_passes(self, guard: str) -> None:
        check, own = GUARDS[guard]
        docs = {**_owner(own), "apps": {**LOCKED_APP, "isLocked": False}}
        assert await check(RECORD_ID, _Graph(docs)) is None

    @pytest.mark.parametrize("missing", ["own", "apps"])
    @pytest.mark.parametrize("guard", GUARDS)
    async def test_an_absent_document_passes(self, guard: str, missing: str) -> None:
        check, own = GUARDS[guard]
        docs = {**_owner(own), (own if missing == "own" else "apps"): None}
        assert await check(RECORD_ID, _Graph(docs)) is None

    async def test_a_record_not_from_a_connector_passes_without_reading_its_app(self) -> None:
        graph = _Graph(_owner("records", origin="UPLOAD"), failing=frozenset({"apps"}))
        assert await router_mod.require_connector_not_locked_for_record(RECORD_ID, graph) is None
