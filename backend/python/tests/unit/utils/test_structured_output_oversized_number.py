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


def _openai(**kwargs: object):  # noqa: ANN202
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(api_key="offline", **kwargs)


def _anthropic(**kwargs: object):  # noqa: ANN202
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=kwargs.pop("model", "claude-test"), api_key="offline", max_tokens=64000, **kwargs)


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
        # A name that says it reasons, whatever its prefix.
        pytest.param(lambda: _openai(model="gpt-4o-reasoning-ft"), RESERVE, id="gpt-4o-reasoning-ft"),
        pytest.param(lambda: _gemini(model="gemini-2.0-flash-thinking-exp-01-21"), RESERVE, id="gemini-2.0-thinking"),
        pytest.param(lambda: _openai(model="deepseek-reasoner"), RESERVE, id="deepseek-reasoner"),
        # Known not to reason, or told not to.
        pytest.param(lambda: _openai(model="gpt-4.1"), 0, id="gpt-4.1"),
        pytest.param(lambda: _openai(model="gpt-4o-mini"), 0, id="gpt-4o-mini"),
        pytest.param(lambda: _openai(model="gpt-5.2-chat-latest"), 0, id="gpt-5.2-chat-latest"),
        pytest.param(lambda: _openai(model="deepseek-chat"), 0, id="deepseek-chat"),
        pytest.param(lambda: _gemini(model="gemini-2.0-flash"), 0, id="gemini-2.0-flash"),
        pytest.param(lambda: _gemini(model="gemini-2.5-flash", thinking_budget=0), 0, id="thinking-budget-0"),
        pytest.param(lambda: _openai(model="gpt-6-luna", reasoning_effort="none"), 0, id="reasoning-none"),
        pytest.param(_anthropic, 0, id="claude-no-thinking"),
        pytest.param(lambda: _anthropic(thinking={"type": "enabled", "budget_tokens": 3000}), 3000, id="claude-thinking-budget"),
    ],
)
def test_a_model_that_may_reason_keeps_room_to_reason(model, reserve: int) -> None:
    assert streaming._reasoning_reserve(model()) == reserve


@pytest.mark.parametrize(
    ("model", "sent"),
    [
        # The cap plus the reserve, clamped to the most the model accepts (its profile, else the catalog).
        pytest.param(lambda: _gemini(model="gemini-3.8-flash"), 2048 + RESERVE, id="gemini-3.8-flash"),
        pytest.param(lambda: _openai(model="gpt-6-luna"), 2048 + RESERVE, id="gpt-6-luna"),
        pytest.param(lambda: _openai(model="gpt-3.5-turbo"), 2048, id="gpt-3.5-turbo"),
        pytest.param(lambda: _anthropic(thinking={"type": "enabled", "budget_tokens": 3000}, model="claude-sonnet-5-5"),
                     5048, id="claude-thinking"),
    ],
)
def test_the_cap_carries_the_reserve(model, sent: int) -> None:
    llm = model()
    capped = streaming.with_output_cap(llm, 2048)
    assert capped is not llm
    assert (getattr(capped, "max_output_tokens", None) or capped.max_tokens) == sent


@pytest.mark.parametrize(
    ("model", "limit"),
    [
        ("gpt-3.5-turbo", 4096), ("gpt-4", 8192), ("gpt-4-turbo", 4096),
        ("gpt-4o-2024-05-13", 4096), ("gpt-5.2-chat-latest", 16384), ("gpt-5.3-chat-latest", 16384),
    ],
)
def test_the_cap_never_exceeds_what_the_model_accepts(model: str, limit: int) -> None:
    capped = streaming.with_output_cap(_openai(model=model), CEILING)
    assert capped.max_tokens is not None and capped.max_tokens <= limit


