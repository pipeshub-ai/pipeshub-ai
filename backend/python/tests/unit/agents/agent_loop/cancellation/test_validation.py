"""`validate_run_id` (`app/agents/agent_loop/cancellation/validation.py`) —
shared between `ChatQuery.runId` (stream side, optional) and
`CancelRunRequest.runId` (cancel side, required)."""

from __future__ import annotations

import uuid

import pytest

from app.agents.agent_loop.cancellation.validation import validate_run_id


class TestValidateRunId:
    def test_none_is_valid_and_passed_through(self) -> None:
        assert validate_run_id(None) is None

    def test_a_valid_uuid_string_is_passed_through_unchanged(self) -> None:
        run_id = str(uuid.uuid4())
        assert validate_run_id(run_id) == run_id

    def test_a_non_uuid_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid runId"):
            validate_run_id("not-a-uuid")

    def test_an_empty_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid runId"):
            validate_run_id("")
