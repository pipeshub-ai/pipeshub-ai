"""Servers popular in the organization: how many people signed in to each, never who."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi import HTTPException

from app.agents.mcp import service as mcp_service
from app.api.routes.mcp_servers import list_popular_servers
from app.core.org_context import OrgContext, bind
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG_PATH = "/services/mcp/instances/{}"
CRED = "/services/mcp/credentials/{}/{}"
ALICE, BOB, CAROL = "6650f1a2b3c4d5e6f7a8b9c0", "6650f1a2b3c4d5e6f7a8b9c1", "6650f1a2b3c4d5e6f7a8b9c2"
AGENT = "3f2b8c1e-4d5a-4f6b-9c7d-8e9f0a1b2c3d"


def _record(instance_id: str, name: str, *, org_id: str = "org-1", scope: str | None = None) -> dict[str, Any]:
    record = {"_id": instance_id, "orgId": org_id, "createdBy": ALICE, "name": name, "typeId": instance_id, "createdAt": 1}
    if scope:
        record["scope"] = scope
    return record


@pytest.fixture(autouse=True)
def _org_1_request() -> Iterator[None]:
    with bind(OrgContext("org-1", ALICE, "user", "jwt", "req-test")):
        yield


def _store() -> FakeConfigService:
    data: dict[str, Any] = {
        ORG_PATH.format("slack"): _record("slack", "Slack"),
        ORG_PATH.format("github"): _record("github", "GitHub"),
        ORG_PATH.format("notion"): _record("notion", "Notion"),
        ORG_PATH.format("elsewhere"): _record("elsewhere", "Other org", org_id="org-2"),
        "/services/mcp/user-instances/org-1/" + ALICE + "/mine": _record("mine", "Mine", scope="personal"),
    }
    for owner in (ALICE, BOB, CAROL):
        data[CRED.format("slack", owner)] = {"isAuthenticated": True}
    # One person's credential, its OAuth tokens and registered client are one person.
    data[CRED.format("github", ALICE)] = {"isAuthenticated": True}
    data[CRED.format("github", ALICE) + "/oauth-tokens"] = {"accessToken": "x"}
    data[CRED.format("github", ALICE) + "/dcr-client"] = {"clientId": "c"}
    data[CRED.format("github", BOB) + "/oauth-tokens"] = {"accessToken": "y"}
    # Neither an agent's own credential nor the shared slot is a person.
    data[CRED.format("notion", AGENT)] = {"isAuthenticated": True}
    data[CRED.format("notion", mcp_service.SHARED_CREDENTIAL_OWNER)] = {"isAuthenticated": True}
    data[CRED.format("elsewhere", CAROL)] = {"isAuthenticated": True}
    data[CRED.format("mine", ALICE)] = {"isAuthenticated": True}
    return FakeConfigService(data)


class TestPopular:
    async def test_counts_people_once_most_used_first(self) -> None:
        result = await list_popular_servers(route_request(_store(), user_id=ALICE), limit=6)

        assert result == {
            "servers": [
                {"instanceId": "slack", "typeId": "slack", "name": "Slack", "people": 3},
                {"instanceId": "github", "typeId": "github", "name": "GitHub", "people": 2},
            ]
        }

    async def test_says_nothing_about_who(self) -> None:
        result = await list_popular_servers(route_request(_store(), user_id=BOB), limit=6)

        assert all(set(row) == {"instanceId", "typeId", "name", "people"} for row in result["servers"])

    async def test_a_member_sees_the_same_counts(self) -> None:
        as_member = await list_popular_servers(route_request(_store(), user_id=BOB), limit=6)
        as_alice = await list_popular_servers(route_request(_store(), user_id=ALICE), limit=6)

        assert as_member == as_alice

    async def test_limit(self) -> None:
        result = await list_popular_servers(route_request(_store(), user_id=ALICE), limit=1)

        assert [row["name"] for row in result["servers"]] == ["Slack"]

    async def test_a_store_that_fails_to_list_counts_nobody(self) -> None:
        class Failing(FakeConfigService):
            async def list_keys_in_directory(self, prefix: str) -> list[str]:
                if "/credentials/" in prefix:
                    raise RuntimeError("store down")
                return await super().list_keys_in_directory(prefix)

        store = _store()
        result = await list_popular_servers(route_request(Failing(store.data), user_id=ALICE), limit=6)

        assert result == {"servers": []}

    async def test_needs_a_signed_in_caller(self) -> None:
        request = route_request(_store(), user_id=ALICE)
        request.state.user = {}
        with pytest.raises(HTTPException):
            await list_popular_servers(request, limit=6)
