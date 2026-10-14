from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.modules.named_entities.linking.verifier import (
    LlmMentionVerifier,
    OfferedCandidate,
    VerifyItem,
    build_prompt,
    offerable,
    parse_reply,
)

# Look-alikes a vector search returns as close matches.
LOOK_ALIKES = [
    VerifyItem("Acme Robotics", "organization", [OfferedCandidate("Acme Financial", "organization", "4 records")]),
    VerifyItem("Initech", "organization", [OfferedCandidate("Initrode", "organization", "2 records")]),
    VerifyItem("Globex Systems", "organization", [
        OfferedCandidate("Platform", "product", "3+ records"),
        OfferedCandidate("Globex Systems Inc", "organization", "1 record"),
    ]),
]


class _Llm:
    def __init__(self, reply: str = "", *, delay: float = 0.0, error: Exception | None = None) -> None:
        self.reply, self.delay, self.error = reply, delay, error
        self.prompts: list[str] = []

    async def ainvoke(self, messages: list[Any], config: dict[str, Any] | None = None) -> AIMessage:
        self.prompts.append(messages[-1].content)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return AIMessage(content=self.reply, usage_metadata={"input_tokens": 120, "output_tokens": 9, "total_tokens": 129})


def _verifier(llm: _Llm | None, **kwargs: object) -> LlmMentionVerifier:
    async def provider() -> tuple[Any, str]:
        return llm, "test-model"

    return LlmMentionVerifier(provider, **kwargs)


def test_the_prompt_names_the_question_and_numbers_candidates_per_name() -> None:
    prompt = build_prompt("Who supplies Acme Robotics?", LOOK_ALIKES)
    assert "Who supplies Acme Robotics?" in prompt
    assert 'Name 1: "Acme Robotics" (organization)' in prompt
    assert "  1. Acme Financial (organization, 4 records)" in prompt
    assert "  2. Globex Systems Inc (organization, 1 record)" in prompt
    assert '"3": [...]' in prompt and "Choose none" in prompt


@pytest.mark.parametrize(("reply", "expected"), [
    ('{"1": [], "2": [], "3": [2]}', {0: [], 1: [], 2: [1]}),
    ('Sure: {"1": [1], "3": [2, 2, 1]}', {0: [0], 2: [1, 0]}),
    ('{"1": [3, 0, -1, true, "x", 1.5], "9": [1], "2": "1"}', {0: []}),
    ('```json\n{"1": ["1", 2.0]}\n```', {0: [0]}),
    ('{1: [1]}', None),
    ("no json here", None),
    ("[1, 2]", None),
])
def test_only_numbers_offered_for_that_name_are_accepted(reply: str, expected: dict[int, list[int]] | None) -> None:
    assert parse_reply(reply, LOOK_ALIKES) == expected


def test_a_name_differing_by_a_digit_is_never_offered() -> None:
    assert not offerable("Q3 2024 plan", "Q4 2024 plan")
    assert offerable("Acme", "ACME")


@pytest.mark.asyncio
async def test_a_valid_reply_is_returned_and_its_usage_recorded() -> None:
    usage: list[Any] = []
    llm = _Llm('{"1": [], "2": [], "3": [2]}')
    decided = await _verifier(llm, on_llm_usage=usage.append).verify("q", LOOK_ALIKES)
    assert decided == {0: [], 1: [], 2: [1]}
    assert [m.usage_metadata["input_tokens"] for m in usage] == [120]
    assert len(llm.prompts) == 1


@pytest.mark.asyncio
async def test_a_failing_usage_sink_does_not_lose_the_answer() -> None:
    def broken(message: object) -> None:
        raise RuntimeError("ledger gone")

    llm = _Llm('{"1": [], "2": [], "3": [2]}')
    assert await _verifier(llm, on_llm_usage=broken).verify("q", LOOK_ALIKES) == {0: [], 1: [], 2: [1]}


@pytest.mark.asyncio
async def test_a_stalled_model_lookup_is_inside_the_deadline() -> None:
    async def provider() -> tuple[Any, str]:
        await asyncio.sleep(1)
        return _Llm('{"1": [1]}'), "late-model"

    verifier = LlmMentionVerifier(provider, timeout_seconds=0.05)
    assert await verifier.verify("q", LOOK_ALIKES) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("llm", [
    _Llm(error=RuntimeError("provider down")),
    _Llm('{"1": [1]}', delay=0.2),
    _Llm("I cannot help with that."),
    None,
])
async def test_any_failure_is_no_answer(llm: _Llm | None) -> None:
    assert await _verifier(llm, timeout_seconds=0.05).verify("q", LOOK_ALIKES) is None


@pytest.mark.asyncio
async def test_nothing_to_choose_from_needs_no_call() -> None:
    llm = _Llm('{"1": [1]}')
    assert await _verifier(llm).verify("q", [VerifyItem("Acme", None, [])]) == {0: []}
    assert llm.prompts == []
