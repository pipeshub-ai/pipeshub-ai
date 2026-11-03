"""The single-call fallback reads a record's text by character budget, not five
blocks at a time, and reads a long block in pieces instead of its first 3,000
characters.

Indexed blocks are short (a few hundred characters), so five of them filled a
call with about a thousand characters and the three-call cap stopped at block
15. Half the records of a 100k-document corpus went PARTIAL that way, and the
end of every block over 3,000 characters was never shown to the model.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING

from app.modules.named_entities.domain.config import NamedEntityBudgets
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.extractor import (
    NamedEntityExtractor,
    NamedEntityRequest,
)
from app.modules.named_entities.strategies.single_call import (
    SingleCallExtractionStrategy,
    pack_windows,
)
from app.modules.named_entities.text import TextUnit

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def _unit(index: int, text: str) -> TextUnit:
    return TextUnit(index, f"b{index}", text)


class TestPacking:
    def test_short_blocks_share_a_window_up_to_the_budget(self) -> None:
        units = [_unit(i, "x" * 95) for i in range(30)]

        windows = pack_windows(units, budget=1_000)

        assert [len(w) for w in windows] == [10, 10, 10]
        assert all(sum(p.end - p.start for p in w) <= 1_000 for w in windows)

    def test_a_long_block_is_read_whole_in_pieces(self) -> None:
        text = " ".join(f"word{i}" for i in range(4_000))
        windows = pack_windows([_unit(0, "short"), _unit(1, text), _unit(2, "tail")], budget=8_000)

        pieces = [p for w in windows for p in w if p.unit.block_index == 1]
        assert "".join(text[p.start:p.end] for p in pieces) == text
        assert all(p.end - p.start <= 8_000 for p in pieces)
        # Cut at a space, so no word is split between two calls.
        assert all(text[p.end - 1] == " " or p.end == len(text) for p in pieces)

    def test_every_block_keeps_its_order(self) -> None:
        units = [_unit(i, "y" * (50 + 400 * (i % 3))) for i in range(40)]

        windows = pack_windows(units, budget=2_000)

        order = [(p.unit.block_index, p.start) for w in windows for p in w]
        assert order == sorted(order)
        assert {p.unit.block_index for w in windows for p in w} == set(range(40))


def _request(blocks: list[str], **budgets: int) -> NamedEntityRequest:
    return NamedEntityRequest(
        blocks=[SimpleNamespace(index=i, id=f"b{i}", type="text", data=text) for i, text in enumerate(blocks)],
        org_id="org",
        enabled=frozenset({EntityKind.PERSON}),
        budgets=NamedEntityBudgets(**budgets),
        provider_name="p",
        model_name="m",
        supports_tools=False,
    )


def _names_in_prompt() -> tuple[Callable[[str, str], Awaitable[str]], list[str]]:
    """Reply with every `Person<N> Example` the prompt shows, in its block."""
    prompts: list[str] = []

    async def complete(system: str, prompt: str) -> str:
        del system
        prompts.append(prompt)
        entities = []
        for block, body in re.findall(r"\[B(\d+)\] (.*)", prompt):
            entities += [
                {"text": name, "block": int(block), "kind": "person", "normalized": ""}
                for name in re.findall(r"Person\d+ Example", body)
            ]
        return json.dumps({"entities": entities})

    return complete, prompts


class TestCoverage:
    async def test_forty_short_blocks_are_read_in_one_call(self) -> None:
        complete, prompts = _names_in_prompt()
        blocks = [f"Person{i} Example joined the review." for i in range(40)]

        result = await NamedEntityExtractor(single=SingleCallExtractionStrategy(complete=complete)).extract(
            _request(blocks),
        )

        assert len(prompts) == 1
        assert result.status == "COMPLETED"
        assert len(result.entities) == 40

    async def test_a_name_past_the_first_3000_characters_of_a_block_is_found_where_it_is(self) -> None:
        complete, prompts = _names_in_prompt()
        filler = "The quarterly plan covers hiring and budget. " * 300
        block = filler + "Person7 Example owns the rollout. " + filler

        result = await NamedEntityExtractor(single=SingleCallExtractionStrategy(complete=complete)).extract(
            _request([block]),
        )

        assert any("Person7 Example" in prompt for prompt in prompts)
        (entity,) = result.entities
        (mention,) = entity.mentions
        assert block[mention.char_start:mention.char_end] == "Person7 Example"
        assert mention.char_start == len(filler)

    async def test_text_past_the_call_cap_is_partial(self) -> None:
        complete, prompts = _names_in_prompt()
        blocks = [f"Person{i} Example " + "x" * 80 for i in range(30)]

        result = await NamedEntityExtractor(single=SingleCallExtractionStrategy(complete=complete)).extract(
            _request(blocks, max_window_chars=1_000, max_llm_windows=2),
        )

        assert result.status == "PARTIAL"
        assert (result.stats.windows_total, result.stats.windows_run) == (3, 2)
        assert len(prompts) == 2
        assert len(result.entities) == 20
