"""A graph that cannot answer the user lookup is "try again" in the tools and for attachments,
never "User not found.", an empty listing or "no attachments"; the store's own text never
reaches a tool result."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import (
    KnowledgeGraph,
    _get_user_key,
)
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval import selection_scope as mod
from app.modules.retrieval.retrieval_service import PERMISSION_CHECK_UNAVAILABLE_MESSAGE
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.utils.attachment_utils import keep_accessible_attachments

SECRET = "bolt://secret-host:7687 refused"


class Store:
    """Answers as the Neo4j and ArangoDB providers do: a failed user read is None unless raise_on_error."""

    def __init__(self) -> None:
        self.down = False

    async def get_user_by_user_id(self, user_id, *, raise_on_error=False) -> dict | None:
        if self.down:
            if raise_on_error:
                raise ConnectionError(SECRET)
            return None
        return {"_key": "ukey", "userId": user_id}

    async def check_access(self, user_key, org_id, *, node_ids=(), virtual_record_ids=(), **_) -> AccessCheck:
        if self.down:
            raise PermissionVerificationUnavailableError(SECRET)
        return AccessCheck(records_by_vrid={v: f"rec-{v}" for v in virtual_record_ids})


@pytest.fixture(autouse=True)
def _clear_memo() -> None:
    mod._memo.clear()


def _state(store: Store, **extra) -> dict:
    return {"graph_provider": store, "org_id": "org-1", "user_id": "u1", "has_knowledge": True,
            "logger": logging.getLogger("test"), **extra}


async def test_attachments_are_an_error_not_dropped_on_an_outage_and_return_with_the_store() -> None:
    store = Store()
    call = lambda: keep_accessible_attachments(  # noqa: E731
        store, org_id="org-1", user_id="u1", is_service_account=False,
        attachments=[{"virtualRecordId": "v1", "recordId": "x"}], previous_conversations=[],
        logger=logging.getLogger("test"),
    )
    store.down = True
    with pytest.raises(PermissionVerificationUnavailableError):
        await call()
    store.down = False
    kept, _ = await call()
    assert [a["recordId"] for a in kept] == ["rec-v1"]


async def test_the_user_key_lookup_raises_then_answers() -> None:
    store = Store()
    store.down = True
    with pytest.raises(PermissionVerificationUnavailableError):
        await _get_user_key(store, "u1")
    store.down = False
    assert await _get_user_key(store, "u1") == "ukey"


@pytest.mark.parametrize("call", [
    lambda tool: tool.lookup_record(identifiers=["PROJ-1"]),
    lambda tool: tool.navigate(),
])
async def test_the_tools_answer_try_again_not_user_not_found(call) -> None:
    store = Store()
    store.down = True
    success, text = await call(KnowledgeGraph(state=_state(store, apps=["app-1"])))
    assert not success
    assert text == PERMISSION_CHECK_UNAVAILABLE_MESSAGE
    assert "User not found" not in text


# the first reaches the selection browse, the second the bound user lookup in the listing itself
@pytest.mark.parametrize("filters", [{"recordGroups": ["g1"]}, {"connectionApps": ["app-1"]}])
async def test_a_store_failure_in_a_listing_never_shows_the_store_text(filters) -> None:
    store = Store()
    store.down = True
    state = _state(store, apps=["app-1"], filters=filters)
    success, text = await KnowledgeGraph(state=state).list_files()
    assert not success
    assert PERMISSION_CHECK_UNAVAILABLE_MESSAGE in text
    assert "secret-host" not in text
    assert "User not found" not in text


async def test_the_selection_error_has_a_fixed_message_and_keeps_the_cause() -> None:
    store = Store()
    store.check_access = AsyncMock(side_effect=ConnectionError(SECRET))
    with pytest.raises(PermissionVerificationUnavailableError) as err:
        await mod.outside_connection(store, "u1", "org-1", ["x"], {"apps": ["a"]})
    assert str(err.value) == PERMISSION_CHECK_UNAVAILABLE_MESSAGE
    assert isinstance(err.value.__cause__, ConnectionError)
    assert "secret-host" not in str(err.value)


async def test_a_scope_check_that_could_not_answer_is_not_an_identifier_not_found(monkeypatch) -> None:
    from app.agents.actions.knowledge_graph.resolver import RecordResolver

    async def one(_self, raw: str):
        if raw == "PROJ-1":
            raise PermissionVerificationUnavailableError(PERMISSION_CHECK_UNAVAILABLE_MESSAGE)
        raise RuntimeError("no such key")
    monkeypatch.setattr(RecordResolver, "_resolve_one", one)
    resolver = RecordResolver(AsyncMock(), AsyncMock(), "org-1", "u1", "ukey", [])
    assert (await resolver.resolve_many(["PROJ-2"])).not_found_identifiers == ["PROJ-2"]       # any other failure is still a miss
    with pytest.raises(PermissionVerificationUnavailableError):
        await resolver.resolve_many(["PROJ-2", "PROJ-1"])
