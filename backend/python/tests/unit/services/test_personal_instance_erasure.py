"""C-14 (SYNCFIX1): deleting a user or an org erases their personal toolset and MCP instances.

Personal toolset instances (main #4324) live one key per instance under their owner, with sign-ins at
``/services/toolsets/{id}/{signer}``, tool rules and an owner pointer beside them. These tests run the
production erasure and the connectors route against one in-memory config store holding two users of
one org and a user of another org, and check every key afterwards.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agents import toolset_store
from app.api.routes import entity
from app.services.personal_instance_erasure import (
    PersonalDataRemainsError,
    erase_personal_instances,
)
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService


def _toolset(org: str, owner: str, instance_id: str) -> dict[str, Any]:
    return {"_id": instance_id, "orgId": org, "createdBy": owner, "scope": "personal", "toolsetType": "jira",
            "instanceName": instance_id, "authType": "OAUTH", "auth": {"clientId": "c", "clientSecret": "s"}}


def _mcp(org: str, owner: str, instance_id: str) -> dict[str, Any]:
    return {"_id": instance_id, "orgId": org, "createdBy": owner, "scope": "personal", "name": instance_id}


def _person(org: str, user: str, instance_id: str) -> dict[str, Any]:
    """One person's personal toolset instance with everything stored for it, and their MCP server."""
    return {
        f"/services/toolset-user-instances/{org}/{user}/{instance_id}": _toolset(org, user, instance_id),
        f"/services/toolset-personal-owners/{instance_id}": {"orgId": org, "userId": user},
        f"/services/toolset-tool-rules/{instance_id}": {"tools": {"create_issue": "allow"}},
        f"/services/toolsets/{instance_id}/{user}": {"isAuthenticated": True, "credentials": {"refresh_token": "r"}},
        f"/services/toolsets/{instance_id}/agent-{user}": {"isAuthenticated": True},
        f"/services/mcp/user-instances/{org}/{user}/mcp-{instance_id}": _mcp(org, user, f"mcp-{instance_id}"),
        f"/services/mcp/credentials/mcp-{instance_id}/{user}": {"token": "t"},
    }


def _world() -> FakeConfigService:
    return FakeConfigService({
        "/services/toolset-instances": [
            {"_id": "org-jira", "orgId": "org-1", "toolsetType": "jira", "authType": "OAUTH"},
            {"_id": "other-jira", "orgId": "org-2", "toolsetType": "jira", "authType": "OAUTH"},
        ],
        **_person("org-1", "u-alice", "alice-jira"),
        **_person("org-1", "u-bob", "bob-jira"),
        **_person("org-2", "u-alice", "alice-elsewhere"),
        # A record no one can read is still Alice's: it is under her key.
        "/services/toolset-user-instances/org-1/u-alice/broken": "not a record",
        # Sign-ins to the org's own instance: Alice's (with a nested key), Bob's, and a user whose id starts with hers.
        "/services/toolsets/org-jira/u-alice": {"isAuthenticated": True},
        "/services/toolsets/org-jira/u-alice/dcr": {"clientId": "x"},
        "/services/toolsets/org-jira/u-bob": {"isAuthenticated": True},
        "/services/toolsets/org-jira/u-alice2": {"isAuthenticated": True},
        "/services/toolsets/other-jira/u-alice": {"isAuthenticated": True},
    })


def _alice_org1(key: str) -> bool:
    return (
        "alice-jira" in key
        or "/org-1/u-alice/" in key
        or key in ("/services/toolsets/org-jira/u-alice", "/services/toolsets/org-jira/u-alice/dcr")
    )


@pytest.fixture(autouse=True)
def _no_refresh_service() -> Any:  # noqa: ANN401 - fixture
    with patch.object(toolset_store, "_refresh_service", return_value=None):
        yield


class TestAServiceAccountIsRemoved:
    """FOLLOWUPS1-2: a service account is an ordinary user record, so it may add its own action or MCP server
    through the API; Node's service-account removal erases them as a person's deletion does (same route, same
    keys), and the people of the org keep theirs."""

    async def test_its_personal_instances_go_and_a_persons_stay(self) -> None:
        store = _world()
        store.data.update(_person("org-1", "svc-nightly", "svc-jira"))
        store.data["/services/toolsets/org-jira/svc-nightly"] = {"isAuthenticated": True}
        people = {**_person("org-1", "u-alice", "alice-jira"), **_person("org-1", "u-bob", "bob-jira")}

        erased = await erase_personal_instances(store, "org-1", "svc-nightly")  # type: ignore[arg-type]

        assert erased["toolsetInstances"] == 1
        assert [key for key in store.data if "svc-nightly" in key or "svc-jira" in key] == []
        for key, value in people.items():
            assert store.data[key] == value, key
        assert "/services/toolsets/org-jira/u-alice" in store.data


