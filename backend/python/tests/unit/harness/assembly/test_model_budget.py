"""P6.5 WM-1..WM-3: the worker's run model charges the ledger; a refusal ends the run, an outage yields."""

from __future__ import annotations

from typing import Any

import pytest

from app.harness.assembly.model import (
    LEDGER_BACKOFF_S,
    ClassifiedModelPort,
    ResolvedModel,
    metered,
)
from app.utils.env_flags import AGENT_HARNESS_BUDGET_LEDGER
from harness_core.budget.errors import BudgetExhausted, BudgetUnavailable
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.metered import LedgerChildBudget, MeteredTransport
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.kernel.durable.ports import ModelBackoff, ModelFailed
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport

RUN = RunAccountRef("org-1", "run-1")


def _model(transport: Any = None) -> ResolvedModel:  # noqa: ANN401
    return ResolvedModel(
        raw_llm=None,
        provider="openAI",
        is_multimodal=False,
        context_length=None,
        is_reasoning=False,
        port=ClassifiedModelPort(_Raising(None)),
        transport=transport,
        transport_name="direct",
    )


class _Raising:
    def __init__(self, error: Exception | None) -> None:
        self.error = error

    async def sample(self, call: Any, on_delta: Any) -> Any:  # noqa: ANN401
        assert self.error is not None
        raise self.error

    def restore(self, raw: bytes, *, provider: str, transport: str) -> None:
        return None


async def test_wm1_the_runs_transport_is_metered_and_deep_mode_gets_the_ledgers_child_budget() -> None:
    ledger = InMemoryBudgetLedger(ManualClock())
    await ledger.open_root(RUN, Cap(None, 1_000))
    model, child_budget = metered(_model(RecordedTransport()), accounts=ledger, account=RUN, pricing_model="m", ids=SeededIds(0))
    assert isinstance(model.transport, MeteredTransport)
    assert isinstance(child_budget, LedgerChildBudget)
    assert model.transport_name == "direct"


@pytest.mark.parametrize("why", ["no ledger route", "no transport", "ledger off"])
async def test_wm2_unchanged_without_a_ledger_route_a_transport_or_when_off(
    why: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = InMemoryBudgetLedger(ManualClock())
    original = _model(None if why == "no transport" else RecordedTransport())
    if why == "ledger off":
        monkeypatch.setenv(AGENT_HARNESS_BUDGET_LEDGER, "off")
    model, child_budget = metered(
        original, accounts=None if why == "no ledger route" else ledger, account=RUN, pricing_model="m", ids=SeededIds(0)
    )
    assert (model, child_budget) == (original, None)


async def test_wm3_a_refusal_fails_the_run_named_for_it_and_an_outage_backs_off() -> None:
    with pytest.raises(ModelFailed) as failed:
        await ClassifiedModelPort(_Raising(BudgetExhausted("spent"))).sample(None, None)  # type: ignore[arg-type]
    assert failed.value.error_type == "budget_exhausted"
    with pytest.raises(ModelBackoff) as backoff:
        await ClassifiedModelPort(_Raising(BudgetUnavailable("down"))).sample(None, None)  # type: ignore[arg-type]
    assert backoff.value.retry_after_s == LEDGER_BACKOFF_S
