"""A toolset instance's tool approval rules (`/api/v1/toolsets/instances/{id}/tool-rules`): everyone in
the organization reads them, only an admin saves them, a save names only the toolset's own tools,
and deleting the instance deletes them. Tool lists say what each tool does to data."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

if TYPE_CHECKING:
    from types import ModuleType

_RULES_KEY = "/services/toolset-tool-rules/inst-j"
_JIRA_TOOLS = [
    {"name": "search_issues", "tags": [{"key": "type", "value": "read"}]},
    {"name": "create_issue", "tags": [{"key": "type", "value": "write"}]},
    {"name": "delete_issue", "tags": [{"key": "type", "value": "destructive"}]},
    {"name": "remove_watcher", "tags": [{"key": "type", "value": "write"}]},
]


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {
            "/services/toolset-instances": [
                {"_id": "inst-j", "orgId": "o1", "instanceName": "Jira (Engineering)", "toolsetType": "jira"},
                {"_id": "inst-other", "orgId": "o2", "instanceName": "Their Jira", "toolsetType": "jira"},
            ],
        }

    async def get_config(self, key: str, default: Any = None, **_kwargs: Any) -> Any:  # noqa: ANN401
        return self.values.get(key, default)

    async def set_config(self, key: str, value: Any, **_kwargs: Any) -> bool:  # noqa: ANN401
        self.values[key] = value
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None

    async def list_keys_in_directory(self, prefix: str) -> list[str]:
        return [key for key in self.values if key.startswith(prefix)]


@pytest.fixture
def toolsets() -> ModuleType:
    # The routes module only loads cleanly once app.edition_config is imported first.
    import app.edition_config  # noqa: F401
    from app.api.routes import toolsets as module

    return module


@pytest.fixture(autouse=True)
def _organization_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """These routes serve an admin's rules; `TestWithoutOrganizationRules` turns them off."""
    monkeypatch.setattr("app.edition_config.organization_toolset_tool_rules", True, raising=False)


def _registry() -> MagicMock:
    registry = MagicMock()
    registry.get_toolset_metadata.return_value = {"name": "jira", "tools": _JIRA_TOOLS}
    return registry


def _as(toolsets: ModuleType, *, admin: bool, org_id: str = "o1") -> Any:  # noqa: ANN401
    """Patches in a signed-in caller."""
    return _Patches(
        patch.object(toolsets, "_get_user_context", return_value={"user_id": "u1", "org_id": org_id}),
        patch.object(toolsets, "_check_user_is_admin", new=AsyncMock(return_value=admin)),
        patch.object(toolsets, "_get_registry", return_value=_registry()),
    )


class _Patches:
    def __init__(self, *patches: Any) -> None:  # noqa: ANN401
        self._patches = patches

    def __enter__(self) -> None:
        for p in self._patches:
            p.__enter__()

    def __exit__(self, *exc: Any) -> None:  # noqa: ANN401
        for p in reversed(self._patches):
            p.__exit__(*exc)


async def _get(toolsets: ModuleType, store: _Store, instance_id: str = "inst-j", **who: Any) -> dict[str, Any]:  # noqa: ANN401
    with _as(toolsets, **{"admin": False, **who}):
        return await toolsets.get_toolset_tool_rules(instance_id, MagicMock(), config_service=store)


async def _put(toolsets: ModuleType, store: _Store, tools: dict[str, str], instance_id: str = "inst-j", **who: Any) -> dict[str, Any]:  # noqa: ANN401
    body = toolsets.ToolsetToolRulesBody(tools=tools)
    with _as(toolsets, **{"admin": True, **who}):
        return await toolsets.update_toolset_tool_rules(instance_id, body, MagicMock(), config_service=store)


class TestReading:
    async def test_a_member_reads_the_rules_and_cant_edit_them(self, toolsets: ModuleType) -> None:
        store = _Store()
        store.values[_RULES_KEY] = {"tools": {"create_issue": "allow"}}

        assert await _get(toolsets, store) == {"tools": {"create_issue": "allow"}, "canEdit": False}

    async def test_an_admin_can_edit_them(self, toolsets: ModuleType) -> None:
        assert await _get(toolsets, _Store(), admin=True) == {"tools": {}, "canEdit": True}

    @pytest.mark.parametrize("instance_id", ["inst-other", "nope"])
    async def test_another_organizations_or_an_unknown_instance_isnt_found(self, toolsets: ModuleType, instance_id: str) -> None:
        with pytest.raises(HTTPException) as exc:
            await _get(toolsets, _Store(), instance_id)
        assert exc.value.status_code == 404


