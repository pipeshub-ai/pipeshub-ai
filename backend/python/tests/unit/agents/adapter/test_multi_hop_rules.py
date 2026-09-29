"""The multi-step answering rules ship whenever a knowledge retrieval tool is
granted, never otherwise, and stay within a fixed token budget."""

from __future__ import annotations

import re

import pytest

from app.agent_loop_lib.core.tokens import count_text_tokens
from app.agents.agent_loop.prompt_builder import MULTI_HOP_RULES
from tests.unit.agents.adapter.test_prompt_invariants import build_prompt_for_fixture
from tests.unit.agents.adapter.test_surface_golden import INTERNAL_SEARCH_CASE, build_case, pinned_environment

# Measured with the agent loop's own estimator (4 chars per token), which
# runs a little above a real tokenizer for English prose.
_TOKEN_BUDGET = 200


def test_block_fits_its_token_budget() -> None:
    assert count_text_tokens(MULTI_HOP_RULES) <= _TOKEN_BUDGET


def test_block_names_no_tool() -> None:
    assert "`" not in MULTI_HOP_RULES
    assert not re.search(r"\w__\w", MULTI_HOP_RULES)


@pytest.mark.parametrize("fixture_name", ["kb_only", "kb_plus_3_apps", "composed_agents", "lazy_with_pinned"])
def test_present_when_retrieval_is_granted(fixture_name: str) -> None:
    prompt = build_prompt_for_fixture(fixture_name)
    finding = prompt.split("## Finding Information", 1)[1].split("\n## ", 1)[0]
    assert finding.count(MULTI_HOP_RULES) == 1


@pytest.mark.parametrize("fixture_name", ["no_sources", "web_search_mode", "service_only", "run_code_no_web"])
def test_absent_without_retrieval(fixture_name: str) -> None:
    assert "Multi-step questions" not in build_prompt_for_fixture(fixture_name)


async def test_present_in_enterprise_search_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    pinned_environment(monkeypatch)
    prompt = (await build_case(*INTERNAL_SEARCH_CASE))["prompt"]
    assert prompt.count(MULTI_HOP_RULES) == 1
