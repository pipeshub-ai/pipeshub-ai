"""USAGE1 U1-8, U1-10..U1-12, U1-18: a journaled usage is read as the whole prompt, whenever it was written.

Before USAGE1 a ``model.response`` carried its reporter's own count: Anthropic and Bedrock left cached tokens
out of ``inputTokens``, the OpenAI family, Gemini and LangChain counted them in. Such an event has no
``inputIncludesCached``; its ``provider`` and ``transport`` say which it was. An event written since carries the
marker whenever it has cached tokens, and is read as it is.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness_core.journal.types import Checkpoint, JournalEvent
from harness_core.kernel.durable.context_compiler import latest_measurement
from harness_core.kernel.fold import (
    checkpoint_is_valid,
    fold_run,
    fold_session,
    make_checkpoint,
    restore_checkpoint,
)

RUN, SESSION = "run1", "ses1"
DIGEST = "sha256:" + "a" * 64
# One request: 1,000 fresh prompt tokens, 4,000 cached reads, 300 cache writes.
READ, WRITTEN, PROMPT = 4_000, 300, 5_300


def _response(provider: str, transport: str, input_tokens: int, *, marked: bool = False, seq: int = 1) -> JournalEvent:
    usage: dict[str, Any] = {
        "inputTokens": input_tokens,
        "outputTokens": 50,
        "cacheReadTokens": READ,
        "cacheWriteTokens": WRITTEN,
    }
    if marked:
        usage["inputIncludesCached"] = True
    return JournalEvent(
        org_id="org1",
        session_id=SESSION,
        stream_id=SESSION,
        seq=seq,
        run_id=RUN,
        type="model.response",
        critical=True,
        ts="2026-10-05T00:00:00Z",
        writer="worker",
        data={
            "messageRef": {"ref": "cas/m1", "hash": DIGEST, "size": 10},
            "toolCalls": [],
            "usage": usage,
            "provider": provider,
            "transport": transport,
        },
    )


def _measured(event: JournalEvent) -> int:
    measured = latest_measurement(fold_session([event], stream_id=SESSION))
    assert measured is not None
    return measured.tokens


@pytest.mark.parametrize(
    ("provider", "transport", "journaled_input"),
    [
        # U1-10: reporters that counted reads and writes apart.
        ("anthropic", "direct", 1_000),
        ("bedrock", "direct", 1_000),
        ("scripted", "scripted", 1_000),
        # U1-11: reporters that already counted the whole prompt.
        ("openai", "direct", PROMPT),
        ("azure-direct", "direct", PROMPT),
        ("gemini", "direct", PROMPT),
        ("anthropic", "langchain", PROMPT),
        ("azureopenai", "langchain", PROMPT),
    ],
)
def test_u1_10_u1_11_an_unmarked_usage_is_read_as_its_reporter_counted(
    provider: str, transport: str, journaled_input: int
) -> None:
    event = _response(provider, transport, journaled_input)
    assert _measured(event) == PROMPT
    run = fold_run([event], run_id=RUN)
    [record] = run.responses
    assert record.usage is not None
    assert (record.usage.input_tokens, record.usage.cache_read_tokens, record.usage.cache_write_tokens) == (
        PROMPT,
        READ,
        WRITTEN,
    )
    assert run.consumed.input_tokens == PROMPT


@pytest.mark.parametrize("provider", ["anthropic", "bedrock", "openai", "gemini", "scripted"])
def test_u1_12_a_marked_usage_is_read_as_it_is_whatever_its_provider(provider: str) -> None:
    assert _measured(_response(provider, "direct", PROMPT, marked=True)) == PROMPT


def test_u1_8_the_request_measures_its_input_tokens_once() -> None:
    # A marked usage whose input is smaller than its cached parts is still read as written, not grown.
    assert _measured(_response("anthropic", "direct", 4_500, marked=True)) == 4_500


def test_u1_18_a_checkpoint_folded_with_reporter_counts_is_refolded() -> None:
    events = [_response("anthropic", "direct", 1_000)]
    run = fold_run(events, run_id=RUN)
    session = fold_session(events, stream_id=SESSION)
    current = make_checkpoint(run, session)
    assert checkpoint_is_valid(current, run_id=RUN, head_seq=1, ref="c", referenced_ref="c")
    assert restore_checkpoint(current)[1] == session

    older = dict(current.state["session"])  # type: ignore[arg-type]
    del older["usage_includes_cached"]
    stale = Checkpoint(
        run_id=current.run_id,
        stream_id=current.stream_id,
        fold_version=current.fold_version,
        seq=current.seq,
        state={**current.state, "session": older},
    )
    assert not checkpoint_is_valid(stale, run_id=RUN, head_seq=1, ref="c", referenced_ref="c")
