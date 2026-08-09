"""Tests for Agent Builder → SDK transformer."""
from __future__ import annotations

import ast

import pytest

from app.services.workflows.migration.agent_config_transformer import (
    dual_run_check,
    transform_agent_config,
)


def test_transform_produces_valid_python():
    config = {
        "name": "Daily Jira Report",
        "description": "Generate a daily report of open Jira issues",
        "system_prompt": "Look at all open issues in MYPROJ and create a summary",
        "tools": [{"name": "jira/search_issues"}, {"name": "slack/send_message"}],
        "skills": [],
    }
    result = transform_agent_config(config)
    source = result.source

    ast.parse(source)  # must be valid Python

    assert "@workflow" in source
    assert "from app.services.workflows.sdk import" in source
    assert "datetime.now()" not in source


def test_transform_sanitizes_name():
    config = {"name": "My Agent (v2)!", "description": ""}
    result = transform_agent_config(config)
    ast.parse(result.source)  # should not raise


def test_transform_includes_tool_steps():
    config = {
        "name": "tool_agent",
        "description": "",
        "tools": [{"name": "jira/search_issues"}],
    }
    result = transform_agent_config(config)
    assert "jira/search_issues" in result.source
    ast.parse(result.source)


def test_transform_includes_knowledge_retrieval():
    config = {
        "name": "kb_agent",
        "description": "",
        "knowledge": [{"id": "kb-001"}],
    }
    result = transform_agent_config(config)
    assert "knowledge/search" in result.source
    ast.parse(result.source)


def test_transform_warns_on_unknown_keys():
    config = {
        "name": "warn_agent",
        "description": "",
        "some_unknown_key": "value",
    }
    result = transform_agent_config(config)
    assert any("some_unknown_key" in w for w in result.warnings)


def test_transform_result_has_source_and_warnings():
    config = {"name": "basic_agent", "description": "A simple agent"}
    result = transform_agent_config(config)
    assert isinstance(result.source, str)
    assert isinstance(result.warnings, list)


def test_dual_run_check_similar():
    result = dual_run_check(
        {},
        "The Jira report has 5 open bugs in MYPROJ. Critical: 2, High: 3.",
        "Jira report for MYPROJ: 5 open bugs. Critical 2, High 3. Summary generated.",
    )
    assert result["passes"]
    assert result["similarity_score"] > 0


def test_dual_run_check_dissimilar():
    result = dual_run_check(
        {},
        "Jira bugs: 5 open in MYPROJ",
        "The weather is sunny today with 25 degrees.",
    )
    assert not result["passes"]


def test_dual_run_check_identical():
    text = "exact same output from both sides"
    result = dual_run_check({}, text, text)
    assert result["similarity_score"] == 1.0
    assert result["passes"]
