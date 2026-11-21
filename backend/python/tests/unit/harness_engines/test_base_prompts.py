"""ST-5 (S1b, G2-F3): an engine's last message is the result of its task, not an account of what it did."""

from __future__ import annotations

import pytest

from harness_engines.codex import launch as codex_launch
from harness_engines.opencode import launch as opencode_launch


@pytest.mark.parametrize("prompt", [opencode_launch.BASE_PROMPT, codex_launch.BASE_PROMPT])
def test_st_5_the_base_prompt_asks_for_the_result(prompt: str) -> None:
    assert "finish with the result of the task" in prompt
    assert "what you did" not in prompt
