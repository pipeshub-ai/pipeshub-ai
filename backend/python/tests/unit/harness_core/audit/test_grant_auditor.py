"""P9 AU-1..AU-4: the grant auditor's check, its log parsing, the effect log, and RE-* on the in-memory source;
C9.01 (EV-10): its usage reconciliation of ended runs against their ledger account."""

from __future__ import annotations

import json
import logging

import pytest

from harness_core.audit import (
    ExecutedCall,
    GrantAuditor,
    InMemoryRunEvidenceSource,
    JournalUsageRecord,
    MalformedLine,
    parse_call,
)
from harness_core.budget.types import ZERO, Amount, LedgerUsage
from harness_core.testing.contracts.audit import (
    EvidenceEnv,
    RunEvidenceSourceContract,
    SeededRun,
)

ORG, RUN = "org-1", "run-1"
CREATE, LOOKUP, DELETE = "crm@inst-1/create", "crm@inst-1/lookup", "crm@inst-1/delete"
ROUTE = "openai:gpt-x"


class _MemoryEnv:
    async def run(self, *, tools: tuple[str, ...], intents: tuple[str, ...]) -> SeededRun:
        source = InMemoryRunEvidenceSource()
        source.pin(ORG, RUN, tools=tools, routes=(ROUTE,))
        for n, tool in enumerate(intents):
            source.intent(ORG, RUN, tool, invocation_id=f"inv-{n}", attempt_id="a1")
        return SeededRun(source, ORG, RUN, frozenset({ROUTE}))


class TestInMemoryRunEvidenceSource(RunEvidenceSourceContract):
    @pytest.fixture
    def evidence_env(self) -> EvidenceEnv:
        return _MemoryEnv()


def pinned() -> InMemoryRunEvidenceSource:
    source = InMemoryRunEvidenceSource()
    source.pin(ORG, RUN, tools=(CREATE, LOOKUP), routes=(ROUTE,))
    return source


def tool(subject: str, invocation: str = "inv-1", run: str = RUN) -> ExecutedCall:
    return ExecutedCall("tool_gateway", ORG, run, subject, "a1", invocation)


async def test_au1_calls_inside_the_grant_are_clean() -> None:
    auditor = GrantAuditor(pinned(), outside_grant=lambda t: t.startswith("pipeshub@"))
    report = await auditor.audit(
        [tool(CREATE), tool(LOOKUP, "inv-2"), tool("pipeshub@builtin/fetch_url", "inv-3"),
         ExecutedCall("model_gateway", ORG, RUN, ROUTE, "a1")]
    )
    assert report.violations == () and report.calls == 4 and report.runs == 1


async def test_au2_ungranted_tool_route_and_unknown_run_are_violations(caplog: pytest.LogCaptureFixture) -> None:
    auditor = GrantAuditor(pinned())
    with caplog.at_level(logging.INFO):
        report = await auditor.audit(
            [tool(DELETE), ExecutedCall("model_gateway", ORG, RUN, "anthropic:claude-x", "a1"),
             tool(CREATE, run="run-unknown")]
        )
    assert sorted(v.code for v in report.violations) == ["route_not_granted", "run_unknown", "tool_not_granted"]
    lines = [r for r in caplog.records if getattr(r, "harness_event", "") == "audit.violation"]
    assert len(lines) == 3 and all(r.levelno == logging.ERROR for r in lines)
    assert {r.__dict__["harness"]["reason"] for r in lines} == {"route_not_granted", "run_unknown", "tool_not_granted"}


async def test_au5_a_violation_is_reported_once_across_passes() -> None:
    auditor = GrantAuditor(pinned())
    assert len((await auditor.audit([tool(DELETE)])).violations) == 1
    assert (await auditor.audit([tool(DELETE)])).violations == ()
    assert len((await auditor.audit([tool(DELETE, "inv-9")])).violations) == 1


async def test_au4_an_ungranted_intent_in_the_effect_log_is_a_violation() -> None:
    source = pinned()
    source.intent(ORG, RUN, DELETE, invocation_id="inv-j")
    report = await GrantAuditor(source).audit([tool(LOOKUP)])
    assert [(v.code, v.call.source, v.call.subject) for v in report.violations] == [
        ("tool_not_granted", "effect_log", DELETE)
    ]


TEXT_TOOL = (
    "2026-10-05 07:21:11,152 - harness_core.gateway - INFO - [logs.py:108] - gateway.call "
    "orgId=org-1 runId=run-1 attemptId=a1 invocationId=inv-1 disposition=executed toolId=crm@inst-1/create"
)
TEXT_MODEL = (
    "2026-10-05 07:21:12,000 - harness_core.gateway.model - INFO - [logs.py:108] - model_gateway.call "
    "orgId=org-1 sessionId=s runId=run-1 attemptId=a1 fence=1 api=chat inputTokens=3 routeId=openai:gpt-x status=ok"
)


def test_au3_text_and_json_lines_parse_to_calls() -> None:
    assert parse_call(TEXT_TOOL) == ExecutedCall("tool_gateway", "org-1", "run-1", CREATE, "a1", "inv-1")
    assert parse_call(TEXT_MODEL) == ExecutedCall("model_gateway", "org-1", "run-1", ROUTE, "a1")
    record = {"harness_event": "gateway.call", "harness": {"orgId": "o", "runId": "r", "toolId": LOOKUP,
                                                           "disposition": "recorded", "invocationId": "i"}}
    assert parse_call(json.dumps(record)) == ExecutedCall("tool_gateway", "o", "r", LOOKUP, None, "i")


