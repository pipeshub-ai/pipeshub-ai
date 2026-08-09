"""Tests for ``WorkflowAgentRunner`` — the concrete ``IWorkflowAgentRunner``
implementation that runs Agent Builder agents from within workflow code.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.workflows.runtime.agent_runner import (
    WorkflowAgentRunner,
    _extract_tool_names,
)


class TestExtractToolNames:
    def test_extracts_from_typical_agent_doc(self) -> None:
        doc = {
            "toolsets": [
                {
                    "_key": "ts-1",
                    "tools": [
                        {"name": "jira__search_issues", "fullName": "jira__search_issues"},
                        {"name": "jira__get_issue", "fullName": "jira__get_issue"},
                    ],
                },
                {
                    "_key": "ts-2",
                    "tools": [
                        {"name": "slack__send_message", "fullName": "slack__send_message"},
                    ],
                },
            ],
        }
        assert _extract_tool_names(doc) == [
            "jira__search_issues", "jira__get_issue", "slack__send_message",
        ]

    def test_returns_empty_when_no_toolsets(self) -> None:
        assert _extract_tool_names({}) == []
        assert _extract_tool_names({"toolsets": []}) == []
        assert _extract_tool_names({"toolsets": None}) == []

    def test_skips_tools_with_no_name(self) -> None:
        doc = {
            "toolsets": [{"tools": [{"name": ""}, {"fullName": "valid__tool"}]}],
        }
        assert _extract_tool_names(doc) == ["valid__tool"]


class TestWorkflowAgentRunnerInit:
    def test_satisfies_protocol_structurally(self) -> None:
        """``WorkflowAgentRunner.run`` must have the same parameters as
        ``IWorkflowAgentRunner.run`` — structural compatibility check
        since the Protocol is not ``@runtime_checkable``."""
        import inspect

        from app.services.workflows.interface.agent_runner import IWorkflowAgentRunner

        protocol_sig = inspect.signature(IWorkflowAgentRunner.run)
        runner_sig = inspect.signature(WorkflowAgentRunner.run)
        assert set(protocol_sig.parameters) == set(runner_sig.parameters)


class TestWorkflowAgentRunnerRun:
    @pytest.mark.asyncio
    async def test_raises_on_missing_agent(self) -> None:
        graph = AsyncMock()
        graph.get_agent = AsyncMock(return_value=None)
        config = AsyncMock()

        runner = WorkflowAgentRunner(graph, config)
        with pytest.raises(ValueError, match="not found"):
            await runner.run(
                agent_id="nonexistent",
                org_id="org-1",
                user_id="user-1",
                goal="do something",
                arguments={},
            )

    @pytest.mark.asyncio
    async def test_resolves_credentials_for_parent_user(self) -> None:
        """The sub-agent inherits the parent workflow's user credentials,
        not the agent creator's."""
        graph = AsyncMock()
        graph.get_agent = AsyncMock(return_value={
            "name": "test-agent",
            "toolsets": [
                {"tools": [{"name": "jira__search_issues"}]},
            ],
            "models": [],
        })

        config = AsyncMock()
        config.get_config = AsyncMock(side_effect=_mock_config_lookup)

        runner = WorkflowAgentRunner(graph, config)

        with (
            patch.object(runner, "_resolve_llm", new_callable=AsyncMock) as mock_llm,
            patch.object(runner._tool_loader, "load", new_callable=AsyncMock) as mock_load,
            patch("app.services.workflows.runtime.agent_runner.Agent") as mock_agent_cls,
        ):
            mock_llm.return_value = (MagicMock(), "gpt-4o")
            mock_registry = MagicMock()
            mock_registry.names.return_value = ["jira__search_issues"]
            mock_load.return_value = mock_registry

            mock_result = MagicMock()
            mock_result.success = True
            mock_result.output = {"issues": []}
            mock_agent_instance = MagicMock()
            mock_agent_instance.run = AsyncMock(return_value=mock_result)
            mock_agent_cls.return_value = mock_agent_instance

            result = await runner.run(
                agent_id="agent-123",
                org_id="org-1",
                user_id="workflow-owner-user",
                goal="Find open bugs",
                arguments={},
            )

            assert result == {"issues": []}
            config.get_config.assert_any_call(
                "/services/toolset-instances", default=[],
            )

    @pytest.mark.asyncio
    async def test_failed_agent_raises_runtime_error(self) -> None:
        graph = AsyncMock()
        graph.get_agent = AsyncMock(return_value={
            "name": "failing-agent",
            "toolsets": [],
            "models": [],
        })
        config = AsyncMock()
        config.get_config = AsyncMock(return_value=[])

        runner = WorkflowAgentRunner(graph, config)

        with (
            patch.object(runner, "_resolve_llm", new_callable=AsyncMock) as mock_llm,
            patch.object(runner._tool_loader, "load", new_callable=AsyncMock) as mock_load,
            patch("app.services.workflows.runtime.agent_runner.Agent") as mock_agent_cls,
        ):
            mock_llm.return_value = (MagicMock(), "gpt-4o")
            mock_registry = MagicMock()
            mock_registry.names.return_value = []
            mock_load.return_value = mock_registry

            mock_result = MagicMock()
            mock_result.success = False
            mock_result.error = "Tool call failed"
            mock_agent_instance = MagicMock()
            mock_agent_instance.run = AsyncMock(return_value=mock_result)
            mock_agent_cls.return_value = mock_agent_instance

            with pytest.raises(RuntimeError, match="Tool call failed"):
                await runner.run(
                    agent_id="agent-456",
                    org_id="org-1",
                    user_id="user-1",
                    goal="Do something impossible",
                    arguments={},
                )


def _mock_config_lookup(path: str, **kwargs: Any) -> Any:
    if path == "/services/toolset-instances":
        return [
            {
                "_id": "instance-jira",
                "toolsetType": "jira",
                "instanceName": "Jira Cloud",
                "tools": [],
                "selectedTools": [],
            },
        ]
    if "toolset-instances/" in path:
        return {"isAuthenticated": True, "accessToken": "fake"}
    return kwargs.get("default")
