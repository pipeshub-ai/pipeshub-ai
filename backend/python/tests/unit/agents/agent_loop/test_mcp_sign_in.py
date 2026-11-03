"""Signing in again from the chat (`mcp_sign_in.py`): which servers the reply's card lists."""
from __future__ import annotations

from typing import Any

import pytest

from app.agents.agent_loop.mcp_access import ResolvedMCPServer
from app.agents.agent_loop.mcp_sign_in import (
    note_sign_in_needed,
    shows_sign_in_card,
    sign_in_part,
)
from app.agents.mcp.errors import MCPHttpStatusError, MCPInsufficientScopeError
from tests.unit.agents.adapter.conftest import make_context


def _web_chat(**overrides: Any) -> Any:  # noqa: ANN401
    """The web chat: where the card is drawn."""
    return make_context(**{
        "client_name": "pipeshub-ai", "chat_streaming": True, "conversation_id": "conv-1", "protocol": "agui",
        **overrides,
    })


def _server(*, owner_id: str = "user-1", instance_id: str = "inst-drive", name: str = "Drive") -> ResolvedMCPServer:
    return ResolvedMCPServer(
        instance_id=instance_id, name=name, display_name=name,
        instance={"_id": instance_id, "authMode": "oauth"}, auth={}, owner_id=owner_id, attached_tools=None,
    )


def _refused(*scopes: str) -> MCPInsufficientScopeError:
    return MCPInsufficientScopeError("needs more permission", scopes=list(scopes))


class TestWhereTheCardShows:
    def test_in_the_web_chat(self) -> None:
        assert shows_sign_in_card(_web_chat()) is True

    @pytest.mark.parametrize("overrides", [
        {"protocol": "legacy"},
        {"client_name": "slack"},
        {"client_name": None},
        {"chat_streaming": False},
        {"conversation_id": None},
    ], ids=["legacy-protocol", "slack", "api", "not-streaming", "no-conversation"])
    def test_nowhere_else(self, overrides: dict[str, Any]) -> None:
        context = _web_chat(**overrides)
        assert shows_sign_in_card(context) is False
        assert note_sign_in_needed(context, _server(), _refused("files.write")) is False
        assert sign_in_part(context) is None

    def test_without_a_context(self) -> None:
        assert shows_sign_in_card(None) is False
        assert note_sign_in_needed(None, _server(), _refused("files.write")) is False
        assert sign_in_part(None) is None


class TestWhatTheCardLists:
    def test_a_server_refused_for_scope(self) -> None:
        context = _web_chat()
        assert note_sign_in_needed(context, _server(), _refused("files.write")) is True
        assert sign_in_part(context) == {
            "type": "mcp_sign_in",
            "servers": [{"instanceId": "inst-drive", "serverName": "Drive", "scopes": ["files.write"]}],
        }

    def test_once_per_server_its_scopes_merged(self) -> None:
        context = _web_chat()
        note_sign_in_needed(context, _server(), _refused("files.read"))
        note_sign_in_needed(context, _server(), _refused("files.write", "files.read"))
        note_sign_in_needed(context, _server(instance_id="inst-gh", name="GitHub"), _refused("repo"))

        assert [(s["serverName"], s["scopes"]) for s in sign_in_part(context)["servers"]] == [
            ("Drive", ["files.read", "files.write"]), ("GitHub", ["repo"]),
        ]

    def test_an_agents_own_sign_in_carries_the_agent(self) -> None:
        context = _web_chat()
        note_sign_in_needed(context, _server(owner_id="agent-7"), _refused("files.write"))
        assert context.mcp_sign_in_needed == [
            {"instanceId": "inst-drive", "serverName": "Drive", "scopes": ["files.write"], "agentKey": "agent-7"},
        ]

    @pytest.mark.parametrize("exc", [
        MCPHttpStatusError(403, challenge={"error": "insufficient_scope", "scope": "files.write"}),
        MCPInsufficientScopeError("needs more", scopes=[]),
        RuntimeError("down"),
    ], ids=["raw-403", "no-scopes", "other"])
    def test_only_a_refusal_signing_in_again_fixes(self, exc: BaseException) -> None:
        context = _web_chat()
        assert note_sign_in_needed(context, _server(), exc) is False
        assert sign_in_part(context) is None

    def test_the_part_is_a_copy(self) -> None:
        context = _web_chat()
        note_sign_in_needed(context, _server(), _refused("files.write"))
        sign_in_part(context)["servers"][0]["scopes"].append("tampered")
        part = sign_in_part(context)
        part["servers"][0]["serverName"] = "changed"
        assert context.mcp_sign_in_needed[0] == {"instanceId": "inst-drive", "serverName": "Drive", "scopes": ["files.write"]}
