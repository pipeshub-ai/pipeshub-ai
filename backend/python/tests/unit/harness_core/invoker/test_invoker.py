"""The Invoker over both catalogs and both effect logs (IV-1..IV-14)."""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import TYPE_CHECKING, override

import pytest

from harness_core.effects import (
    EffectCall,
    EffectKey,
    EffectResult,
    EffectRunner,
    EffectUnknown,
    RequestEffectLog,
    ToolExecution,
    args_digest,
)
from harness_core.invoker import (
    CatalogTool,
    InMemoryToolCatalog,
    InvocationScope,
    Invoker,
    ToolRefusal,
    idempotency_key,
)
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.testing.contracts.effect_log import CountingTool
from harness_core.testing.contracts.tool_catalog import (
    CREATE,
    DELETE,
    GRANT,
    LOOKUP,
    TOOLS,
    call,
)
from harness_core.testing.ids import SeededIds
from harness_core.tools.durable_port import RegistryToolPort
from tests.unit.harness_core.effects.test_effect_log_contract import RUN, JournalHarness
from tests.unit.harness_core.invoker.catalogs import MemoryCatalogs, RegistryCatalogs

if TYPE_CHECKING:
    from harness_core.effects import ApprovalDecision, ToolEffect
    from harness_core.invoker import InvokeResult, ToolCatalog
    from harness_core.kernel.core.messages import ToolCall

SCOPE = InvocationScope(RUN, "org-1", "run-1.a1")

Catalogs = MemoryCatalogs | RegistryCatalogs


@pytest.fixture(params=[MemoryCatalogs, RegistryCatalogs], ids=["memory", "registry"])
def catalogs(request: pytest.FixtureRequest) -> Catalogs:
    return request.param()


class OrderedLog(RequestEffectLog):
    """Records ``intent``/``outcome`` into the same list the tools record their runs in."""

    def __init__(self, steps: list[str]) -> None:
        super().__init__()
        self.steps = steps

    @override
    async def intent(self, call: EffectCall) -> None:
        self.steps.append("intent")
        await super().intent(call)

    @override
    async def outcome(self, call: EffectCall, execution: ToolExecution) -> None:
        self.steps.append("outcome")
        await super().outcome(call, execution)


