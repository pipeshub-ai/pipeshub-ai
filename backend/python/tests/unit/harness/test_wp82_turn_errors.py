"""WP82-MCP-1, WP82-WALL-3: what ends a turn reaches the kernel as itself, and a turn learns its run's deadline.

In a long research run, the refused save at the child's turn end left the turn inside the Tool Gateway's MCP task group, which
wrapped it in an ``ExceptionGroup``; the kernel's typed handlers (a lost lease, a refused commit, a store outage) never
saw it, logged ``engine.kernel.error errorType=ExceptionGroup`` and released the attempt.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.harness.gateway.mcp import ToolGatewayApp
from harness_core.budget.run_budget import RunBudget
from harness_core.journal.types import OwnershipLost


class _Refused(Exception):
    pass


@pytest.mark.parametrize("error", [_Refused("BAD_REQUEST"), OwnershipLost("STALE_ATTEMPT")])
async def test_wp82_mcp_1_an_error_inside_a_mounted_turn_keeps_its_type(error: Exception) -> None:
    app = ToolGatewayApp(verifier=object())  # type: ignore[arg-type]
    with pytest.raises(type(error)) as raised:
        async with app.serving(object(), object()):  # type: ignore[arg-type]
            raise error
    assert raised.value is error
    assert app._mounts == {}  # pyright: ignore[reportPrivateUsage]


async def test_wp82_mcp_1_a_turn_that_ends_well_unmounts() -> None:
    app = ToolGatewayApp(verifier=object())  # type: ignore[arg-type]
    async with app.serving(object(), object()) as key:  # type: ignore[arg-type]
        assert key in app._mounts  # pyright: ignore[reportPrivateUsage]
    assert app._mounts == {}  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(("deadline_at_ms", "left"), [(None, None), (1_800_000 + 23 * 60_000, 23 * 60.0)])
def test_wp82_wall_3_the_turn_is_given_what_its_runs_deadline_leaves(
    deadline_at_ms: int | None, left: float | None
) -> None:
    """The claim's deadline is what the run budget counts from (ADR-RUN-01 D4: ``RunBudget.of_claim``)."""
    budgets = SimpleNamespace(max_cost_usd=5.0, max_tokens=1_000, calendar_deadline="not a time")
    claim = SimpleNamespace(deadline_at_ms=deadline_at_ms, depth=0, spec=SimpleNamespace(budgets=budgets))
    assert RunBudget.of_claim(claim).left_s(1_800.0) == left  # type: ignore[arg-type]
