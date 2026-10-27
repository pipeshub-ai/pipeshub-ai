"""The passages of a record that answer a question, picked out by the reader model.

These pin what the reader model is given (the question and the record's text),
what the caller gets back (the model's text, bounded, with its token use), and
that a model which cannot answer leaves the caller with the plain read.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.blocks import BlockType
from app.utils import record_extract as extract_module
from app.utils.record_extract import (
    EXTRACT_MAX_CHARS,
    NOTHING_RELEVANT,
    READER_ROLE,
    extract_record,
)


def _record(*texts: str) -> dict:
    return {
        "id": "r-1",
        "virtual_record_id": "v-r-1",
        "context_metadata": "Record ID: r-1\nName: FY2025 Annual Report",
        "block_containers": {
            "blocks": [
                {"index": i, "type": BlockType.TEXT.value, "parent_index": None, "data": t}
                for i, t in enumerate(texts)
            ],
            "block_groups": [],
        },
    }


def _llm(reply: str = "65 sponsors", *, error: Exception | None = None) -> MagicMock:
    llm = MagicMock()
    message = SimpleNamespace(content=reply, usage_metadata={"input_tokens": 1200, "output_tokens": 30})
    llm.ainvoke = AsyncMock(side_effect=error) if error else AsyncMock(return_value=message)
    return llm


async def _extract(record: dict, llm: MagicMock, fallback_chars: int = 40_000, **kwargs: object) -> dict:
    config = {"provider": "azureOpenAI", "configuration": {"model": "small-model"}}
    with patch.object(extract_module, "get_llm_for_role", AsyncMock(return_value=(llm, config))) as role:
        result = await extract_record(
            record, query="How many sponsors?", config_service=MagicMock(), fallback_chars=fallback_chars, **kwargs,
        )
    assert role.await_args.args[1] == READER_ROLE
    return result


class TestExtract:
    async def test_the_reader_sees_the_question_and_the_record_and_its_reply_is_returned(self) -> None:
        llm = _llm("The ASF had 65 sponsors.")
        result = await _extract(_record("The ASF had 65 sponsors.", "Other text."), llm)

        prompt = llm.ainvoke.await_args.args[0][1].content
        assert "How many sponsors?" in prompt
        assert "The ASF had 65 sponsors." in prompt
        assert result["content"] == "The ASF had 65 sponsors."
        assert result["order"] == "extract"
        assert result["chars"] == len("The ASF had 65 sponsors.")
        assert result["extract"] == {
            "model": "small-model",
            "provider": "azureOpenAI",
            "nothingRelevant": False,
            "truncated": False,
            "unverifiedLines": 0,
            "sourceChars": result["extract"]["sourceChars"],
            "sourceComplete": True,
            "inputTokens": 1200,
            "outputTokens": 30,
        }
        assert result["extract"]["sourceChars"] > 0

    async def test_an_extract_is_one_response_with_nothing_to_read_on(self) -> None:
        result = await _extract(_record("The ASF had 65 sponsors."), _llm("The ASF had 65 sponsors."))

        assert result["complete"] is True
        assert result["nextStartBlock"] is None
        assert result["maxChars"] == 40_000

    async def test_lines_not_in_the_record_word_for_word_are_counted(self) -> None:
        reply = (
            "[cover page] FY2025 Annual Report\n\n"
            "- **The ASF had 65 sponsors** … across 295 projects.\n"
            "The foundation was backed by 85 sponsors in total."
        )
        record = _record("FY2025 Annual Report", "The ASF had   65 sponsors in FY25, across 295 projects.")

        result = await _extract(record, _llm(reply))

        assert result["extract"]["unverifiedLines"] == 1

    @pytest.mark.parametrize(("record_text", "reply", "unverified"), [
        # A verbatim markdown link: the bracket splits the line, and both sides are found.
        ("Read [the release notes](https://airflow.apache.org/notes) before upgrading.",
         "Read [the release notes](https://airflow.apache.org/notes) before upgrading.", 0),
        # Short quotes are checked too.
        ("The ASF had 65 sponsors.", "65 sponsors", 0),
        ("The ASF had 65 sponsors.", "85 sponsors", 1),
        # A match must not continue a number or a word.
        ("The contract value is $1,250,000.", "The contract value is $1,250", 1),
        ("Ten sponsorships were renewed.", "Ten sponsor", 1),
        # List numbers are part of the text.
        ("1. Acme Corporation paid $10,000.", "9. Acme Corporation paid $10,000.", 1),
        ("1. Acme Corporation paid $10,000.", "1. Acme Corporation paid $10,000.", 0),
        # The reader's own note, in brackets, is not checked; the quote beside it is.
        ("The vote passed on 29 September.", "[dev@ thread, 2026-09-29] The vote passed on 29 September.", 0),
        # A line that is only a note is checked, and so is a link's label.
        ("The ASF had 65 sponsors in FY25.", "[The ASF had 85 sponsors in FY25]", 1),
        ("Read [the release notes](https://airflow.apache.org/notes) first.",
         "Read [85 sponsors](https://airflow.apache.org/notes) first.", 1),
        # A separator at either end may not run into a longer number.
        ("Apache Airflow 2.10.4 was released in March.", "Apache Airflow 2. … released in March.", 1),
        ("The contract value is $1,250.99 a year.", "The contract value is $1,250.", 1),
        ("Revenue was $1,250,000 in all.", "Revenue was $1,250,", 1),
        ("Apache Airflow 2.10.4 was released.", ".10.4 was released.", 1),
        # ... but one followed by a space still matches.
        ("The ASF had 65 sponsors. It grew.", "The ASF had 65 sponsors.", 0),
        ("Signed on September 3, 2024 in Wilmington.", "Signed on September 3,", 0),
        # A word after a number and a period is still a word.
        ("3.Executive summary approved by the board.", "Executive summary approved by the board.", 0),
    ])
    async def test_what_counts_as_a_line_from_the_record(self, record_text: str, reply: str, unverified: int) -> None:
        result = await _extract(_record(record_text), _llm(reply))

        assert result["extract"]["unverifiedLines"] == unverified

    async def test_a_reply_that_finds_nothing_says_so(self) -> None:
        result = await _extract(_record("Unrelated."), _llm(NOTHING_RELEVANT))

        assert result["extract"]["nothingRelevant"] is True

    async def test_a_reply_that_runs_on_is_bounded_and_says_so(self) -> None:
        result = await _extract(_record("text"), _llm("y" * (EXTRACT_MAX_CHARS * 2)))

        assert result["chars"] == EXTRACT_MAX_CHARS
        assert result["extract"]["truncated"] is True

    async def test_a_reply_keeps_within_the_callers_budget_at_a_line_break(self) -> None:
        reply = "\n".join(f"line {i} " + "z" * 90 for i in range(60))

        result = await _extract(_record("text"), _llm(reply), fallback_chars=2_000)

        assert result["chars"] <= 2_000
        assert reply.startswith(result["content"]) and result["content"].endswith("z")
        assert result["extract"]["truncated"] is True

    async def test_a_reply_in_parts_is_joined(self) -> None:
        llm = _llm()
        llm.ainvoke.return_value.content = [{"type": "text", "text": "65 "}, {"type": "text", "text": "sponsors"}]

        result = await _extract(_record("text"), llm)

        assert result["content"] == "65 sponsors"


class TestFallback:
    async def test_an_empty_reply_leaves_the_plain_read(self) -> None:
        result = await _extract(_record("The ASF had 65 sponsors."), _llm("  "))

        assert "65 sponsors" in result["content"]
        assert result["extract"] == {"error": "EmptyReply"}

    async def test_a_refusal_from_the_identity_layer_is_raised_not_read_around(self) -> None:
        refusal = HTTPException(status_code=403, detail="no identity")
        with pytest.raises(HTTPException) as err:
            await _extract(_record("text"), _llm(error=refusal))
        assert err.value is refusal

    async def test_a_budget_above_the_readers_is_read_again_at_the_callers_budget(self) -> None:
        record = _record(*[f"block {i} " + "x" * 400 for i in range(400)])
        with patch.object(extract_module, "get_llm_for_role", AsyncMock(side_effect=ValueError("No LLM found"))):
            result = await extract_record(record, query="sponsors", config_service=MagicMock(), fallback_chars=140_000)

        assert result["maxChars"] == 140_000
        assert result["chars"] > 120_000

    async def test_a_model_that_fails_leaves_the_plain_read_and_says_why(self) -> None:
        result = await _extract(_record("The ASF had 65 sponsors."), _llm(error=TimeoutError()))

        assert "65 sponsors" in result["content"]
        assert result["order"] == "in_order"
        assert result["extract"] == {"error": "TimeoutError"}

    async def test_no_model_configured_leaves_the_plain_read(self) -> None:
        with patch.object(extract_module, "get_llm_for_role", AsyncMock(side_effect=ValueError("No LLM found"))):
            result = await extract_record(
                _record("The ASF had 65 sponsors."), query="sponsors", config_service=MagicMock(), fallback_chars=40_000,
            )

        assert "65 sponsors" in result["content"]
        assert result["extract"] == {"error": "ValueError"}

    async def test_the_plain_read_keeps_the_callers_budget(self) -> None:
        record = _record(*[f"block {i} " + "x" * 400 for i in range(400)])
        with patch.object(extract_module, "get_llm_for_role", AsyncMock(side_effect=ValueError("No LLM found"))):
            result = await extract_record(record, query="sponsors", config_service=MagicMock(), fallback_chars=4_000)

        assert result["chars"] <= 4_000
        assert result["maxChars"] == 4_000