@pytest.mark.parametrize(
    "llm",
    [
        pytest.param(lambda: _openai(model="deepseek-chat", base_url="http://127.0.0.1:9/v1"), id="deepseek-compatible"),
        pytest.param(lambda: _openai(model="llama-3.1-8b-instruct", base_url="http://127.0.0.1:9/v1"), id="vllm"),
        pytest.param(lambda: _openai(model="my-model"), id="unknown-openai-name"),
        pytest.param(lambda: _gemini(model="gemini-test"), id="unknown-gemini"),
    ],
)
def test_no_cap_is_sent_when_the_models_limit_is_unknown(llm) -> None:
    model = llm()
    # The model itself: nothing changes in the request, and the raw reply is still read.
    assert streaming.with_output_cap(model, CEILING) is model


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
        result = await enrich_table_grid(_gemini(model="gemini-3.8-flash"), GRID, logger=logging.getLogger("t"))

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
async def test_a_cut_off_row_batch_is_split_once_then_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    rows = [{"Name": f"n{i}", "Age": str(i)} for i in range(24)]
    sent: list[object] = []
    body = '{"descriptions": [{"key": "' + row_keys(rows)[0] + '", "description": "' + "and " * 2000
    with _cut_off_model(sent, body), caplog.at_level(logging.WARNING):
        texts = await table_enrichment.describe_row_dicts(
            _gemini(model="gemini-3.8-flash"), rows, "s", logging.getLogger("t")
        )

    # The batch, then its two halves once; a half cut off again is not split further.
    assert sent == [CEILING + RESERVE] * 3
    # Every row keeps its plain text; none takes the cut-off description.
    assert texts == [f"Name: n{i}, Age: {i}" for i in range(24)]
    degraded = [r.getMessage() for r in caplog.records if "row batch degraded" in r.getMessage()]
    assert len(degraded) == 2 and all("reason=parse" in m for m in degraded)


def _keyed_answer(messages: list) -> object:
    import re

    from app.modules.parsers.excel.prompt_template import KeyedRowDescriptions

    keys = re.findall(r"^\[([A-Za-z2-7]{4})\]:", "\n".join(str(m.content) for m in messages), re.MULTILINE)
    return KeyedRowDescriptions(descriptions=[{"key": k, "description": f"described {k}"} for k in keys]), len(keys)


