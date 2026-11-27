"""WP81-FU-2: an OpenAI model newer than the installed tiktoken's table counts with o200k, not the dense fallback.

A long research run (Codex on Azure ``gpt-6.1-sol``) had its cut call estimated at 192,368 prompt tokens where the provider's
previous prompt was 149,817 plus one step: about 25% high. Encrypted reasoning items are not counted at all (they go
to the reasoning state, not to messages); the excess is the counter. tiktoken 0.x knows ``gpt-5*`` but not
``gpt-6*``, so the model took ``dense_tokens``, which reads English and code 1.2-1.3 times o200k.
"""

from __future__ import annotations

import pytest

from harness_core.access.token_count import TokenCounter, dense_tokens, encoding_for
from harness_core.kernel.budget.pricing import MODEL_PRICING

_OPENAI = tuple(sorted(m for m in MODEL_PRICING if m.startswith(("gpt-", "o1", "o3", "o4"))))

TRANSCRIPT = (
    "Read `backend/python/harness_core/gateway/model_gateway.py` and summarise how a cut call is settled.\n"
    "def _cut_usage(self, call: _Call[R], produced: int) -> tuple[TokenUsage, TokenUsage]:\n"
    "    prompt = call.input_estimate\n"
    '    return TokenUsage(input_tokens=prompt, output_tokens=best), {"bound": 32_768}\n'
    "The gateway charges the best estimate and keeps the upper bound held until the provider reports.\n"
) * 200


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "azure/gpt-6.1-sol", "openai/gpt-6-luna", "gpt-10", "o9-mini"])
def test_wp81fu2_1_later_openai_families_count_with_o200k(model: str) -> None:
    assert encoding_for(model) == "o200k_base"


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "gemini-2.5-pro", "omni-small", "gpt-4", "gpt-3.5-turbo"])
def test_wp81fu2_2_other_models_keep_their_count(model: str) -> None:
    assert encoding_for(model) == {"gpt-4": "cl100k_base", "gpt-3.5-turbo": "cl100k_base"}.get(model)


@pytest.mark.parametrize("model", _OPENAI)
def test_wp81fu2_3_every_priced_openai_model_has_a_bpe(model: str) -> None:
    """A price-table entry that tiktoken does not know must not silently take the dense fallback again."""
    assert encoding_for(model) is not None


def test_wp81fu2_4_run5s_model_estimates_as_the_provider_counts() -> None:
    counter = TokenCounter("gpt-6.1-sol")
    if counter.method != "o200k_base":
        pytest.skip("no verified local o200k file on this machine (litellm's copy or tiktoken's cache)")
    o200k = counter.text(TRANSCRIPT)
    assert o200k == TokenCounter("gpt-5").text(TRANSCRIPT)
    # What run 5 was charged on: the fallback reads this transcript well above o200k, as 192,368 read above ~150k.
    assert dense_tokens(TRANSCRIPT) / o200k > 1.15
