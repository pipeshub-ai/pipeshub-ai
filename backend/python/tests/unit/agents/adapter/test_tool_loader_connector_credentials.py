"""Each connector's tools must be built with that connector's own saved
credentials.

The loader matches a connector's short name (Google Drive's is "drive") to
the agent's configured toolsets by suffix, so "googledrive" counts as
"drive". But "onedrive" ends in "drive" too. An agent with OneDrive and
Google Drive could build Google Drive's client from OneDrive's credentials,
and an agent with only OneDrive tried to load Google Drive at all — then
reported Google Drive as needing sign-in, a connector the user never added.

Drives the real `PipesHubToolLoader` and `ToolInstanceCreator`; the
connector catalogue and the client factories (the OAuth/network boundary)
are replaced with recording fakes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.tools.decorators import tool
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.instance_creator import ToolInstanceCreator
from app.agents.agent_loop.tool_loader import PipesHubToolLoader
from app.agents.tools.factories.registry import ClientFactoryRegistry

if TYPE_CHECKING:
    from contextlib import AbstractContextManager


class _RecordingFactory:
    def __init__(self, label: str) -> None:
        self.label = label
        self.configs: list[dict] = []

    async def create_client(self, config_service: Any, logger: Any, config: dict, state: dict) -> dict:  # noqa: ANN401
        self.configs.append(config)
        return {"factory": self.label, "config": config}


class _GoogleDrive:
    def __init__(self, client: dict) -> None:
        self.client = client

    @tool(path="/tools/drive/list_files", short_description="List files", description="List Drive files")
    async def list_files(self) -> tuple[bool, str]:
        return True, "[]"


class _OneDrive:
    def __init__(self, client: dict) -> None:
        self.client = client

    @tool(path="/tools/onedrive/list_items", short_description="List items", description="List OneDrive items")
    async def list_items(self) -> tuple[bool, str]:
        return True, "[]"


_GOOGLE_CREDS = {"auth": {"clientId": "google-client"}}
_ONEDRIVE_CREDS = {"auth": {"clientId": "microsoft-client"}}


@pytest.fixture
def factories(monkeypatch: pytest.MonkeyPatch) -> dict[str, _RecordingFactory]:
    google = _RecordingFactory("google-drive")
    onedrive = _RecordingFactory("onedrive")
    # Same shape as production: Google registers one factory under both its
    # short and long names.
    monkeypatch.setattr(ClientFactoryRegistry, "_factories", {
        "drive": google, "googledrive": google, "onedrive": onedrive,
    })
    monkeypatch.setattr(ClientFactoryRegistry, "_initialized", True)
    return {"google": google, "onedrive": onedrive}


def _context(toolsets: list[dict], logger: Any = None) -> AgentContext:  # noqa: ANN401
    return AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=logger or MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
        agent_toolsets=toolsets,
        toolset_configs={"gd-1": _GOOGLE_CREDS, "od-1": _ONEDRIVE_CREDS},
    )


def _catalogue() -> "AbstractContextManager[MagicMock]":
    fake = MagicMock()
    fake.get_all_toolsets.return_value = {
        "drive": {"class": _GoogleDrive, "isInternal": False, "description": "Google Drive"},
        "onedrive": {"class": _OneDrive, "isInternal": False, "description": "OneDrive"},
    }
    return patch("app.agents.registry.toolset_registry.get_toolset_registry", return_value=fake)


_BOTH = [{"name": "onedrive", "instanceId": "od-1"}, {"name": "googledrive", "instanceId": "gd-1"}]


class TestEachConnectorGetsItsOwnCredentials:
    async def test_google_drive_uses_google_credentials_when_onedrive_is_listed_first(
        self, factories: dict[str, _RecordingFactory],
    ) -> None:
        context = _context(_BOTH)
        with _catalogue():
            registry = await PipesHubToolLoader().load(context)

        assert factories["google"].configs == [_GOOGLE_CREDS]
        assert factories["onedrive"].configs == [_ONEDRIVE_CREDS]
        assert {"drive", "onedrive"} <= {g.name for g in registry.toolsets()}

    async def test_onedrive_only_agent_does_not_load_or_flag_google_drive(
        self, factories: dict[str, _RecordingFactory],
    ) -> None:
        context = _context([{"name": "onedrive", "instanceId": "od-1"}])
        with _catalogue():
            registry = await PipesHubToolLoader().load(context)

        assert factories["google"].configs == []
        assert "drive" not in context.toolset_load_failures
        assert [g.name for g in registry.toolsets()] == ["onedrive"]

    async def test_short_name_still_finds_the_long_configured_name(
        self, factories: dict[str, _RecordingFactory],
    ) -> None:
        context = _context([{"name": "googledrive", "instanceId": "gd-1"}])
        with _catalogue():
            await PipesHubToolLoader().load(context)

        assert factories["google"].configs == [_GOOGLE_CREDS]


class TestClientReuseWithinARequest:
    async def test_second_instance_reuses_the_cached_client(self, factories: dict[str, _RecordingFactory]) -> None:
        context = _context([{"name": "googledrive", "instanceId": "gd-1"}])
        creator = ToolInstanceCreator(context)

        first = await creator.create_instance_async(_GoogleDrive, "drive")
        second = await ToolInstanceCreator(context).create_instance_async(_GoogleDrive, "drive")

        assert first.client is second.client
        assert len(factories["google"].configs) == 1

    async def test_config_is_looked_up_by_tool_name_when_given(self, factories: dict[str, _RecordingFactory]) -> None:
        context = _context([])
        context.tool_to_toolset_map = {"drive__list_files": "gd-1"}

        instance = await ToolInstanceCreator(context).create_instance_async(_GoogleDrive, "drive", "drive__list_files")

        assert instance.client["config"] == _GOOGLE_CREDS


class _Raising:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def create_client(self, *_args: Any) -> dict:  # noqa: ANN401
        raise self.exc


class TestALoadFailureIsLoggedOnce:
    """P3b: the instance creator raises, the loader logs `toolset.load_failed` once: classed, no error
    text, with or without a `logger` in the request's state."""

    @pytest.mark.parametrize("with_state_logger", [True, False])
    async def test_one_classed_line_without_text(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, with_state_logger: bool,
    ) -> None:
        import logging

        from app.utils.logger import log_record_fields

        caplog.set_level(logging.DEBUG)
        monkeypatch.setattr(ClientFactoryRegistry, "_factories", {"onedrive": _Raising(KeyError("secret-ish-text-551"))})
        monkeypatch.setattr(ClientFactoryRegistry, "_initialized", True)
        # A real logger: a second line from the instance creator (it logs through `context.logger`) must show.
        context = _context([{"name": "onedrive", "instanceId": "od-1"}], logging.getLogger("tests.instance_creator"))
        if not with_state_logger:
            context.tool_state.pop("logger", None)
        with _catalogue():
            await PipesHubToolLoader().load(context)

        assert context.toolset_load_failures == {"onedrive": "error"}
        (failed,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
        fields = log_record_fields(failed)
        assert (failed.getMessage(), fields["toolset"], fields["reason"], fields["error_class"]) == (
            "toolset.load_failed", "onedrive", "error", "internal",
        )
        assert "error_message" not in fields and "secret-ish-text-551" not in caplog.text

    async def test_not_authenticated_is_a_warning_without_a_stack(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        from app.agents.tools.factories.base import ToolsetAuthError
        from app.utils.logger import log_record_fields

        caplog.set_level(logging.DEBUG)
        monkeypatch.setattr(ClientFactoryRegistry, "_factories", {"onedrive": _Raising(ToolsetAuthError("sign in"))})
        monkeypatch.setattr(ClientFactoryRegistry, "_initialized", True)
        context = _context([{"name": "onedrive", "instanceId": "od-1"}])
        with _catalogue():
            await PipesHubToolLoader().load(context)

        (failed,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert (failed.levelno, failed.exc_text) == (logging.WARNING, None)
        assert (log_record_fields(failed)["reason"], log_record_fields(failed)["error_class"]) == ("not_authenticated", "auth_expired")

    async def test_a_toolset_that_cant_be_grouped_is_logged(
        self, factories: dict[str, _RecordingFactory], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        from app.agent_loop_lib.tools.registry import ToolRegistry
        from app.utils.logger import log_record_fields

        caplog.set_level(logging.DEBUG)

        secret = "duplicate group sentinel-781"  # a variable: the logged traceback quotes the raise line

        def _refuse(*_args: Any, **_kwargs: Any) -> None:  # noqa: ANN401
            raise ValueError(secret)

        monkeypatch.setattr(ToolRegistry, "register_toolset", _refuse)
        with _catalogue():
            await PipesHubToolLoader().load(_context([{"name": "onedrive", "instanceId": "od-1"}]))

        (failed,) = [r for r in caplog.records if r.levelno >= logging.WARNING]
        fields = log_record_fields(failed)
        assert (failed.getMessage(), fields["toolset"]) == ("toolset.group_failed", "onedrive")
        assert "error_message" not in fields and "sentinel-781" not in caplog.text
