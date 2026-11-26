"""R15-WRAP (round 15: WP-70's wrap-up prompt x WP-66's one token counter, R14-MGW, R14-LOOP).

WP-70 hands the wrap-up a fair share of its room for every gathered result, and sizes that room at three characters a
token. The reserve it draws on (BUDGETEND) was held for ``input_tokens`` as WP-66's counter counts them, and the call
is charged what the provider reports: a run that gathered Chinese, Japanese or Korean pages (a token or more a
character) sent a prompt two to three times its reserve, overspending the reserve the run held for it and, on a small
window, overflowing it. The prompt is now held to the reserve by the same counter the reservation uses, counted on a
worker thread, never the event loop.
"""

from __future__ import annotations

import threading

from harness_core.access.token_count import counter_for
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import MeteredTransport
from harness_core.budget.types import Cap, PinnedBudget, RunAccountRef
from harness_core.budget.wrap_up import WrapUpReserve, WrapUpSize
from harness_core.engines.durable import wrap_up as wrap_up_module
from harness_core.engines.durable.ports import GatheredResult
from harness_core.engines.durable.wrap_up import ModelEngineWrapUp, wrap_up_prompt
from harness_core.kernel.core.responses import TokenUsage
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events

MODEL = "gpt-6-luna"
REQUEST = "调研这些页面并写出 report.md，每条结论注明来源。"
INPUT_TOKENS = 4_000


def _cjk_pages(n: int = 30) -> list[GatheredResult]:
    return [
        GatheredResult("fetch_url", f"第{i}页：" + "知识库检索与引用的研究结果" * 300, f"https://example.cn/{i}")
        for i in range(n)
    ]


def test_r15_wrap_1_a_cjk_wrap_up_prompt_fits_its_reserve_by_the_runs_own_counter() -> None:
    counter = counter_for(MODEL)
    prompt = wrap_up_prompt(REQUEST, _cjk_pages(), input_tokens=INPUT_TOKENS, count=counter.text)
    assert counter.text(prompt) <= INPUT_TOKENS, counter.text(prompt)
    # Still a useful prompt: the newest pages, each with its source.
    assert "https://example.cn/29" in prompt and "Tool results (oldest first)" in prompt


def test_r15_wrap_2_english_results_keep_the_room_they_had() -> None:
    pages = [GatheredResult("fetch_url", f"Fact {i}. " + "plain words " * 400, f"https://e.com/{i}") for i in range(30)]
    counted = wrap_up_prompt(REQUEST, pages, input_tokens=INPUT_TOKENS, count=counter_for(MODEL).text)
    assert counted == wrap_up_prompt(REQUEST, pages, input_tokens=INPUT_TOKENS)


async def test_r15_wrap_3_the_answer_counts_its_prompt_off_the_event_loop(monkeypatch) -> None:
    loop_thread = threading.get_ident()
    threads: list[int] = []
    real = wrap_up_module.wrap_up_prompt

    def recording(*args: object, **kwargs: object) -> str:
        threads.append(threading.get_ident())
        assert kwargs.get("count") is not None, "the prompt is held to the reserve by the run's counter"
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(wrap_up_module, "wrap_up_prompt", recording)
    ledger = InMemoryBudgetLedger(ManualClock())
    run = RunAccountRef("org-1", "engine-r15")
    ledger.pin(run, PinnedBudget(Cap(None, 500_000)))
    reserve = WrapUpReserve(
        accounts=ledger, account=run, pricing_model=MODEL, ids=SeededIds(1), size=WrapUpSize(INPUT_TOKENS, 1_000)
    )
    inner = RecordedTransport()
    inner.events = [reply_events("报告。", usage=TokenUsage(input_tokens=900, output_tokens=40))]
    metered = MeteredTransport(inner, accounts=ledger, account=run, pricing_model=MODEL, ids=SeededIds(2), wrap_up=reserve)
    port = ModelEngineWrapUp(reserve=reserve, transport=metered)
    assert await port.hold()
    answer = await port.answer(REQUEST, _cjk_pages(), "steps")
    assert answer and threads and loop_thread not in threads