class TestAUserIsDeleted:
    async def test_every_path_of_their_personal_instances_and_their_sign_ins_go(self) -> None:
        store = _world()
        before = set(store.data)

        erased = await erase_personal_instances(store, "org-1", "u-alice")  # type: ignore[arg-type]

        assert erased["toolsetInstances"] == 2
        assert erased["mcpKeys"] >= 2
        assert [key for key in store.data if _alice_org1(key)] == []
        assert set(store.data) == {key for key in before if not _alice_org1(key)}

    async def test_another_user_and_the_same_user_in_another_org_are_untouched(self) -> None:
        store = _world()
        await erase_personal_instances(store, "org-1", "u-alice")  # type: ignore[arg-type]

        for key, value in {**_person("org-1", "u-bob", "bob-jira"), **_person("org-2", "u-alice", "alice-elsewhere")}.items():
            assert store.data[key] == value, key
        for kept in ("/services/toolsets/org-jira/u-bob", "/services/toolsets/org-jira/u-alice2", "/services/toolsets/other-jira/u-alice"):
            assert kept in store.data
        assert [i["_id"] for i in store.data["/services/toolset-instances"]] == ["org-jira", "other-jira"]

    async def test_it_runs_again_without_finding_anything(self) -> None:
        store = _world()
        await erase_personal_instances(store, "org-1", "u-alice")  # type: ignore[arg-type]
        assert (await erase_personal_instances(store, "org-1", "u-alice"))["toolsetInstances"] == 0  # type: ignore[arg-type]

    async def test_their_token_refreshes_stop(self) -> None:
        refresher = MagicMock()
        with patch.object(toolset_store, "_refresh_service", return_value=refresher):
            await erase_personal_instances(_world(), "org-1", "u-alice")  # type: ignore[arg-type]
        assert {c.args[0] for c in refresher.cancel_refresh_tasks_for_instance.call_args_list} == {"alice-jira", "broken"}
        assert {c.args[0] for c in refresher.cancel_refresh_task.call_args_list} == {
            "/services/toolsets/org-jira/u-alice", "/services/toolsets/org-jira/u-alice/dcr",
        }

    async def test_a_key_that_would_not_go_fails_the_erasure_so_it_is_retried(self) -> None:
        class Stuck(FakeConfigService):
            async def delete_config(self, key: str) -> bool:
                if key == "/services/toolsets/alice-jira/u-alice":
                    raise ConnectionError("store down")
                return await super().delete_config(key)

        store = Stuck(_world().data)
        with pytest.raises(PersonalDataRemainsError) as failed:
            await erase_personal_instances(store, "org-1", "u-alice")  # type: ignore[arg-type]
        assert failed.value.remaining == ["/services/toolsets/alice-jira/u-alice"]

    @pytest.mark.parametrize(("org", "user"), [("org-1", ""), ("", "u-alice"), ("org-1", "u-alice/../u-bob")])
    async def test_an_id_that_would_widen_the_erasure_is_refused(self, org: str, user: str) -> None:
        store = _world()
        with pytest.raises(ValueError):
            await erase_personal_instances(store, org, user)  # type: ignore[arg-type]
        assert store.deletes == []


class TestAnOrgIsDeleted:
    async def test_every_members_personal_instances_and_every_sign_in_to_its_instances_go(self) -> None:
        store = _world()
        erased = await erase_personal_instances(store, "org-1", None)  # type: ignore[arg-type]

        assert erased["toolsetInstances"] == 3
        assert [k for k in store.data if "/org-1/" in k or "alice-jira" in k or "bob-jira" in k or "/org-jira/" in k] == []
        for key, value in _person("org-2", "u-alice", "alice-elsewhere").items():
            assert store.data[key] == value, key
        assert "/services/toolsets/other-jira/u-alice" in store.data


class TestTheConnectorsRoute:
    @staticmethod
    def _request(store: FakeConfigService, claims: dict[str, Any]) -> MagicMock:
        request = MagicMock()
        request.state.user = claims
        request.app.container.config_service.return_value = store
        request.app.container.logger.return_value = MagicMock()
        return request

    async def test_the_org_is_the_tokens_and_the_control_plane_is_told(self) -> None:
        store = _world()
        sync = AsyncMock(return_value=True)
        with patch.object(entity, "sync_capabilities", sync):
            answer = await entity.erase_personal_instances_route(
                self._request(store, {"orgId": "org-1", "scopes": ["principal:erase"]}),
                entity.PersonalInstancesEraseRequest(userId="u-alice"),
            )
        assert answer.status_code == 200
        assert [key for key in store.data if _alice_org1(key)] == []
        assert "/services/toolset-user-instances/org-2/u-alice/alice-elsewhere" in store.data
        sync.assert_awaited_once_with(store, "org-1")

    async def test_what_remains_answers_503(self) -> None:
        with patch.object(entity, "erase_personal_instances", AsyncMock(side_effect=PersonalDataRemainsError(["k"]))):
            with pytest.raises(HTTPException) as refused:
                await entity.erase_personal_instances_route(
                    self._request(_world(), {"orgId": "org-1"}), entity.PersonalInstancesEraseRequest(userId="u-alice"),
                )
        assert refused.value.status_code == 503

    def test_the_body_names_no_org_and_no_path(self) -> None:
        with pytest.raises(ValidationError):
            entity.PersonalInstancesEraseRequest.model_validate({"userId": "u-alice", "orgId": "org-2"})
        with pytest.raises(ValidationError):
            entity.PersonalInstancesEraseRequest.model_validate({"userId": "../u-bob"})
