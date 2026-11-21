"""A caller that knows the asking organisation adds ``user_lookup_scope(org_id)`` to its user lookup.

The default adds nothing, so each lookup below is the plain call a store with no organisation argument
answers: the user id alone names the user. The graph here takes no ``org_id``, so a lookup that passed
one would fail. The edition's own binding is whatever ``app.edition_config.user_lookup_scope`` returns.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agents.agent_loop.skills.manager_factory import run_user_key
from app.api.routes.chatbot import readable_for_reader
from app.modules.agents.service.builders import _resolve_service_account_binding
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.utils.chat_helpers import accessible_node_names
from app.utils.user_lookup import user_lookup_scope

ORG = "org-1"
PERSON = {"_key": "key-1", "id": "key-1", "userId": "user-1", "orgId": ORG}
SERVICE_ACCOUNT = {
    "_key": "key-sa", "id": "key-sa", "userId": "sa-1", "orgId": ORG, "kind": "service", "isActive": True,
}


class _Graph:
    """A store whose user lookup takes no organisation, as the default signature has it."""

    def __init__(self, *users: dict[str, Any]) -> None:
        self.users = {u["userId"]: u for u in users}
        self.lookups: list[tuple[str, dict[str, Any]]] = []

    async def get_user_by_user_id(self, user_id: str, *, raise_on_error: bool = False) -> dict[str, Any] | None:
        self.lookups.append((user_id, {"raise_on_error": raise_on_error}))
        user = self.users.get(user_id)
        return dict(user) if user else None

    async def check_access(self, user_key: str, org_id: str, *, node_ids: set[str], **_: object) -> AccessCheck:
        return AccessCheck(node_ids=frozenset(node_ids), names={n: f"name of {n}" for n in node_ids})

    async def get_knowledge_hub_access_v3(self, user_key: str, org_id: str, **_: object) -> object:
        return object()


@pytest.fixture
def default_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lookups run with the default binding, whichever one this build installs."""
    monkeypatch.setattr("app.edition_config.user_lookup_scope", user_lookup_scope)


@pytest.mark.parametrize("org_id", [None, "", ORG])
def test_the_default_adds_no_argument(org_id: str | None) -> None:
    assert user_lookup_scope(org_id) == {}


@pytest.mark.usefixtures("default_scope")
class TestEachLookupIsThePlainCall:
    async def test_the_names_a_person_may_open(self) -> None:
        graph = _Graph(PERSON)
        assert await accessible_node_names(graph, {"r1"}, "user-1", ORG) == {"r1": "name of r1"}
        assert graph.lookups == [("user-1", {"raise_on_error": True})]

    async def test_the_reader_check(self) -> None:
        graph = _Graph(PERSON)
        assert await readable_for_reader(graph, {"r1"}, "user-1", ORG) == {"r1": "name of r1"}
        assert graph.lookups == [("user-1", {"raise_on_error": True})]

    async def test_a_service_account_binding(self) -> None:
        graph = _Graph(SERVICE_ACCOUNT)
        assert await _resolve_service_account_binding("sa-1", graph, ORG) == "sa-1"
        assert graph.lookups == [("sa-1", {"raise_on_error": True})]

    async def test_a_runs_user_key(self) -> None:
        graph = _Graph(PERSON)
        context = MagicMock(user_key="", user_id="user-1", org_id=ORG, graph_provider=graph)
        assert await run_user_key(context) == "key-1"
        assert graph.lookups == [("user-1", {"raise_on_error": False})]