class TestSaving:
    async def test_an_admin_saves_rules_for_everyone(self, toolsets: ModuleType) -> None:
        store = _Store()
        saved = await _put(toolsets, store, {"create_issue": "allow", "delete_issue": "ask"})

        assert saved == {"tools": {"create_issue": "allow", "delete_issue": "ask"}, "canEdit": True}
        assert store.values[_RULES_KEY] == {"tools": {"create_issue": "allow", "delete_issue": "ask"}}
        assert (await _get(toolsets, store))["tools"] == {"create_issue": "allow", "delete_issue": "ask"}

    async def test_a_save_replaces_the_rules(self, toolsets: ModuleType) -> None:
        store = _Store()
        await _put(toolsets, store, {"create_issue": "allow"})
        await _put(toolsets, store, {"delete_issue": "ask"})
        assert store.values[_RULES_KEY] == {"tools": {"delete_issue": "ask"}}

    async def test_a_member_cant_save(self, toolsets: ModuleType) -> None:
        store = _Store()
        with pytest.raises(HTTPException) as exc:
            await _put(toolsets, store, {"create_issue": "allow"}, admin=False)
        assert exc.value.status_code == 403
        assert _RULES_KEY not in store.values

    async def test_another_organizations_instance_isnt_found(self, toolsets: ModuleType) -> None:
        store = _Store()
        with pytest.raises(HTTPException) as exc:
            await _put(toolsets, store, {"create_issue": "allow"}, instance_id="inst-other")
        assert exc.value.status_code == 404
        assert "/services/toolset-tool-rules/inst-other" not in store.values

    async def test_a_tool_the_toolset_doesnt_have_is_refused(self, toolsets: ModuleType) -> None:
        store = _Store()
        with pytest.raises(HTTPException) as exc:
            await _put(toolsets, store, {"create_issue": "allow", "drop_database": "allow"})
        assert exc.value.status_code == 400
        assert "drop_database" in exc.value.detail
        assert _RULES_KEY not in store.values

    @pytest.mark.parametrize("tools", [{"create_issue": "maybe"}, {"create_issue": None}, ["create_issue"]])
    def test_only_the_three_rules_are_accepted(self, toolsets: ModuleType, tools: Any) -> None:  # noqa: ANN401
        with pytest.raises(ValidationError):
            toolsets.ToolsetToolRulesBody.model_validate({"tools": tools})


class TestDeletingTheInstance:
    async def test_deletes_its_rules(self, toolsets: ModuleType) -> None:
        store = _Store()
        store.values[_RULES_KEY] = {"tools": {"create_issue": "allow"}}
        graph = MagicMock()
        graph.check_toolset_instance_in_use = AsyncMock(return_value=[])
        with _as(toolsets, admin=True), \
             patch.object(toolsets, "_load_instances_for_mutation", new=AsyncMock(return_value=list(store.values["/services/toolset-instances"]))), \
             patch.object(toolsets, "_get_graph_provider", return_value=graph):
            await toolsets.delete_toolset_instance("inst-j", MagicMock(), config_service=store)

        assert _RULES_KEY not in store.values
        assert [i["_id"] for i in store.values["/services/toolset-instances"]] == ["inst-other"]


class TestTheToolsetsSchema:
    async def test_says_what_each_tool_does_to_data(self, toolsets: ModuleType) -> None:
        registry = _registry()
        registry.get_toolset_metadata.return_value = {
            "name": "jira", "display_name": "Jira", "description": "", "category": "app", "supported_auth_types": [],
            "tools": _JIRA_TOOLS,
        }
        request = MagicMock()
        request.app.state.oauth_config_registry = None
        with patch.object(toolsets, "_get_registry", return_value=registry):
            schema = await toolsets.get_toolset_schema("jira", request)

        # A deleting name counts whatever the tag says, as it does for the approval gate.
        assert {tool["name"]: tool["kind"] for tool in schema["toolset"]["tools"]} == {
            "search_issues": "read", "create_issue": "write", "delete_issue": "destructive", "remove_watcher": "destructive",
        }


class TestWithoutOrganizationRules:
    """The default build has no organization rules: both routes answer 404, and nothing is saved."""

    @pytest.fixture(autouse=True)
    def _off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.edition_config.organization_toolset_tool_rules", False, raising=False)

    async def test_reading_isnt_found(self, toolsets: ModuleType) -> None:
        with pytest.raises(HTTPException) as exc:
            await _get(toolsets, _Store(), admin=True)
        assert exc.value.status_code == 404

    async def test_saving_isnt_found_and_saves_nothing(self, toolsets: ModuleType) -> None:
        store = _Store()
        with pytest.raises(HTTPException) as exc:
            await _put(toolsets, store, {"create_issue": "ask"})
        assert exc.value.status_code == 404
        assert _RULES_KEY not in store.values
