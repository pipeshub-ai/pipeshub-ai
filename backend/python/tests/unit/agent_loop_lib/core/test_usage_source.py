"""`llm_usage_source`: the label a model call's usage is reported under."""

from __future__ import annotations

import asyncio

from app.agent_loop_lib.core.usage_source import (
    MAIN_SOURCE,
    current_llm_usage_source,
    llm_usage_source,
)


def test_the_default_is_the_main_agent() -> None:
    assert current_llm_usage_source() == MAIN_SOURCE


def test_a_block_sets_its_label_and_restores_the_outer_one() -> None:
    with llm_usage_source("sub_agent:researcher"):
        with llm_usage_source("plan_critic"):
            assert current_llm_usage_source() == "plan_critic"
        assert current_llm_usage_source() == "sub_agent:researcher"
    assert current_llm_usage_source() == MAIN_SOURCE


def test_the_label_is_restored_when_the_block_raises() -> None:
    try:
        with llm_usage_source("planner"):
            raise RuntimeError("model call failed")
    except RuntimeError:
        pass
    assert current_llm_usage_source() == MAIN_SOURCE


async def test_concurrent_tasks_keep_their_own_label() -> None:
    """Parallel sub-agents run as separate tasks; one must not relabel another."""
    seen: dict[str, str] = {}

    async def child(name: str) -> None:
        with llm_usage_source(f"sub_agent:{name}"):
            await asyncio.sleep(0)
            seen[name] = current_llm_usage_source()

    with llm_usage_source("main"):
        await asyncio.gather(child("a"), child("b"))

    assert seen == {"a": "sub_agent:a", "b": "sub_agent:b"}
