"""What a run of the golden evals cost, in dollars.

The agent runtime counts tokens for every model call in a run
(``AgentResult.usage``), so the arithmetic here is tokens × a published price.
Providers do not return a price, so the table below is copied from their public
pages and carries the date it was read: a stale table gives a wrong number
quietly, and a dated one at least says when it was last true.

An unknown model is not priced at zero — that would report a free run. The cost
is reported as unknown instead, and the summary says which model needs a price.
"""

from __future__ import annotations

from dataclasses import dataclass

# Read from the providers' own pricing pages on 2026-09-20 (Anthropic:
# platform.claude.com/docs/en/about-claude/pricing). Dollars per million
# tokens. Update the date whenever you touch a number.
PRICES_AS_OF = "2026-09-20"

PRICES_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    # model key: (input, output)
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o4-mini": (1.10, 4.40),
    # Anthropic's pricing page, read 2026-09-20: Sonnet 5 is $2/$10. The
    # $3/$15 that was here is Sonnet 4.5's and 4.6's price, which would have
    # overstated a Sonnet 5 run by half.
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


@dataclass(frozen=True)
class RunCost:
    """What one eval run cost, or why that is not known."""

    model: str
    input_tokens: int
    output_tokens: int
    usd: float | None
    note: str

    @property
    def known(self) -> bool:
        return self.usd is not None

    def render(self) -> str:
        if self.usd is None:
            return f"Cost: not known — {self.note}"
        return (
            f"Cost: about ${self.usd:.2f} "
            f"({self.input_tokens:,} tokens in, {self.output_tokens:,} out, "
            f"prices as of {PRICES_AS_OF})"
        )


def price_for(model: str) -> tuple[float, float] | None:
    """Input and output price per million tokens, or None when unlisted."""
    if model in PRICES_PER_MILLION_TOKENS:
        return PRICES_PER_MILLION_TOKENS[model]
    # Providers version model names ("gpt-4o-mini-2024-07-18"); match the
    # longest listed prefix so a dated snapshot still prices.
    candidates = [key for key in PRICES_PER_MILLION_TOKENS if model.startswith(key)]
    if not candidates:
        return None
    return PRICES_PER_MILLION_TOKENS[max(candidates, key=len)]


def run_cost(model: str, input_tokens: int, output_tokens: int) -> RunCost:
    """Dollars for one run's token counts."""
    prices = price_for(model)
    if prices is None:
        return RunCost(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd=None,
            note=(
                f"no price listed for '{model}'. Add it to "
                "PRICES_PER_MILLION_TOKENS in tests/evals/cost.py."
            ),
        )
    if input_tokens == 0 and output_tokens == 0:
        return RunCost(
            model=model,
            input_tokens=0,
            output_tokens=0,
            usd=None,
            note=(
                "the provider reported no token usage, so there is nothing to "
                "price. Check that the run actually called the model."
            ),
        )
    in_price, out_price = prices
    usd = (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return RunCost(model, input_tokens, output_tokens, usd, "")


def monthly_projection(nightly_usd: float, weekly_usd: float) -> float:
    """Dollars a month for a nightly run plus a weekly one.

    30.4 nights and 4.3 weeks: the average month, not a 28-day one, so the
    figure does not read low.
    """
    return nightly_usd * 30.4 + weekly_usd * 4.3


def render_projection(nightly_usd: float, weekly_usd: float) -> str:
    monthly = monthly_projection(nightly_usd, weekly_usd)
    return (
        f"At about ${nightly_usd:.2f} a night and ${weekly_usd:.2f} a week, "
        f"this schedule costs roughly ${monthly:.2f} a month."
    )


__all__ = [
    "PRICES_AS_OF",
    "PRICES_PER_MILLION_TOKENS",
    "RunCost",
    "monthly_projection",
    "price_for",
    "render_projection",
    "run_cost",
]
