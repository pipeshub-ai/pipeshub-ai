"""An agent's actions: it may attach only the toolset instances its editor can see, and a personal
one (a user's own, with their sign-in) never goes on an agent other people run."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.agents import toolset_store
from app.api.routes.agent import (
    InvalidRequestError,
    _assert_no_personal_toolsets,
    _assert_toolsets_visible,
    _attached_personal_toolsets,
)
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

if TYPE_CHECKING:
    from collections.abc import Iterator

USER_PATH = "/services/toolset-user-instances/org-1/{}/{}"


def _record(instance_id: str, owner: str, scope: str | None = None) -> dict[str, Any]:
    record = {"_id": instance_id, "instanceName": instance_id, "toolsetType": "jira", "orgId": "org-1", "createdBy": owner}
    if scope:
        record["scope"] = scope
    return record


def _store() -> FakeConfigService:
    return FakeConfigService({
        "/services/toolset-instances": [_record("org-jira", "admin-1")],
        USER_PATH.format("u-alice", "alice-jira"): _record("alice-jira", "u-alice", "personal"),
        USER_PATH.format("u-bob", "bob-jira"): _record("bob-jira", "u-bob", "personal"),
    })


def _attach(*instance_ids: str | None) -> dict[str, dict[str, Any]]:
    return {f"toolset-{n}": {"displayName": f"Toolset {n}", "instanceId": iid, "tools": []} for n, iid in enumerate(instance_ids)}


@pytest.fixture(autouse=True)
def _nothing_looked_up() -> Iterator[None]:
    with patch.object(toolset_store, "resolve_public_http_target", new=lambda *_a, **_k: None):
        yield


class TestTheEditorCanSeeWhatTheyAttach:
    async def test_the_orgs_and_their_own(self) -> None:
        visible = await _assert_toolsets_visible(_attach("org-jira", "alice-jira"), _store(), "org-1", "u-alice")  # type: ignore[arg-type]
        assert sorted(visible) == ["alice-jira", "org-jira"]

    @pytest.mark.parametrize("instance_id", ["bob-jira", "deleted-long-ago"])
    async def test_not_someone_elses_or_one_that_doesnt_exist(self, instance_id: str) -> None:
        with pytest.raises(InvalidRequestError, match="not found"):
            await _assert_toolsets_visible(_attach("org-jira", instance_id), _store(), "org-1", "u-alice")  # type: ignore[arg-type]

    async def test_one_saved_before_instances_existed_is_left_alone(self) -> None:
        assert await _assert_toolsets_visible(_attach(None), _store(), "org-1", "u-alice") == {}  # type: ignore[arg-type]


class TestAPersonalActionStaysOnTheOwnersAgent:
    @pytest.mark.parametrize("shared,service_account", [(True, False), (False, True)])
    async def test_not_on_one_others_run(self, shared: bool, service_account: bool) -> None:
        instances = {"alice-jira": _record("alice-jira", "u-alice", "personal")}
        with pytest.raises(InvalidRequestError, match="Personal actions"):
            _assert_no_personal_toolsets(instances, shared_with_org=shared, service_account=service_account)

    def test_fine_on_their_own_agent_and_an_org_instance_anywhere(self) -> None:
        _assert_no_personal_toolsets({"alice-jira": _record("alice-jira", "u-alice", "personal")}, shared_with_org=False, service_account=False)
        _assert_no_personal_toolsets({"org-jira": _record("org-jira", "admin-1")}, shared_with_org=True, service_account=True)

    async def test_sharing_finds_the_owners_personal_actions_whoever_shares(self) -> None:
        """A co-editor can't see the owner's personal action; the check looks across users."""
        found = await _attached_personal_toolsets({"alice-jira", "org-jira"}, _store(), "org-1")  # type: ignore[arg-type]
        assert list(found) == ["alice-jira"]

    async def test_sharing_isnt_let_through_when_the_store_cant_answer(self) -> None:
        store = _store()

        async def _down(_prefix: str) -> list[str]:
            raise ConnectionError("store down")

        store.list_keys_in_directory = _down  # type: ignore[method-assign]
        with pytest.raises(ConnectionError):
            await _attached_personal_toolsets({"alice-jira"}, store, "org-1")  # type: ignore[arg-type]
