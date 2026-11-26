"""UX2 EA-11, VC-8: measured on the real durable tool catalog (P2.1's enumerators, as UX1's CL-10 measured it).

EA-11: of the writes an engine can be asked about, how many name a destination the grant can hold to.
VC-8: how many external writes the flow rule asks about in each context, before UX2 (a deep-mode child and an
imported conversation were unknown) and after (the child's inherited label, the import's derived label).
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

import pytest

from app.harness.approvals import flow_guarded
from app.harness.flow_destination import DESTINATION_ARGS
from app.harness.labels import HISTORY_LABELLED_EVENT, SessionSourceLedger
from app.harness.unpinned import flow_rules
from harness_core.effects.flow import FlowRule, external_write
from harness_core.effects.types import allow_all
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.logs import HarnessLog
from harness_core.security.labels import label_of
from tests.unit.harness.test_effect_tags_complete import ENUMERATORS
from tests.unit.harness.test_ux2_ledger import DIGEST, PRIVATE, PUBLIC, event

if TYPE_CHECKING:
    from harness_core.effects.types import ToolEffect
    from harness_core.tools.base import Tool

LOG = HarnessLog.named("tests.ux2.measure")


@pytest.fixture(scope="module")
def tools() -> list[Tool]:
    return [tool for enumerate_ in ENUMERATORS.values() for tool in enumerate_()]


def _argument_names(tool: Tool) -> set[str]:
    schema = tool.raw_input_schema
    if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
        return set(schema["properties"])
    return {p.name for p in tool.parameters}


def test_ea11_how_many_engine_writes_can_be_allowed_for_the_conversation(tools: list[Tool]) -> None:
    writes = [t for t in tools if classify_tool_effect(t.name, t.tags).effect in ("write", "execute")]
    named = [t for t in writes if _argument_names(t) & set(DESTINATION_ARGS)]
    by_arg = Counter(arg for t in named for arg in sorted(_argument_names(t) & set(DESTINATION_ARGS))[:1])
    print(f"\nUX2-EA11 write_or_execute={len(writes)} destination_named={len(named)} by_first_arg={dict(by_arg)}")
    assert 0 < len(named) < len(writes)


def _asks(effects: list[ToolEffect], ledger: SessionSourceLedger, run_id: str) -> int:
    policy = flow_guarded(allow_all, rules=flow_rules(LOG), ledger=ledger, run_id=run_id, log=LOG)
    return sum(1 for effect in effects if policy(ToolCall(id="c", name="t", arguments={}), effect) == "ask")


def _child(inherited: object) -> SessionSourceLedger:
    ledger = SessionSourceLedger(inherited=inherited)  # type: ignore[arg-type]
    ledger.apply([event("run.admitted", "child", stream="child", parentRunId="parent", principalRef="p", runKind="child")])
    return ledger


def _imported(label: dict[str, object] | None) -> SessionSourceLedger:
    ledger = SessionSourceLedger()
    stream = [
        event("run.admitted", "r", principalRef="p", runKind="interactive"),
        event("session.imported", "r", upToMessageSeq=4, transcriptDigest=DIGEST),
    ]
    if label is not None:
        stream.append(event(HISTORY_LABELLED_EVENT, "r", transcriptDigest=DIGEST, label=label))
    ledger.apply(stream)
    return ledger


def test_vc8_external_writes_that_ask_by_context(tools: list[Tool], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV
    from app.utils.env_flags import HARNESS_FLOW_GUARD

    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)
    effects = [classify_tool_effect(t.name, t.tags) for t in tools]
    external = [e for e in effects if external_write(e)]
    unknown = label_of((), complete=False)
    rows = {
        # context: (before UX2, after UX2)
        "deep child, parent read nothing": (_asks(external, _child(None), "child"), _asks(external, _child(PUBLIC), "child")),
        "deep child, parent read a record": (_asks(external, _child(None), "child"), _asks(external, _child(PRIVATE), "child")),
        "deep child, parent used a connector tool": (_asks(external, _child(None), "child"), _asks(external, _child(unknown), "child")),
        "import, user turns only": (_asks(external, _imported(None), "r"), _asks(external, _imported({"complete": True, "sources": []}), "r")),
        "import, durable answers that read a record": (
            _asks(external, _imported(None), "r"),
            _asks(external, _imported({"complete": True, "sources": [{"kind": "indexed", "recordId": "rec-1"}]}), "r"),
        ),
        "import, legacy answers": (_asks(external, _imported(None), "r"), _asks(external, _imported({"complete": False, "sources": []}), "r")),
    }
    print(f"\nUX2-VC8 tools={len(effects)} external_writes={len(external)}")
    for context, (before, after) in rows.items():
        print(f"UX2-VC8 {context}: before={before} after={after}")
    n = len(external)
    assert rows["deep child, parent read nothing"] == (n, 0)
    assert rows["deep child, parent read a record"] == (n, n)
    assert rows["deep child, parent used a connector tool"] == (n, n)
    assert rows["import, user turns only"] == (n, 0)
    assert rows["import, durable answers that read a record"] == (n, n)
    assert rows["import, legacy answers"] == (n, n)
    assert FlowRule("ask").level == "ask"
