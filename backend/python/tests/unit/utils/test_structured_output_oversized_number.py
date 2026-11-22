"""A reply whose JSON holds a number too long for Python to convert is a reply that does not fit
the schema, not a failed call.

A model can run an integer field on until its output limit (tens of thousands of digits).
Python refuses to convert an integer string longer than ``sys.get_int_max_str_digits()``
(4300 by default), and the JSON parser behind ``with_structured_output`` lets that
``ValueError`` out as is. The helper must not report it as a call error, and a table must
keep what the model did answer.
"""

import json
import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel

from app.modules.parsers.excel.prompt_template import (
    TableEnrichment,
    TableSummaryHeaders,
)
from app.utils import streaming, table_enrichment
from app.utils.table_enrichment import enrich_table_grid, row_keys

# A reply cut off by the output limit inside an integer field, as such replies arrive.
_DIGITS = 65_460
_RUNAWAY = '{"summary": "", "headers": [], "header_row_count": ' + "1" * _DIGITS

GRID = [["Name", "Age"], ["Ann", "30"], ["Bob", "41"]]


class _Answer(BaseModel):
    count: int = 0


def _parse(schema: type[BaseModel], text: str) -> BaseModel:
    """What ``with_structured_output`` does with the reply text."""
    return PydanticOutputParser(pydantic_object=schema).parse(text)


def _runaway_reply(schema: type[BaseModel]):  # noqa: ANN202
    calls: list[int] = []

    async def _invoke(*_args: object, **_kwargs: object) -> BaseModel:
        calls.append(1)
        return _parse(schema, _RUNAWAY)

    return _invoke, calls


def test_the_parser_raises_a_plain_value_error() -> None:
    """The failure this guards against: not a JSON or validation error, so nothing else catches it."""
    with pytest.raises(ValueError, match="integer string conversion"):
        _parse(_Answer, _RUNAWAY)


@pytest.mark.asyncio
@pytest.mark.parametrize("raise_call_errors", [False, True])
async def test_a_runaway_number_is_an_unusable_reply_not_a_call_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raise_call_errors: bool
) -> None:
    invoke, calls = _runaway_reply(_Answer)
    monkeypatch.setattr(streaming, "_ainvoke_throttled", invoke)
    with caplog.at_level(logging.WARNING, logger=streaming.logger.name):
        answer = await streaming.invoke_with_structured_output_and_reflection(
            MagicMock(), [], _Answer, raise_call_errors=raise_call_errors
        )
    assert answer is None
    # Not asked again: there is no reply to show the model, and a second runaway costs as much.
    assert len(calls) == 1
    lines = [r.getMessage() for r in caplog.records if "too long" in r.getMessage()]
    assert len(lines) == 1
    assert "_Answer" in lines[0] and f"{_DIGITS} digits" in lines[0]
    assert "1" * 100 not in lines[0]


def test_a_runaway_number_is_classed_as_a_bad_answer() -> None:
    try:
        _parse(_Answer, _RUNAWAY)
    except ValueError as e:
        assert streaming.answer_error_class(e) == "bad_request"
    assert streaming.answer_error_class(ValueError("something else")) is None


@pytest.mark.asyncio
async def test_a_table_keeps_its_rows_when_the_summary_reply_runs_away(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The first reply gives headers and rows but no summary; the summary follow-up runs away."""
    keys = row_keys(GRID)
    first = json.dumps({
        "summary": "",
        "headers": ["Name", "Age"],
        "header_row_count": 1,
        "descriptions": [{"key": k, "description": f"row {i}"} for i, k in enumerate(keys[1:], 1)],
    })
    replies: list[str] = []

    async def _invoke(_runnable: object, messages: list, **_kwargs: object) -> BaseModel:
        prompt = "\n".join(str(m.content) for m in messages)
        if '"descriptions"' in prompt:
            replies.append("first")
            return _parse(TableEnrichment, first)
        replies.append("summary")
        return _parse(TableSummaryHeaders, _RUNAWAY)

    monkeypatch.setattr(streaming, "_ainvoke_throttled", _invoke)
    with caplog.at_level(logging.WARNING):
        result = await enrich_table_grid(MagicMock(), GRID, logger=logging.getLogger("t"))

    assert replies == ["first", "summary"]
    assert not result.failed
    assert result.degraded
    assert result.headers == ["Name", "Age"]
    assert result.descriptions == ["row 1", "row 2"]
    assert result.summary == table_enrichment.fallback_table_summary(["Name", "Age"], 2)
    # Reported once, as an unusable reply, not as a failed call.
    assert not [r for r in caplog.records if "call failed" in r.getMessage()]
    assert len([r for r in caplog.records if "too long" in r.getMessage()]) == 1
