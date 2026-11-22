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

    return ChatGoogleGenerativeAI(model=kwargs.pop("model", "gemini-test"), google_api_key="offline", **kwargs)


RESERVE = streaming.REASONING_OUTPUT_RESERVE
CEILING = table_enrichment.TABLE_CALL_MAX_OUTPUT_TOKENS


@pytest.mark.parametrize(
    ("model", "reserve"),
    [
        # Reason by default, with nothing on the model object to say so.
        pytest.param(lambda: _gemini(model="gemini-3.8-flash"), RESERVE, id="gemini-3.8-flash"),
        pytest.param(lambda: _gemini(model="models/gemini-2.5-flash"), RESERVE, id="gemini-2.5-flash"),
        pytest.param(lambda: _openai(model="gpt-6-luna"), RESERVE, id="gpt-6-luna"),
        pytest.param(lambda: _openai(model="o4-mini"), RESERVE, id="o4-mini"),
        # Unknown: when in doubt, the reserve.
        pytest.param(lambda: _gemini(model="gemini-test"), RESERVE, id="unknown-gemini"),
        # Known not to reason, or told not to.
        pytest.param(lambda: _openai(model="gpt-4.1"), 0, id="gpt-4.1"),
        pytest.param(lambda: _openai(model="gpt-4o-mini"), 0, id="gpt-4o-mini"),
        pytest.param(lambda: _gemini(model="gemini-2.0-flash"), 0, id="gemini-2.0-flash"),
        pytest.param(lambda: _gemini(model="gemini-2.5-flash", thinking_budget=0), 0, id="thinking-budget-0"),
        pytest.param(lambda: _openai(model="gpt-6-luna", reasoning_effort="none"), 0, id="reasoning-none"),
        pytest.param(lambda: _anthropic(), 0, id="claude-no-thinking"),
        pytest.param(lambda: _anthropic(thinking={"type": "enabled", "budget_tokens": 3000}), 3000, id="claude-thinking-budget"),
    ],
)
def test_a_model_that_may_reason_keeps_room_to_reason(model, reserve: int) -> None:
    llm = model()
    capped = streaming.with_output_cap(llm, 2048)
    limit = getattr(capped, "max_output_tokens", None) or capped.max_tokens
    assert limit == 2048 + reserve
    # A copy: the model the caller holds keeps its own settings.
    assert capped is not llm


def _openai(**kwargs: object):  # noqa: ANN202
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(api_key="offline", **kwargs)


def _anthropic(**kwargs: object):  # noqa: ANN202
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model="claude-test", api_key="offline", max_tokens=64000, **kwargs)


def test_a_lower_limit_of_the_models_own_stands() -> None:
    own = _gemini(max_output_tokens=1000)
    # The model itself, so the caller still reads the raw reply and its stop reason.
    assert streaming.with_output_cap(own, 2048) is own
    assert streaming.with_output_cap(MagicMock(), 2048) is None
    assert streaming.with_output_cap(_gemini(), None) is None


@pytest.mark.asyncio
async def test_every_table_call_carries_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    caps: list[object] = []

    async def _invoke(_llm: object, _messages: object, schema: type[BaseModel], *_a: object, **kw: object):  # noqa: ANN202
        caps.append((schema.__name__, kw.get("max_output_tokens")))
        if schema.__name__ == "TableSummaryHeaders":
            return TableSummaryHeaders(summary="s", headers=["Name", "Age"], header_row_count=1)
        return None

    monkeypatch.setattr(table_enrichment, "invoke_with_structured_output_and_reflection", _invoke)
    await enrich_table_grid(MagicMock(), GRID, logger=logging.getLogger("t"))
    # First call (no answer), the summary follow-up, then the rows in a batch.
    assert caps == [
        ("TableEnrichment", CEILING), ("TableSummaryHeaders", CEILING), ("KeyedRowDescriptions", CEILING),
    ]


def _cut_off_model(sent: list, text: str):  # noqa: ANN202
    from unittest.mock import patch

    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_google_genai import ChatGoogleGenerativeAI

    async def _cut_off(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN202
        sent.append(self.max_output_tokens)
        message = AIMessage(content=text, response_metadata={"finish_reason": "MAX_TOKENS"})
        return ChatResult(generations=[ChatGeneration(message=message)])

    return patch.object(ChatGoogleGenerativeAI, "_agenerate", _cut_off)


@pytest.mark.asyncio
async def test_a_reply_cut_off_at_the_cap_degrades_once_and_is_not_asked_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A real Gemini model with its transport stubbed: every reply stops at the cap mid-JSON."""
    sent: list[object] = []
    # LangChain's partial JSON parser would repair this into {"summary": "Ages of"}.
    with _cut_off_model(sent, '{"summary": "Ages of'), caplog.at_level(logging.WARNING):
        result = await enrich_table_grid(_gemini(), GRID, logger=logging.getLogger("t"))

    # The first call and the summary follow-up, each once, each capped.
    assert sent == [CEILING + RESERVE] * 2
    assert result.failed and result.degraded
    assert result.summary != "Ages of"
    assert len([r for r in caplog.records if "output cap" in r.getMessage()]) == 2
    assert not [r for r in caplog.records if "call failed" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_cut_off_reply_is_caught_under_the_models_own_lower_limit() -> None:
    sent: list[object] = []
    with _cut_off_model(sent, '{"summary": "Quarterly sales by re'):
        answer = await streaming.invoke_with_structured_output_and_reflection(
            _gemini(max_output_tokens=300), [("user", "x")], TableSummaryHeaders, 1, max_output_tokens=900
        )
    assert sent == [300]
    assert answer is None


@pytest.mark.asyncio
async def test_a_cut_off_row_batch_falls_back_once_and_is_not_split(caplog: pytest.LogCaptureFixture) -> None:
    rows = [{"Name": f"n{i}", "Age": str(i)} for i in range(24)]
    sent: list[object] = []
    body = '{"descriptions": [{"key": "' + row_keys(rows)[0] + '", "description": "' + "and " * 2000
    with _cut_off_model(sent, body), caplog.at_level(logging.WARNING):
        texts = await table_enrichment.describe_row_dicts(_gemini(), rows, "s", logging.getLogger("t"))

    # One call: a cut-off batch is neither split nor asked again.
    assert sent == [CEILING + RESERVE]
    # Every row keeps its plain text; none takes the cut-off description.
    assert texts == [f"Name: n{i}, Age: {i}" for i in range(24)]
    degraded = [r.getMessage() for r in caplog.records if "row batch degraded" in r.getMessage()]
    assert len(degraded) == 1 and "reason=parse" in degraded[0]


def test_a_whole_corpus_sized_reply_fits_under_the_ceiling() -> None:
    """The ceiling is a safety stop, not a budget: the longest whole reply a table batch needs
    stays well under it. Worst case of a 50-row batch, each row a long, wide row."""
    from app.utils.concurrency import TABLE_ROW_BATCH_SIZE

    summary, per_header, per_row = 250, 8, 160  # tokens: verbose summary, header name, long row
    worst = 40 + summary + 30 * per_header + TABLE_ROW_BATCH_SIZE * (12 + per_row)
    assert worst < CEILING