@pytest.mark.parametrize(
    "line",
    [
        "",
        "2026 - x - INFO - engine.kernel.turn orgId=o runId=r",
        TEXT_TOOL.replace("disposition=executed", "disposition=refused"),
        TEXT_TOOL.replace("disposition=executed", "disposition=deferred"),
        json.dumps({"harness_event": "journal.commit", "harness": {}}),
    ],
)
def test_au3_other_lines_and_calls_that_never_ran_are_not_calls(line: str) -> None:
    assert parse_call(line) is None


@pytest.mark.parametrize(
    "line",
    [
        TEXT_TOOL.replace("toolId=crm@inst-1/create", ""),
        TEXT_MODEL.replace("routeId=openai:gpt-x", ""),
        TEXT_TOOL.replace("runId=run-1", ""),
        "{not json",
        json.dumps({"harness_event": "gateway.call"}),
    ],
)
def test_au3_a_call_line_missing_what_the_auditor_needs_is_malformed(line: str) -> None:
    with pytest.raises(MalformedLine):
        parse_call(line)


def _metered(*records: JournalUsageRecord, used: LedgerUsage | None, reserved: Amount = ZERO, ended: bool = True) -> InMemoryRunEvidenceSource:
    source = pinned()
    source.usage(ORG, RUN, *records)
    source.account(ORG, RUN, used=used, reserved=reserved)
    if ended:
        source.end(ORG, RUN)
    return source


MODEL_CALL = ExecutedCall("model_gateway", ORG, RUN, ROUTE, "a1")
RESPONSE = JournalUsageRecord("model.response", 120, 9, 100, 0, input_includes_cached=True, provider="openai", transport="chat")


async def test_c901_an_ended_runs_journal_usage_equal_to_its_ledger_is_reconciled() -> None:
    """C9.01, EV-10: the turn's ``model.response`` and a wrap-up's ``usage.recorded`` add up to the ledger's ``used``."""
    wrap_up = JournalUsageRecord("usage.recorded", 30, 4, 0, 0, input_includes_cached=True)
    auditor = GrantAuditor(_metered(RESPONSE, wrap_up, used=LedgerUsage(150, 13, 100, 0)))
    report = await auditor.audit([MODEL_CALL])
    assert (report.violations, report.reconciled) == ((), 1)


async def test_c901_a_difference_is_one_violation_with_both_totals(caplog: pytest.LogCaptureFixture) -> None:
    auditor = GrantAuditor(_metered(RESPONSE, used=LedgerUsage(500, 9, 100, 0)))
    with caplog.at_level(logging.ERROR):
        report = await auditor.audit([MODEL_CALL])
    [violation] = report.violations
    assert (violation.code, violation.call.run_id) == ("usage_unreconciled", RUN)
    assert (violation.detail["journalInputTokens"], violation.detail["ledgerInputTokens"]) == (120, 500)
    [line] = [r for r in caplog.records if getattr(r, "harness_event", "") == "audit.violation"]
    assert line.__dict__["harness"]["ledgerInputTokens"] == 500
    assert (await auditor.audit([MODEL_CALL])).violations == (), "reported once"


async def test_c901_a_legacy_response_counts_its_cached_tokens_as_the_fold_does() -> None:
    """A ``model.response`` journaled before USAGE1 (no ``inputIncludesCached``) by a reporter that leaves cached
    tokens out of its input (Anthropic's Messages) counts them in, as the fold does."""
    legacy = JournalUsageRecord("model.response", 20, 9, 100, 0, provider="anthropic", transport="messages")
    auditor = GrantAuditor(_metered(legacy, used=LedgerUsage(120, 9, 100, 0)))
    assert (await auditor.audit([MODEL_CALL])).reconciled == 1


@pytest.mark.parametrize(
    ("ended", "reserved"),
    [(False, ZERO), (True, Amount(0, 50))],
)
async def test_c901_a_run_still_running_or_holding_budget_waits_for_a_later_pass(ended: bool, reserved: Amount) -> None:
    source = _metered(RESPONSE, used=LedgerUsage(999, 9, 100, 0), reserved=reserved, ended=ended)
    auditor = GrantAuditor(source)
    first = await auditor.audit([MODEL_CALL])
    assert (first.violations, first.reconciled) == ((), 0)
    source.end(ORG, RUN)
    source.account(ORG, RUN, used=LedgerUsage(999, 9, 100, 0), reserved=ZERO)
    later = await auditor.audit([])
    assert [v.code for v in later.violations] == ["usage_unreconciled"]
    assert (await auditor.audit([])).runs == 0, "a decided run is not read again"


async def test_c901_a_run_whose_ledger_never_heard_a_usage_is_not_reconciled() -> None:
    auditor = GrantAuditor(_metered(RESPONSE, used=None))
    report = await auditor.audit([MODEL_CALL])
    assert (report.violations, report.reconciled) == ((), 0)
    assert (await auditor.audit([])).runs == 0