@pytest.mark.asyncio
async def test_a_batch_too_big_for_its_cap_is_described_by_its_halves(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[int] = []

    async def _invoke(_llm: object, messages: list, schema: type[BaseModel], *_a: object, **_kw: object):  # noqa: ANN202
        answer, n = _keyed_answer(messages)
        sizes.append(n)
        if n > 12:
            raise streaming.TruncatedReplyError("the reply reached its output-token limit")
        return answer

    monkeypatch.setattr(table_enrichment, "invoke_with_structured_output_and_reflection", _invoke)
    rows = [{"Name": f"n{i}"} for i in range(24)]
    texts = await table_enrichment.describe_row_dicts(MagicMock(), rows, "s", logging.getLogger("t"))
    assert sorted(sizes) == [12, 12, 24]
    assert all(t.startswith("described ") for t in texts)


@pytest.mark.asyncio
async def test_a_row_batch_cap_grows_with_its_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    caps: list[object] = []

    async def _invoke(_llm: object, messages: list, schema: type[BaseModel], *_a: object, **kw: object):  # noqa: ANN202
        caps.append(kw.get("max_output_tokens"))
        return _keyed_answer(messages)[0]

    monkeypatch.setattr(table_enrichment, "invoke_with_structured_output_and_reflection", _invoke)
    for n in (10, 300):
        await table_enrichment.describe_row_dicts(
            MagicMock(), [{"A": str(i)} for i in range(n)], "s", logging.getLogger("t")
        )
    assert caps == [CEILING, 300 * table_enrichment.ROW_BATCH_TOKENS_PER_ROW]


class _TooLarge(Exception):
    status_code = 400


@pytest.mark.asyncio
async def test_a_refused_cap_is_dropped_once(caplog: pytest.LogCaptureFixture) -> None:
    """A model whose real limit is below the one on record: the 400 is answered by one resend
    without the cap, not by failing every table."""
    from unittest.mock import patch

    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_openai import ChatOpenAI

    sent: list[object] = []
    ok = TableSummaryHeaders(summary="s", headers=["a"], header_row_count=0).model_dump_json()

    async def _agen(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN202
        sent.append(self.max_tokens)
        if self.max_tokens:
            raise _TooLarge("Error code: 400 - max_tokens is too large: 8192. This model supports at most 4096 completion tokens")
        message = AIMessage(content=ok, response_metadata={"finish_reason": "stop"})
        return ChatResult(generations=[ChatGeneration(message=message)])

    with patch.object(ChatOpenAI, "_agenerate", _agen), caplog.at_level(logging.WARNING):
        answer = await streaming.invoke_with_structured_output_and_reflection(
            _openai(model="gpt-4"), [("user", "x")], TableSummaryHeaders, 1, raise_call_errors=True,
            max_output_tokens=CEILING,
        )
    assert sent == [8192, None]
    assert answer is not None and answer.summary == "s"
    assert len([r for r in caplog.records if "output cap was refused" in r.getMessage()]) == 1


@pytest.mark.asyncio
async def test_another_bad_request_is_not_resent() -> None:
    from unittest.mock import patch

    from langchain_openai import ChatOpenAI

    sent: list[object] = []

    async def _agen(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN202
        sent.append(self.max_tokens)
        raise _TooLarge("Error code: 400 - invalid image")

    with patch.object(ChatOpenAI, "_agenerate", _agen), pytest.raises(_TooLarge):
        await streaming.invoke_with_structured_output_and_reflection(
            _openai(model="gpt-4"), [("user", "x")], TableSummaryHeaders, 1, raise_call_errors=True,
            max_output_tokens=CEILING,
        )
    assert sent == [8192]


@pytest.mark.asyncio
async def test_a_cut_off_batch_is_caught_after_the_modules_are_reloaded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import importlib

    import app.utils.is_truncated as is_truncated_module
    import app.utils.opik as opik_module

    importlib.reload(is_truncated_module)
    importlib.reload(opik_module)
    reloaded = importlib.reload(streaming)
    monkeypatch.setattr(
        table_enrichment, "invoke_with_structured_output_and_reflection",
        reloaded.invoke_with_structured_output_and_reflection,
    )
    sent: list[object] = []
    with _cut_off_model(sent, '{"descriptions": [{"key": "K", "description": "cut'), caplog.at_level(logging.WARNING):
        texts = await table_enrichment.describe_row_dicts(
            _gemini(model="gemini-3.8-flash"), [{"A": str(i)} for i in range(5)], "s", logging.getLogger("t")
        )
    assert texts == [f"A: {i}" for i in range(5)]
    assert not [r for r in caplog.records if "call failed" in r.getMessage()]


def test_a_whole_corpus_sized_reply_fits_under_the_ceiling() -> None:
    """The ceiling is a safety stop, not a budget: the longest whole reply a table batch needs
    stays well under it. Worst case of a 50-row batch, each row a long, wide row."""
    from app.utils.concurrency import TABLE_ROW_BATCH_SIZE

    summary, per_header, per_row = 250, 8, 160  # tokens: verbose summary, header name, long row
    worst = 40 + summary + 30 * per_header + TABLE_ROW_BATCH_SIZE * (12 + per_row)
    assert worst < CEILING


# --- A reply that ran away ends that call's part of the table -----------------------------


def _runaway_rows(rows: list[dict]) -> str:
    return '{"descriptions": [{"key": "' + row_keys(rows)[0] + '", "description": "x"}], "n": ' + "1" * _DIGITS + "}"


@pytest.mark.asyncio
async def test_a_runaway_row_batch_falls_back_without_a_split(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A batch large enough to split: a runaway reply is not asked for again, in halves or whole."""
    from app.modules.parsers.excel.prompt_template import KeyedRowDescriptions

    rows = [{"Name": f"n{i}", "Age": str(i)} for i in range(4 * table_enrichment.MIN_SPLIT_ROWS)]
    calls: list[int] = []

    async def _invoke(*_args: object, **_kwargs: object) -> BaseModel:
        calls.append(1)
        return _parse(KeyedRowDescriptions, _runaway_rows(rows))

    monkeypatch.setattr(streaming, "_ainvoke_throttled", _invoke)
    with caplog.at_level(logging.WARNING):
        texts = await table_enrichment.describe_row_dicts(MagicMock(), rows, "s", logging.getLogger("t"))

    assert len(calls) == 1
    assert texts == [f"Name: n{i}, Age: {i}" for i in range(len(rows))]
    degraded = [r.getMessage() for r in caplog.records if "row batch degraded" in r.getMessage()]
    assert len(degraded) == 1 and "reason=parse" in degraded[0]
    assert not [r for r in caplog.records if "call failed" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_runaway_first_reply_falls_back_without_another_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The summary follow-up would ask for the same header row count that just ran away."""
    replies: list[str] = []

    async def _invoke(_runnable: object, messages: list, **_kwargs: object) -> BaseModel:
        prompt = "\n".join(str(m.content) for m in messages)
        replies.append("first" if '"descriptions"' in prompt else "summary")
        return _parse(TableEnrichment, _RUNAWAY)

    monkeypatch.setattr(streaming, "_ainvoke_throttled", _invoke)
    with caplog.at_level(logging.WARNING):
        result = await enrich_table_grid(MagicMock(), GRID, logger=logging.getLogger("t"))

    assert replies == ["first"]
    assert result.failed and result.degraded
    assert not [r for r in caplog.records if "call failed" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_runaway_number_raises_only_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    invoke, calls = _runaway_reply(_Answer)
    monkeypatch.setattr(streaming, "_ainvoke_throttled", invoke)
    with pytest.raises(streaming.OversizedNumberError):
        await streaming.invoke_with_structured_output_and_reflection(
            MagicMock(), [], _Answer, raise_call_errors=True, raise_oversized_number=True
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_cut_off_reply_is_caught_without_the_structured_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model the structured wrapper cannot be built for returns its message as is."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    def _no_wrapper(*_a: object, **_kw: object):  # noqa: ANN202
        raise NotImplementedError

    monkeypatch.setattr(ChatGoogleGenerativeAI, "with_structured_output", _no_wrapper)
    sent: list[object] = []
    # Valid JSON for the schema, yet the reply stopped at its limit.
    with _cut_off_model(sent, '{"summary": "Ages of", "headers": ["Name"], "header_row_count": 0}'):
        answer = await streaming.invoke_with_structured_output_and_reflection(
            _gemini(model="gemini-3.8-flash"), [("user", "x")], TableSummaryHeaders, 1, max_output_tokens=900
        )
    assert answer is None
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_cut_off_chunk_falls_back_in_get_rows_text() -> None:
    grid = [["Name", "Age"], ["Ann", "30"], ["Bob", "41"]]
    sent: list[object] = []
    with _cut_off_model(sent, '{"descriptions": [{"key": "K", "description": "cut'):
        texts, _rows = await table_enrichment.get_rows_text(
            _gemini(model="gemini-3.8-flash"), {"grid": grid}, "s", ["Name", "Age"]
        )
    assert texts == ["Name: Ann, Age: 30", "Name: Bob, Age: 41"]
    assert len(sent) == 1


def _openai_request_schema(llm: object, schema: type[BaseModel]) -> dict:
    """The JSON schema an OpenAI-style request carries for *schema*."""
    from langchain_core.messages import HumanMessage

    stack, bound = [streaming._apply_structured_output(llm, schema=schema, include_raw=True)], None
    while stack:
        node = stack.pop()
        if getattr(node, "kwargs", None) and hasattr(node, "bound"):
            bound = node
            break
        for attr in ("first", "bound", "steps__", "middle", "last"):
            value = getattr(node, attr, None)
            if isinstance(value, dict):
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
            elif value is not None:
                stack.append(value)
    assert bound is not None
    kwargs = {k: v for k, v in bound.kwargs.items() if k != "ls_structured_output_format"}
    payload = bound.bound._get_request_payload([HumanMessage(content="t")], **kwargs)
    response_format = payload["response_format"]
    if isinstance(response_format, type):
        from openai.lib._parsing._completions import type_to_response_format_param

        response_format = type_to_response_format_param(response_format)
    return response_format["json_schema"]["schema"]


@pytest.mark.parametrize("model", ["ft:gpt-4o-2024-08-06:acme::abc123", "gpt-4.1"])
@pytest.mark.parametrize("schema", [TableSummaryHeaders, TableEnrichment])
def test_an_openai_request_carries_no_number_bounds(model: str, schema: type[BaseModel]) -> None:
    """Fine-tuned OpenAI models refuse minimum/maximum in a strict schema with a 400."""
    field = _openai_request_schema(_openai(model=model), schema)["properties"]["header_row_count"]
    assert field["type"] == "integer"
    assert not {"minimum", "maximum"} & set(field)


def test_the_bounds_are_still_read_leniently_without_the_schema_bound() -> None:
    from app.modules.parsers.excel.prompt_template import TableSummaryHeaders as Schema

    unbounded = streaming._without_number_bounds(Schema)
    assert issubclass(unbounded, Schema)
    assert unbounded.model_validate({"header_row_count": 7}).header_row_count == 0
    assert streaming._without_number_bounds(_Answer) is _Answer


def test_a_typed_dict_schema_goes_out_as_given() -> None:
    from typing import TypedDict

    class _Typed(TypedDict):
        count: int

    llm = MagicMock()
    llm.__class__ = type(_openai(model="gpt-4.1"))
    streaming._apply_structured_output(llm, _Typed)
    assert llm.with_structured_output.call_args.args[0] is _Typed
