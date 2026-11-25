"""USAGE1 U1-16: the CLI's usage line prices a run from the one price table, cached tokens once."""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.core.responses import RunUsage, TokenUsage

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture
def fmt_usage() -> Iterator[Callable[[RunUsage | None, str], str]]:
    # Importing the CLI silences logging for the process; other tests in this worker read their logs.
    silenced = logging.root.manager.disable
    cli = importlib.import_module("app.agent_loop_lib.cli")
    logging.disable(silenced)
    yield cli._fmt_usage  # pyright: ignore[reportPrivateUsage]
    logging.disable(silenced)


def _run(*calls: TokenUsage) -> RunUsage:
    usage = RunUsage()
    for call in calls:
        usage.add(call)
    return usage


def test_u1_16_an_openai_run_with_cached_tokens_is_priced_once(fmt_usage: Callable[[RunUsage | None, str], str]) -> None:
    # gpt-6-sol: 10,000 fresh x 2.00 + 40,000 read x 0.20 + 3,000 written x 2.50 + 500 out x 10.00 = $0.0405.
    call = TokenUsage(input_tokens=53_000, output_tokens=500, cache_read_tokens=40_000, cache_write_tokens=3_000)
    line = fmt_usage(_run(call), "gpt-6-sol")
    assert "~$0.0405" in line
    assert "53.0k in / 500 out" in line
    assert "cache ✓40.0k ↑3.0k" in line


def test_u1_16_two_calls_cost_what_each_costs(fmt_usage: Callable[[RunUsage | None, str], str]) -> None:
    first = TokenUsage(input_tokens=53_000, output_tokens=500, cache_write_tokens=43_000)
    second = TokenUsage(input_tokens=53_000, output_tokens=500, cache_read_tokens=43_000)
    # (10,000 x 2.00 + 43,000 x 2.50 + 5,000) + (10,000 x 2.00 + 43,000 x 0.20 + 5,000) micro-USD.
    assert "~$0.1661" in fmt_usage(_run(first, second), "gpt-6-sol")


def test_u1_16_no_usage_prints_nothing(fmt_usage: Callable[[RunUsage | None, str], str]) -> None:
    assert fmt_usage(None, "gpt-6-sol") == ""
    assert fmt_usage(RunUsage(), "gpt-6-sol") == ""