def invoker(
    catalog: ToolCatalog,
    log: RequestEffectLog | None = None,
    approvals: ApprovalDecision = "allow",
) -> Invoker:
    def policy(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return approvals

    runner = EffectRunner(log or RequestEffectLog(), policy)
    return Invoker(catalog, runner, SCOPE, ids=SeededIds())


def executed(result: InvokeResult) -> EffectResult:
    assert isinstance(result, EffectResult), result
    return result


async def test_iv1_a_granted_read_executes_and_is_recorded(catalogs: Catalogs) -> None:
    log = RequestEffectLog()
    result = executed(await invoker(catalogs.build(TOOLS, GRANT), log).invoke(call("lookup")))
    assert (result.disposition, result.invocation_id) == ("executed", "inv_000001")
    assert result.execution is not None and result.execution.content == "lookup:x"
    found = await log.find(EffectKey(RUN, LOOKUP.effect.tool_id, args_digest({"q": "x"})))
    assert found is not None and (found.state, found.invocation_id) == ("completed", "inv_000001")


async def test_iv2_a_write_records_its_intent_before_it_runs(catalogs: Catalogs) -> None:
    catalog = catalogs.build(TOOLS, GRANT)
    result = executed(await invoker(catalog, OrderedLog(catalogs.runs)).invoke(call("create")))
    assert result.disposition == "executed"
    assert catalogs.runs == ["intent", "create", "outcome"]


@pytest.mark.parametrize("name", ["delete", "ghost"], ids=["iv3-withheld", "iv4-unknown"])
async def test_iv3_iv4_an_ungranted_call_is_refused_and_never_runs(
    catalogs: Catalogs, name: str, caplog: pytest.LogCaptureFixture
) -> None:
    log = RequestEffectLog()
    with caplog.at_level(logging.INFO):
        result = await invoker(catalogs.build(TOOLS, GRANT), log).invoke(call(name, "secret-arg"))
    assert isinstance(result, ToolRefusal)
    assert (result.reason, result.invocation_id, result.tool_name) == ("not_granted", "inv_000001", name)
    assert catalogs.runs == []
    assert await log.find(EffectKey(RUN, DELETE.effect.tool_id, args_digest({"q": "secret-arg"}))) is None
    refused = [r for r in caplog.records if r.__dict__.get("harness_event") == "tool.refused"]
    assert [r.levelno for r in refused] == [logging.WARNING]
    fields = refused[0].__dict__["harness"]
    assert (fields["reason"], fields["invocationId"], fields["runId"]) == ("not_granted", "inv_000001", RUN)
    assert all("secret-arg" not in r.getMessage() for r in caplog.records)
    assert [r.__dict__.get("harness_event") for r in caplog.records if r.name.startswith("harness_core")] == [
        "tool.refused"
    ]


async def test_iv5_an_identical_recall_of_a_write_answers_from_the_log(catalogs: Catalogs) -> None:
    subject = invoker(catalogs.build(TOOLS, GRANT))
    first = executed(await subject.invoke(call("create", "a")))
    again = executed(await subject.invoke(call("create", "a")))
    assert (first.disposition, again.disposition) == ("executed", "recorded")
    assert again.invocation_id == first.invocation_id == "inv_000001"
    assert again.execution is not None and again.execution.content == "create:a"
    assert catalogs.runs == ["create"]


@pytest.mark.parametrize(("policy", "disposition"), [("deny", "denied"), ("ask", "held")])
async def test_iv6_policy_deny_and_ask_do_not_run(
    catalogs: Catalogs, policy: ApprovalDecision, disposition: str
) -> None:
    log = RequestEffectLog()
    result = executed(await invoker(catalogs.build(TOOLS, GRANT), log, policy).invoke(call("create")))
    assert result.disposition == disposition
    assert catalogs.runs == []
    assert await log.find(EffectKey(RUN, CREATE.effect.tool_id, args_digest({"q": "x"}))) is None


def counting_catalog(tool: CountingTool) -> InMemoryToolCatalog:
    schema = ToolSchema(name="create", description="create", input_schema={"type": "object"})
    return InMemoryToolCatalog([CatalogTool(schema, CREATE.effect, tool)], [CREATE.effect.tool_id])


async def test_iv7_an_unknown_effect_is_never_run_again() -> None:
    """Memory catalog only: ``RegistryToolPort`` turns every exception into an error outcome."""
    tool = CountingTool([EffectUnknown("ack lost")])
    subject = invoker(counting_catalog(tool))
    first = executed(await subject.invoke(call("create")))
    again = executed(await subject.invoke(call("create")))
    assert (first.disposition, again.disposition) == ("unknown", "unknown")
    assert again.invocation_id == first.invocation_id
    assert tool.calls == 1


async def test_iv8_another_invocations_dangling_intent_is_in_flight() -> None:
    log, tool = RequestEffectLog(), CountingTool()
    subject = invoker(counting_catalog(tool), log)
    created = call("create")
    await log.intent(subject.effect_call(created, CREATE.effect, "other-inv"))
    result = executed(await subject.invoke(created))
    assert (result.disposition, result.invocation_id) == ("in_flight", "other-inv")
    assert tool.calls == 0


async def test_iv9_invocation_ids_come_from_the_injected_source(catalogs: Catalogs) -> None:
    ids = SeededIds()
    subject = Invoker(catalogs.build(TOOLS, GRANT), EffectRunner(RequestEffectLog()), SCOPE, ids=ids)
    first = executed(await subject.invoke(call("lookup", "1")))
    given = executed(await subject.invoke(call("lookup", "2"), invocation_id="t3.0"))
    second = executed(await subject.invoke(call("lookup", "3")))
    assert [first.invocation_id, given.invocation_id, second.invocation_id] == [
        "inv_000001",
        "t3.0",
        "inv_000002",
    ]
    without = Invoker(catalogs.build(TOOLS, GRANT), EffectRunner(RequestEffectLog()), SCOPE)
    with pytest.raises(ValueError, match="no id source"):
        _ = await without.invoke(call("lookup"))
    assert catalogs.runs == ["lookup", "lookup", "lookup"]


@pytest.mark.parametrize(
    ("scope", "invocation", "turn_text", "key"),
    [
        (InvocationScope("run-1", "org-1", "run-1.a1"), "t1.0", "", "org-1:run-1:t1.0"),
        (InvocationScope("r", "o", "r.a3"), "inv_000007", "said", "o:r:inv_000007"),
    ],
)
def test_iv10_the_tool_context_is_a_pure_derivation(
    scope: InvocationScope, invocation: str, turn_text: str, key: str
) -> None:
    subject = Invoker(InMemoryToolCatalog([], []), EffectRunner(RequestEffectLog()), scope)
    ctx = subject.context(invocation, turn_text)
    assert (ctx.run_id, ctx.org_id, ctx.invocation_id, ctx.attempt_id, ctx.turn_text) == (
        scope.run_id,
        scope.org_id,
        invocation,
        scope.attempt_id,
        turn_text,
    )
    assert ctx.idempotency_key == key == idempotency_key(scope.org_id, scope.run_id, invocation)


async def test_iv11_the_catalog_classifies_each_call() -> None:
    """A read-classified call of a write tool runs as a read: no intent first, never deduped."""
    built = RegistryCatalogs()
    port = built.build(TOOLS, GRANT)
    reclassified = RegistryToolPort(
        port._registry,  # pyright: ignore[reportPrivateUsage]
        {"create": CREATE.effect, "lookup": LOOKUP.effect},
        call_effects={"create": lambda args: "read" if args.get("q") == "select" else "write"},
    )
    log = OrderedLog(built.runs)
    subject = invoker(reclassified, log)
    reads = [executed(await subject.invoke(call("create", "select"))) for _ in range(2)]
    assert [r.disposition for r in reads] == ["executed", "executed"]
    assert built.runs == ["intent", "create", "outcome", "intent", "create", "outcome"]
    built.runs.clear()
    writes = [executed(await subject.invoke(call("create", "insert"))) for _ in range(2)]
    assert [w.disposition for w in writes] == ["executed", "recorded"]
    assert built.runs == ["intent", "create", "outcome"]


def test_iv12_granted_is_the_catalogs_grant(catalogs: Catalogs) -> None:
    subject = invoker(catalogs.build(TOOLS, GRANT))
    assert sorted(s.name for s in subject.granted()) == ["create", "lookup"]
    assert invoker(catalogs.build(TOOLS, ())).granted() == []


async def test_iv13_over_the_journal_with_the_folds_invocation_ids() -> None:
    harness = JournalHarness()
    await harness.start()
    catalogs = MemoryCatalogs()
    subject = Invoker(catalogs.build(TOOLS, GRANT), EffectRunner(harness.log), SCOPE)
    first_turn = await harness.new_call(CREATE.effect, {"q": "a"})
    first = executed(await subject.invoke(call("create", "a"), invocation_id=first_turn.invocation_id))
    assert (first.disposition, first.invocation_id) == ("executed", "t1.0")
    assert [kinds for _, kinds in harness.journal.commits(RUN)[-2:]] == [["tool.intent"], ["tool.outcome"]]
    second_turn = await harness.new_call(CREATE.effect, {"q": "a"})
    again = executed(await subject.invoke(call("create", "a"), invocation_id=second_turn.invocation_id))
    assert (again.disposition, again.invocation_id) == ("recorded", "t1.0")
    assert catalogs.runs == ["create"]


def test_iv14_the_invoker_imports_alone() -> None:
    code = (
        "import sys, harness_core.invoker, harness_core.testing.contracts.tool_catalog\n"
        "bad = sorted(m for m in sys.modules if m == 'app' or m.startswith('app.')"
        " or m.startswith('harness_core.kernel.durable'))\n"
        "assert not bad, bad\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
