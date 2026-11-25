"""WP66-TOK: one input counter for a call's reservation and for its estimate.

An OpenAI-family model is counted with its own BPE, read only from a verified local file; every other model, or one
without the file, takes the dense fallback, which counts code, digits, CJK and emoji densely and is never below the
old four-characters-a-token estimate. No count reaches the network.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from harness_core.access import token_count
from harness_core.access.spend import input_estimate, output_estimate
from harness_core.access.token_count import TokenCounter, dense_tokens, encoding_for
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.core.tokens import count_text_tokens

if TYPE_CHECKING:
    from pathlib import Path

ENGLISH = (
    "The model gateway reserves budget before each call and settles the provider's usage after it. "
    "When a call is cut short, the person is charged a best estimate and the cap keeps the upper bound. "
) * 40
CODE = (
    "def settle(call: Call, usage: TokenUsage | None) -> None:\n"
    "    if usage is None or usage.output_tokens == 0:\n"
    "        raise ValueError(f\"no usage for {call.id!r}\")\n"
    "    ledger[call.key] = {\"in\": usage.input_tokens, \"out\": [1, 2, 3]}\n"
) * 40
CJK = "模型网关为引擎提供模型接口，每次调用前预留预算，调用后按实际用量结算。日本語のテキストも含みます。한국어 문장도 있습니다。" * 20
EMOJI = "Done 🎉 ship it 🚀👍🏽 ok ✅ " * 60
SAMPLES = {"english": ENGLISH, "code": CODE, "cjk": CJK, "emoji": EMOJI}


def _bpe_counts(text: str) -> tuple[int, int]:
    """o200k and cl100k counts, through the module's own verified local load."""
    o200k = TokenCounter("gpt-5")
    cl100k = TokenCounter("gpt-4")
    if o200k.method != "o200k_base" or cl100k.method != "cl100k_base":
        pytest.skip("no verified local BPE file on this machine (litellm's copy or tiktoken's cache)")
    return o200k.text(text), cl100k.text(text)


def test_wp66_tok_1_openai_models_count_with_their_own_bpe() -> None:
    assert encoding_for("gpt-5") == "o200k_base"
    assert encoding_for("openai/gpt-4.1-mini") == "o200k_base"
    assert encoding_for("gpt-oss-120b") == "o200k_base"
    assert encoding_for("gpt-4") == "cl100k_base"
    assert encoding_for("claude-sonnet-4-6") is None
    assert encoding_for("gemini-2.5-pro") is None
    assert encoding_for(None) is None
    o200k, _ = _bpe_counts("hello world")
    assert o200k == 2


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_wp66_tok_2_the_fallback_is_never_below_the_old_estimate(name: str) -> None:
    text = SAMPLES[name]
    assert dense_tokens(text) >= count_text_tokens(text)
    assert TokenCounter("claude-sonnet-4-6").text(text) == dense_tokens(text)


@pytest.mark.parametrize("name", ["english", "code", "cjk"])
def test_wp66_tok_3_the_fallback_is_at_or_above_both_bpes_where_the_old_one_undercounted(name: str) -> None:
    """The old estimate counted CJK at a quarter of a token a character and code at a token per four; the fallback
    is at least what either OpenAI BPE says, and on Latin text no more than half again."""
    text = SAMPLES[name]
    o200k, cl100k = _bpe_counts(text)
    dense = dense_tokens(text)
    assert dense >= max(o200k, cl100k)
    if name != "cjk":
        assert dense <= 1.5 * max(o200k, cl100k)


def test_wp66_tok_4_emoji_and_cjk_count_densely() -> None:
    o200k, cl100k = _bpe_counts(EMOJI)
    assert dense_tokens(EMOJI) >= o200k
    assert dense_tokens(EMOJI) >= 0.7 * cl100k
    assert dense_tokens("🎉") >= 2
    assert dense_tokens("模型") >= 2
    assert count_text_tokens(CJK) < dense_tokens(CJK) / 3


def test_wp66_tok_5_reservation_and_estimate_share_one_counter() -> None:
    message = UserMessage(content=CODE)
    for model in ("gpt-5", "claude-sonnet-4-6", None):
        counter = TokenCounter(model)
        assert input_estimate([message], None, [], model) == counter.message(message)
        assert output_estimate(CODE, model) == counter.text(CODE)


def test_wp66_tok_6_no_file_no_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without a verified local BPE an OpenAI model falls back; a planted file with the wrong digest is not read."""
    import socket

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a token count reached the network")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    (tmp_path / "fb374d419588a4632f3f557e76b4b70aebbca790").write_bytes(b"aGVsbG8= 0\n")
    monkeypatch.setattr(token_count, "_bpe_dirs", lambda: [tmp_path])
    assert token_count._load("o200k_base") is None  # pyright: ignore[reportPrivateUsage]
    fresh = token_count._Bpe("o200k_base")  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setitem(token_count._BPES, "o200k_base", fresh)  # pyright: ignore[reportPrivateUsage]
    counter = TokenCounter("gpt-5")
    assert counter.method == "dense"
    assert counter.text(CODE) == dense_tokens(CODE)


def test_wp66_tok_7_a_resent_history_costs_only_its_new_turn() -> None:
    """The overhead a laptop pays: a long history counted once, then each resend with a turn appended."""
    counter = TokenCounter("gpt-5")
    if counter.method == "dense":
        pytest.skip("no verified local BPE file on this machine")
    history = [UserMessage(content=f"{ENGLISH} turn {i}") for i in range(60)]
    started = time.perf_counter()
    first = sum(counter.message(m) for m in history)
    cold = time.perf_counter() - started
    started = time.perf_counter()
    again = sum(counter.message(m) for m in [*history, UserMessage(content="one more")])
    warm = time.perf_counter() - started
    assert again > first
    assert warm < max(cold, 0.002)
