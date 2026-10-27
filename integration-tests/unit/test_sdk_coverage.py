"""The SDK coverage list counts only what a test really calls."""

from __future__ import annotations

import pytest

from sdk_tests.coverage import (
    generated_operations,
    handwritten_operations,
    sdk_operations,
)

pytestmark = pytest.mark.unit


def test_only_operations_marked_for_the_sdk_are_counted():
    spec = {
        "paths": {
            "/agents": {
                "parameters": [],
                "get": {
                    "operationId": "listAgents",
                    "tags": ["Agents"],
                    "x-pipeshub-sdk": True,
                },
                "post": {"operationId": "internalCreate", "tags": ["Agents"]},
            },
            "/org": {"get": {"operationId": "getOrg", "x-pipeshub-sdk": True}},
        }
    }

    assert sdk_operations(spec) == {"listAgents": "Agents", "getOrg": "Untagged"}


def test_generated_operations_come_from_every_step_of_every_workflow():
    arazzo = {
        "workflows": [
            {"steps": [{"operationId": "createAgent"}, {"operationId": "deleteAgent"}]},
            {"steps": [{"operationId": "createAgent"}, {"workflowId": "another"}]},
        ]
    }

    assert generated_operations(arazzo) == {"createAgent", "deleteAgent"}


def test_handwritten_list_ignores_comments_and_blank_lines():
    assert handwritten_operations("# why\n\nstreamChat\n  uploadRecords  \n") == {
        "streamChat",
        "uploadRecords",
    }
