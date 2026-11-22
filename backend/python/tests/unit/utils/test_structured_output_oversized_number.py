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


# --- The runaway itself: a bounded schema and a capped reply -----------------------------


@pytest.mark.parametrize("schema", [TableSummaryHeaders, TableEnrichment])
def test_the_header_row_count_goes_out_bounded(schema: type[BaseModel]) -> None:
    field = schema.model_json_schema()["properties"]["header_row_count"]
    assert (field["minimum"], field["maximum"]) == (0, 1)


@pytest.mark.parametrize(
    ("answer", "expected"),
    [(1, 1), (0, 0), (True, 1), ("1", 1), (1.0, 1), (7, 0), (-1, 0), pytest.param("1" * 5000, 0, id="runaway-digits"), pytest.param("²", 0, id="non-ascii-digit"), (None, 0)],
)
def test_an_out_of_range_count_reads_as_no_header_row(answer: object, expected: int) -> None:
    for schema in (TableSummaryHeaders, TableEnrichment):
        assert schema.model_validate({"header_row_count": answer}).header_row_count == expected


def _gemini(**kwargs: object):  # noqa: ANN202
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(model="gemini-test", google_api_key="offline", **kwargs)


def test_the_cap_is_set_on_a_copy_of_the_model() -> None:
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import ChatOpenAI

    gemini = _gemini()
    capped = streaming.with_output_cap(gemini, 2048)
    assert capped.max_output_tokens == 2048
    assert gemini.max_output_tokens is None
    # Reasoning counts against the same limit, so a reasoning model gets an allowance on top.
    assert streaming.with_output_cap(_gemini(reasoning_effort="low"), 2048).max_output_tokens == (
        2048 + streaming.REASONING_OUTPUT_RESERVE
    )
    assert streaming.with_output_cap(ChatOpenAI(model="m", api_key="offline"), 2048).max_tokens == 2048
    thinking = ChatAnthropic(
        model="m", api_key="offline", max_tokens=64000, thinking={"type": "enabled", "budget_tokens": 3000}
    )
    # Anthropic requires max_tokens above the thinking budget.
    assert streaming.with_output_cap(thinking, 2048).max_tokens == 5048
    # A lower limit of the model's own stands; an unknown model is left alone.
    assert streaming.with_output_cap(_gemini(max_output_tokens=1000), 2048) is None
    assert streaming.with_output_cap(MagicMock(), 2048) is None
    assert streaming.with_output_cap(gemini, None) is None


@pytest.mark.asyncio
async def test_table_calls_carry_an_output_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    caps: list[object] = []

    async def _invoke(_llm: object, _messages: object, schema: type[BaseModel], *_a: object, **kw: object):  # noqa: ANN202
        caps.append(kw.get("max_output_tokens"))

    monkeypatch.setattr(table_enrichment, "invoke_with_structured_output_and_reflection", _invoke)
    await enrich_table_grid(MagicMock(), GRID, logger=logging.getLogger("t"))
    assert len(caps) == 2
    first, summary = caps
    assert isinstance(first, int) and 0 < first <= table_enrichment.FIRST_CALL_MAX_OUTPUT_TOKENS
    assert isinstance(summary, int) and 0 < summary <= table_enrichment.SUMMARY_CALL_MAX_OUTPUT_TOKENS


@pytest.mark.asyncio
async def test_a_reply_cut_off_at_the_cap_degrades_once_and_is_not_asked_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A real Gemini model with its transport stubbed: every reply stops at the cap mid-JSON."""
    from unittest.mock import patch

    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_google_genai import ChatGoogleGenerativeAI

    sent_caps: list[object] = []

    async def _cut_off(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN202
        sent_caps.append(self.max_output_tokens)
        # LangChain's partial JSON parser would repair this into {"summary": "Ages of"}.
        message = AIMessage(content='{"summary": "Ages of', response_metadata={"finish_reason": "MAX_TOKENS"})
        return ChatResult(generations=[ChatGeneration(message=message)])

    with patch.object(ChatGoogleGenerativeAI, "_agenerate", _cut_off), caplog.at_level(logging.WARNING):
        result = await enrich_table_grid(_gemini(), GRID, logger=logging.getLogger("t"))

    # The first call and the summary follow-up, each once, each capped.
    assert len(sent_caps) == 2
    assert all(isinstance(cap, int) and cap <= table_enrichment.FIRST_CALL_MAX_OUTPUT_TOKENS for cap in sent_caps)
    assert result.failed and result.degraded
    assert result.summary != "Ages of"
    cut = [r for r in caplog.records if "output cap" in r.getMessage()]
    assert len(cut) == 2
    assert not [r for r in caplog.records if "call failed" in r.getMessage()]
