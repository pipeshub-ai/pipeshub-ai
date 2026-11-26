"""MA1 QA37-I6: a child on its role's model is charged that model's price, its parent its own.

The worker meters a run's transport at the model its run context names (``model_name``): the pinned spec's
configured model. A role-assigned child's spec names its role's model, so its calls commit at that price into its
own account, and its parent's account holds both. Expected amounts are worked from the price rows by hand.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.harness.assembly.context import RunContext
from app.harness.assembly.loop import model_name
from app.harness.assembly.model import ClassifiedModelPort, ResolvedModel, metered
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.types import Cap, RunAccountRef
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal.children import ChildRequest
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.core.responses import TokenUsage
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_gateway import RecordedTransport, reply_events

from . import world as w

if TYPE_CHECKING:
    from harness_core.journal.types import Claim

# gpt-6-sol: 2.00 in, 10.00 out per 1M tokens. claude-haiku-4-5: 1.00 in, 5.00 out.
A = ("openai", "gpt-6-sol")
B = ("anthropic", "claude-haiku-4-5")
USAGE = TokenUsage(input_tokens=1_000, output_tokens=100)
A_MICROS = 3_000
B_MICROS = 1_500


def _parent_spec() -> RunSpec:
    wire: dict[str, Any] = dump_contract(w.pinned_spec(tool_ids=[]))  # type: ignore[assignment]
    wire["model"]["policy"] = [{"provider": A[0], "model": A[1]}]
    wire["extensions"]["x.pipeshub"]["model"] = {"modelKey": "mk-a", "modelName": A[1]}
    wire["budgets"]["maxChildren"] = 2
    return validate_contract(RunSpec, wire)


class _Unused:
    async def sample(self, call: Any, on_delta: Any) -> Any:  # noqa: ANN401
        raise AssertionError("the transport is called directly")

    def restore(self, raw: bytes, *, provider: str, transport: str) -> None:
        return None


class _Metered:
    """A run's model as the worker meters it for one attempt."""

    def __init__(self, ledger: InMemoryBudgetLedger, claim: Claim) -> None:
        self.run = RunContext.from_claim(w.node_claim(claim))
        inner = RecordedTransport([reply_events("ok", usage=USAGE) for _ in range(4)], family=self.run.model.provider)
        model = ResolvedModel(
            raw_llm=None, provider=self.run.model.provider, is_multimodal=False, context_length=None, is_reasoning=False,
            port=ClassifiedModelPort(_Unused()), transport=inner, transport_name="direct",
        )
        model, _ = metered(
            model, accounts=ledger, account=RunAccountRef(self.run.org_id, self.run.run_id),
            pricing_model=model_name(self.run), ids=SeededIds(0),
        )
        assert model.transport is not None
        self._transport = model.transport

    async def call(self) -> None:
        _ = await self._transport.complete([UserMessage(content="hi")])


async def _spawned(role_routes: dict[str, str] | None) -> tuple[InMemoryBudgetLedger, Claim, Claim]:
    journal = w.pipeshub_journal(clock=ManualClock(), configured_models={":".join(B): "mk-b"})
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=_parent_spec(), input_text="go", role_routes=role_routes)
    parent = await w.claim_next(journal, "run-1")
    admitted = await journal.spawn(
        parent.token, ChildRequest("t1.0", "look it up", definition={"role": "researcher", "toolNames": []})
    )
    child = await w.claim_next(journal, admitted.child_run_id)
    ledger = InMemoryBudgetLedger(ManualClock())
    root = RunAccountRef(w.ORG, "run-1")
    _ = await ledger.open_root(root, Cap(None, None))
    _ = await ledger.open_child(root, RunAccountRef(w.ORG, child.run_id), Cap(None, None))
    return ledger, parent, child


async def test_qa37_i6_the_ledger_commits_the_childs_calls_at_its_roles_price_and_the_parents_at_its_own() -> None:
    ledger, parent, child = await _spawned({"researcher": ":".join(B)})

    parent_model, child_model = _Metered(ledger, parent), _Metered(ledger, child)
    await parent_model.call()
    await child_model.call()
    await child_model.call()
    await parent_model.call()

    assert model_name(child_model.run) == B[1]
    assert (await ledger.remaining(RunAccountRef(w.ORG, child.run_id))).committed.usd_micros == 2 * B_MICROS
    assert (await ledger.remaining(RunAccountRef(w.ORG, "run-1"))).committed.usd_micros == 2 * A_MICROS + 2 * B_MICROS


async def test_qa37_i6_a_child_without_a_role_model_is_charged_its_parents_price_as_before() -> None:
    ledger, parent, child = await _spawned(None)

    parent_model, child_model = _Metered(ledger, parent), _Metered(ledger, child)
    await parent_model.call()
    await child_model.call()

    assert model_name(child_model.run) == A[1]
    assert (await ledger.remaining(RunAccountRef(w.ORG, child.run_id))).committed.usd_micros == A_MICROS
    assert (await ledger.remaining(RunAccountRef(w.ORG, "run-1"))).committed.usd_micros == 2 * A_MICROS
