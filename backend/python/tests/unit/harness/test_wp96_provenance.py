"""WP-96 (WP96-PORT, WP96-DECL, WP96-FLOW, WP96-REPLAY, WP96-CITE, WP96-BOUND): ADR-RUN-01 D3 in PipesHub runs.

Every tool result is recorded with its provenance by the tool port (``x.pipeshub.provenance.recorded``) and the
session's ``ProvenanceLedger`` is the one store the flow guard, the answer's citations and audit read. A tool
source cannot declare a tool without a rule, and the port refuses a granted tool without one. An older run's
results are replayed from its outcomes and citation deltas as before (WP-62, WP-83 keep passing). Links into a
repository the session fetched publicly, at the fetched commit, are cited as the files they name, and a child's
sources are narrowed before they are bounded (WP-81) because ``CitationJournal`` offers no other way.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.assembly.tool_sources import SourcedTool
from app.harness.citations import CitationJournal
from app.harness.engines.answer import EngineAnswer
from app.harness.engines.child_result import EngineChildResult
from app.harness.engines.repository_citations import cite_repository_links
from app.harness.labels import (
    SessionSourceLedger,
    call_provenance,
    citation_provenance,
    own_work_label,
)
from app.harness.repositories import TOOL_ID as REPOSITORY_ID
from app.harness.tool_provenance import connector_rule, platform_rule
from app.utils.chat_helpers import CitationRefMapper
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import FlowGuardedPolicy, FlowRule
from harness_core.effects.types import ToolEffect, ToolExecution
from harness_core.journal.types import NewEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext, classify_tool_effect
from harness_core.provenance.repository import RepositoryTree
from harness_core.provenance.rules import (
    CONNECTOR_RULE,
    MCP_RULE,
    NO_CONTENT_RULE,
    OPAQUE_RULE,
    RECORDS_RULE,
    REPOSITORY_RULE,
    WEB_RULE,
    UndeclaredProvenance,
)
from harness_core.provenance.stamp import PROVENANCE_EVENT, ProvenanceStamp
from harness_core.provenance.types import (
    RecordRef,
    RepositoryRef,
    ResultProvenance,
    WebPage,
    provenance_from_wire,
)
from harness_core.testing.contracts.source_ledger import Stream
from harness_core.tools.base import ParameterType, Tag, ToolParameter
from harness_core.tools.decorators import tool
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.toolset import ToolsetBuilder
from tests.unit.harness.engines.test_source_ledger import (
    DIGEST,
    SEARCH_ID,
    TOOL_NAMES,
    WEB_ID,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import JsonValue

ENGINE = "run-engine"
COMMIT = "4f83564c1f0e8596be3f71d78f847a96447a3bdd"
ONYX = RepositoryRef("https://github.com/onyx-dot-app/onyx", COMMIT)
NAMES = {**TOOL_NAMES, REPOSITORY_ID: "repository_fetch"}
_CTX = ToolContext("run1", "org1", "inv-1", "a1", "org1:run1:inv-1")


# ---- the tool port records every result (WP96-PORT) ----


class _Docs:
    @tool(
        path="/tools/docs/search",
        short_description="Search",
        description="Search the docs.",
        parameters=[ToolParameter(name="q", type=ParameterType.STRING, description="Query", required=True)],
    )
    async def search(self, q: str) -> str:
        return f"found {q}"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    ToolsetBuilder(_Docs(), name="docs", description="Docs", path_prefix="/tools/docs").register_into(registry)
    return registry


async def _cited(call: ToolCall, result: object) -> tuple[NewEvent, ...]:
    del call, result
    delta: dict[str, JsonValue] = {"callId": "c1", "records": {"vr-1": {"id": "rec-1"}}}
    return (NewEvent("x.pipeshub.citations.recorded", delta, critical=False),)


async def test_wp96_port_1_each_result_rides_its_provenance_record() -> None:
    registry = _registry()
    effects = {"docs__search": classify_tool_effect("docs__search", [Tag("category", "read")])}
    stamp = ProvenanceStamp.for_tools(effects, {"docs__search": RECORDS_RULE}, journal=True, sources_of=call_provenance)
    port = RegistryToolPort(registry, effects, present=_cited, provenance=stamp)
    done = await port.execute(ToolCall(id="c1", name="docs__search", arguments={"q": "x"}), _CTX)
    record = done.events[-1]
    assert record.type == PROVENANCE_EVENT
    data = cast("dict[str, Any]", record.data)
    assert (data["invocationId"], data["rule"]) == ("inv-1", "records")
    assert provenance_from_wire(data["provenance"]) == ResultProvenance("principals", "first_party", (RecordRef("rec-1"),))


def test_wp96_port_2_a_granted_tool_without_a_rule_is_refused() -> None:
    registry = _registry()
    effects = {"docs__search": classify_tool_effect("docs__search", [Tag("category", "read")])}
    with pytest.raises(UndeclaredProvenance):
        _ = RegistryToolPort(registry, effects, provenance=ProvenanceStamp({}, journal=True))


# ---- declarations (WP96-DECL) ----


def test_wp96_decl_1_a_tool_source_cannot_declare_a_tool_without_a_rule() -> None:
    field = next(f for f in dataclasses.fields(SourcedTool) if f.name == "provenance")
    assert field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
    with pytest.raises(TypeError):
        _ = cast("Any", SourcedTool)("t", "pipeshub@x/t", None)


@pytest.mark.parametrize(
    ("name", "tool_id", "rule"),
    [
        ("knowledgegraph__search", "pipeshub@knowledgegraph/search", RECORDS_RULE),
        ("internaltools__ask_user_question", "pipeshub@internaltools/ask_user_question", NO_CONTENT_RULE),
        ("dynamic__fetch_url", "web@default/fetch_url", WEB_RULE),
        ("codingsandbox__run_code", "pipeshub@sandbox/run_code", OPAQUE_RULE),
    ],
)
def test_wp96_decl_2_platform_tools_by_what_they_return(name: str, tool_id: str, rule: object) -> None:
    assert platform_rule(name, tool_id) is rule


def test_wp96_decl_3_every_connector_action_has_one_rule() -> None:
    assert connector_rule("jira__create_issue", "jira@inst-1/create_issue") is CONNECTOR_RULE
    assert connector_rule("slack__send_message", "slack@unknown/send_message") is CONNECTOR_RULE
    assert (CONNECTOR_RULE.answered.readers, CONNECTOR_RULE.answered.integrity) == ("unknown", "first_party")
    assert MCP_RULE.failed == MCP_RULE.answered


# ---- the flow guard reads the ledger (WP96-FLOW) ----


def _call(stream: Stream, invocation: str, tool_id: str, labels: dict[str, JsonValue], *, egress: str = "external") -> None:
    _ = stream.add(
        "tool.intent",
        {
            "invocationId": invocation, "toolId": tool_id, "argsDigest": DIGEST, "effect": "read",
            "sink": cast("JsonValue", {"egress": egress, "argsLeaveBoundary": egress == "external"}),
        },
        run_id=ENGINE,
    )
    _ = stream.add("tool.outcome", {"invocationId": invocation, "status": "ok", "labels": labels}, run_id=ENGINE)


def _recorded(stream: Stream, invocation: str, tool_id: str, rule: str, provenance: ResultProvenance) -> None:
    _ = stream.add(
        PROVENANCE_EVENT,
        {"invocationId": invocation, "toolId": tool_id, "rule": rule, "provenance": provenance.to_wire()},
        run_id=ENGINE,
    )


def _fetch_url_decision(stream: Stream) -> str:
    ledger = SessionSourceLedger(tool_names=NAMES)
    ledger.apply(stream.events)
    policy = FlowGuardedPolicy(lambda call, effect: "allow", FlowRule("ask"), ledger, ENGINE)
    effect = ToolEffect(WEB_ID, "read", True, SinkDescriptor(egress="external", args_leave_boundary=True))
    return policy(ToolCall(id="c9", name="dynamic__fetch_url", arguments={"url": "https://onyx.app/"}), effect)


def test_wp96_flow_1_a_recorded_public_clone_keeps_web_reads_unasked_whatever_its_labels() -> None:
    """Run 7's shape on the new design: the record decides, not the outcome's labels (which say nothing here)."""
    stream = Stream()
    stream.admitted(ENGINE)
    for clone in ("clone-1", "clone-2"):
        _call(stream, clone, REPOSITORY_ID, {"integrity": "external"})
        _recorded(stream, clone, REPOSITORY_ID, "repository", ResultProvenance("public", "external", (ONYX,)))
    assert _fetch_url_decision(stream) == "allow"
    label = own_work_label(stream.events, ENGINE, tool_names=NAMES)
    assert (label.complete, label.sources) == (True, frozenset())


def test_wp96_flow_2_a_recorded_unknown_result_holds_web_reads_whatever_its_labels() -> None:
    stream = Stream()
    stream.admitted(ENGINE)
    _call(stream, "mcp-1", WEB_ID, {"integrity": "external", "confidentiality": {"readers": "public", "sources": []}})
    _recorded(stream, "mcp-1", "mcp@srv/read", "mcp", ResultProvenance("unknown", "external", ()))
    assert _fetch_url_decision(stream) == "ask"


def test_wp96_flow_3_a_recorded_private_read_holds_a_web_read_and_names_its_record() -> None:
    stream = Stream()
    stream.admitted(ENGINE)
    _call(stream, "search-1", SEARCH_ID, {"integrity": "internal"}, egress="none")
    _ = stream.add("x.pipeshub.citations.recorded", {"callId": "search-1", "records": {"vr": {"id": "rec-1"}}}, run_id=ENGINE)
    _recorded(stream, "search-1", SEARCH_ID, "records", ResultProvenance("principals", "first_party", (RecordRef("rec-1"),)))
    ledger = SessionSourceLedger(tool_names=NAMES)
    ledger.apply(stream.events)
    assert ledger.source_records(ENGINE) == frozenset({"rec-1"})
    assert _fetch_url_decision(stream) == "ask"


# ---- older runs replay as before (WP96-REPLAY) ----


def test_wp96_replay_1_an_older_run_reads_as_its_outcomes_did() -> None:
    stream = Stream()
    stream.admitted(ENGINE)
    _call(stream, "web-1", WEB_ID, {"integrity": "external"})
    _ = stream.add(
        "x.pipeshub.repository.fetched",
        {"host": "github.com", "repository": "onyx-dot-app/onyx", "bytes": 1, "cached": False, "full": False,
         "commit": COMMIT},
        run_id=ENGINE,
    )
    _call(stream, "clone-1", REPOSITORY_ID, {"integrity": "external", "confidentiality": {"readers": "public", "sources": []}})
    ledger = SessionSourceLedger(tool_names=NAMES)
    ledger.apply(stream.events)
    assert ledger.source_records(ENGINE) == frozenset()
    # Its repository is a source of the run's record, replayed from the fetch's own event.
    assert ONYX in ledger.provenance.citable(ENGINE)
    assert all(not f.recorded for f in ledger.provenance.facts(ENGINE))


def test_wp96_replay_2_a_citation_delta_of_a_recorded_call_is_not_counted_twice() -> None:
    stream = Stream()
    stream.admitted(ENGINE)
    _call(stream, "search-1", SEARCH_ID, {"integrity": "internal"}, egress="none")
    _ = stream.add("x.pipeshub.citations.recorded", {"callId": "search-1", "records": {"vr": {"id": "rec-1"}}}, run_id=ENGINE)
    # The record says the call named nothing private (what the stamp decided): the delta is not read again.
    _recorded(stream, "search-1", SEARCH_ID, "records", ResultProvenance("public", "first_party", ()))
    ledger = SessionSourceLedger(tool_names=NAMES)
    ledger.apply(stream.events)
    assert ledger.source_records(ENGINE) == frozenset()


def test_wp96_replay_3_a_delta_names_web_pages_and_untrusted_records() -> None:
    data: dict[str, JsonValue] = {
        "webRecords": [{"url": "https://onyx.app/", "content": "x"}],
        "records": {"vr": {"id": "rec-1", "connector": "SLACK"}},
    }
    provenance = citation_provenance(data, fetched_at="T")
    assert provenance.readers == "principals" and provenance.integrity == "external"
    assert WebPage("https://onyx.app/", "T") in provenance.sources and RecordRef("rec-1") in provenance.sources


# ---- repository files are cited (WP96-CITE) ----


def _context() -> Any:  # noqa: ANN401 - a stand-in AgentContext
    return SimpleNamespace(tool_state={"citation_ref_mapper": CitationRefMapper()})


class _Fetched:
    """``CitableRepositories``: ONYX fetched publicly, with its tree at the fetched commit."""

    def __init__(self, *fetched: RepositoryRef) -> None:
        self._fetched = fetched

    def fetched(self) -> tuple[RepositoryRef, ...]:
        return self._fetched

    def tree(self, source: RepositoryRef) -> RepositoryTree | None:
        return ONYX_TREE if source.commit == COMMIT else None


ONYX_TREE = RepositoryTree(frozenset({"backend/onyx/main.py"}), frozenset({"backend", "backend/onyx"}))
MAIN_PY = f"https://github.com/onyx-dot-app/onyx/blob/{COMMIT}/backend/onyx/main.py"


def test_wp96_cite_1_a_link_into_the_fetched_tree_becomes_a_source_and_a_branch_link_its_permalink() -> None:
    context = _context()
    text = cite_repository_links(
        f"See [main.py]({MAIN_PY}) and {MAIN_PY}. The [same at main](https://github.com/onyx-dot-app/onyx/blob/main/"
        "backend/onyx/main.py), not [a gone file](https://github.com/onyx-dot-app/onyx/blob/main/x.py).",
        context,
        _Fetched(ONYX),
    )
    assert text.startswith(
        "See [main.py](ref1) and [onyx-dot-app/onyx · backend/onyx/main.py](ref1). The [same at main](ref1)"
    ), text
    assert "blob/main/x.py" in text
    records = context.tool_state["web_records"]
    assert [(r["url"], r["title"], r["sourceKind"]) for r in records] == [
        (MAIN_PY, "onyx-dot-app/onyx · backend/onyx/main.py", "repository")
    ]


def test_wp96_cite_2_the_engine_answer_cites_repository_files() -> None:
    context = SimpleNamespace(tool_state={})
    answer = EngineAnswer(context, lambda: (), _Fetched(ONYX))
    text, citations = answer(f"The app starts in [main.py]({MAIN_PY}).")
    assert len(citations) == 1, text
    metadata = citations[0]["metadata"]
    assert (metadata["webUrl"], metadata["recordName"], metadata["connector"], citations[0]["citationType"]) == (
        MAIN_PY, "onyx-dot-app/onyx · backend/onyx/main.py", "REPOSITORY", "web|repository",
    )
    # Without the ledger's repository, the same link is a plain link and no source.
    _, none = EngineAnswer(SimpleNamespace(tool_state={}), lambda: (), _Fetched())(f"[main.py]({MAIN_PY})")
    assert none == []


def test_wp96_cite_3_a_child_hands_its_repository_sources_to_its_parent() -> None:
    from app.harness.citations import CHILD_SOURCES_KEY

    context = SimpleNamespace(tool_state={}, include_retrieval_context=False)
    link = f"https://github.com/onyx-dot-app/onyx/blob/{COMMIT}/backend"
    out = EngineChildResult(context, lambda: (), _Fetched(ONYX))({"output": f"See [backend]({link})."}, [])
    sources = cast("dict[str, Any]", out[CHILD_SOURCES_KEY])
    # A folder opens the host's tree view at the fetched commit.
    page = f"https://github.com/onyx-dot-app/onyx/tree/{COMMIT}/backend"
    assert [r["url"] for r in sources["webRecords"]] == [page]
    assert [p["url"] for p in sources["refs"]] == [page]


def test_wp96_cite_4_session_repositories_read_the_fetch_listing_from_the_cache(tmp_path: Path) -> None:
    import json

    from app.harness.repositories import SessionRepositories
    from harness_core.repos.cache import TREE_FILE, RepositoryCache
    from harness_core.repos.fetch import MIB

    stream = Stream()
    stream.admitted(ENGINE)
    _call(stream, "clone-1", REPOSITORY_ID, {"integrity": "external"})
    _recorded(stream, "clone-1", REPOSITORY_ID, "repository", ResultProvenance("public", "external", (ONYX,)))
    sessions = SessionSourceLedger(tool_names=NAMES)
    sessions.apply(stream.events)
    cache = RepositoryCache(tmp_path / "cache", max_bytes=64 * MIB, max_age_s=3600)
    staged = cache.temporary()
    (staged / "repo.tar").write_bytes(b"x")
    (staged / TREE_FILE).write_text(json.dumps({"files": ["backend/onyx/main.py"], "folders": ["backend"]}))
    _ = cache.put("k", staged, head=COMMIT, ref=None, label="github.com/onyx-dot-app/onyx", public=True)
    repositories = SessionRepositories(sessions.provenance, lambda: cache)
    assert repositories.fetched() == (ONYX,)
    tree = repositories.tree(ONYX)
    assert tree is not None and tree.kind("backend/onyx/main.py") == "file"


# ---- narrowing precedes the bound (WP96-BOUND) ----


def test_wp96_bound_1_citation_journal_offers_no_unbounded_delta() -> None:
    import inspect

    assert "bounded" not in inspect.signature(CitationJournal.delta).parameters
    context = SimpleNamespace(tool_state={"web_records": [{"url": "https://a/", "content": "x"}]})
    seen: list[dict[str, Any]] = []

    def narrow(whole: dict[str, Any]) -> dict[str, Any]:
        seen.append(dict(whole))
        return {"webRecords": []}

    assert CitationJournal(context).cited(narrow) == {"webRecords": []}
    assert seen and seen[0]["webRecords"] == [{"url": "https://a/", "content": "x"}]


def test_wp96_repo_tool_declares_its_rule() -> None:
    from app.harness.repositories import REPOSITORY_FETCH, RepositoryCatalog

    class _Bare:
        def schemas(self) -> list[Any]:
            return [SimpleNamespace(name=REPOSITORY_FETCH)]

        def effect(self, name: str) -> None:
            return None

        def provenance(self, name: str) -> None:
            return None

    with pytest.raises(UndeclaredProvenance):
        _ = RepositoryCatalog(cast("Any", None), cast("Any", _Bare()))
    assert REPOSITORY_RULE.provenance(
        cast("Any", SimpleNamespace(status="ok", claim=None))
    ).readers == "unknown"
    assert ToolExecution("ok", "{}", provenance=ResultProvenance("public", "external", (ONYX,))).public


# ---- the outcome lists the ledger's sources (WP96-OUTCOME) ----


def test_wp96_outcome_1_sources_gathered_are_the_ledgers_by_their_ids() -> None:
    from harness_core.effects.flow import GrantingLedger
    from harness_core.engines.outcome.gathered import gathered_sources
    from harness_core.provenance.types import source_id

    stream = Stream()
    stream.admitted(ENGINE)
    page = WebPage("https://onyx.app/", "T")
    _call(stream, "clone-1", REPOSITORY_ID, {"integrity": "external"})
    _recorded(stream, "clone-1", REPOSITORY_ID, "repository", ResultProvenance("public", "external", (ONYX,)))
    _call(stream, "web-1", WEB_ID, {"integrity": "external"})
    _recorded(stream, "web-1", WEB_ID, "web", ResultProvenance("public", "external", (page,)))
    _call(stream, "search-1", SEARCH_ID, {"integrity": "internal"}, egress="none")
    _recorded(stream, "search-1", SEARCH_ID, "records", ResultProvenance("principals", "first_party", (RecordRef("rec-1"),)))
    sessions = SessionSourceLedger(tool_names=NAMES)
    ledger = GrantingLedger(sessions)
    ledger.apply(stream.events)
    gathered = gathered_sources(ledger, ENGINE)
    assert [g.ref for g in gathered] == [source_id(ONYX), source_id(page), source_id(RecordRef("rec-1"))]
    assert (gathered[0].url, gathered[0].title) == (f"https://github.com/onyx-dot-app/onyx/commit/{COMMIT}", "onyx-dot-app/onyx")
    assert gathered[1].url == "https://onyx.app/" and gathered[2].url is None
    assert gathered_sources(object(), ENGINE) == ()


# ---- approvals follow main #4313: the flow guard is unchanged (WP96-APPROVALS) ----

DRIVE_READ = "drive@inst-1/read_file"
JIRA_WRITE = ToolEffect("jira@inst-2/update_issue", "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))


def _connector_read(stream: Stream, invocation: str, tool_id: str, rule: Any, *, recorded: bool, labels: dict[str, JsonValue]) -> None:  # noqa: ANN401
    """A toolset read whose citation delta names the live item it read; ``recorded``: with the stamp's record, made by
    the rule the tool is declared with (as the port makes it), else as a worker before WP-96 journaled it."""
    delta: dict[str, JsonValue] = {
        "callId": invocation,
        "toolRecords": [{"metadata": {"sourceKind": "live", "connector": "DRIVE", "externalRecordId": "file-1",
                                      "recordId": "live:file-1"}}],
    }
    _call(stream, invocation, tool_id, labels)
    _ = stream.add("x.pipeshub.citations.recorded", delta, run_id=ENGINE)
    if recorded:
        stamp = ProvenanceStamp({"read": rule}, journal=True, sources_of=call_provenance)
        execution = ToolExecution("ok", "x", events=(NewEvent("x.pipeshub.citations.recorded", delta),))
        record = stamp.record("read", tool_id, invocation, execution)
        _ = stream.add(record.type, cast("dict[str, JsonValue]", record.data), run_id=ENGINE)


def _write_decision(stream: Stream) -> tuple[str, bool]:
    ledger = SessionSourceLedger(tool_names=NAMES)
    ledger.apply(stream.events)
    policy = FlowGuardedPolicy(lambda call, effect: "allow", FlowRule("ask", above="private"), ledger, ENGINE)
    decision = policy(ToolCall(id="w1", name="jira__update_issue", arguments={"key": "J-1"}), JIRA_WRITE)
    return decision, ledger.untrusted_read(ENGINE)


def _session(read: tuple[str, Any, dict[str, JsonValue]] | None, *, recorded: bool, private: bool) -> Stream:
    stream = Stream()
    stream.admitted(ENGINE)
    if private:
        _call(stream, "search-1", SEARCH_ID, {"integrity": "internal"}, egress="none")
        _ = stream.add("x.pipeshub.citations.recorded", {"callId": "search-1", "records": {"vr": {"id": "rec-1"}}},
                       run_id=ENGINE)
    if read is not None:
        tool_id, rule, labels = read
        _connector_read(stream, "read-1", tool_id, rule, recorded=recorded, labels=labels)
    return stream


CONNECTOR_READ = (DRIVE_READ, CONNECTOR_RULE, {"integrity": "internal"})
MCP_READ = ("mcp@srv/read", MCP_RULE, {"integrity": "external"})


@pytest.mark.parametrize("private", [False, True], ids=["after-nothing", "after-a-private-read"])
def test_wp96_approvals_1_a_connector_read_then_a_toolset_write_decides_as_before_wp96(private: bool) -> None:
    """Read a Drive file, then update a Jira issue (approvals follow main #4313,
    the flow guard unchanged). With the stamp's record the write is decided exactly as before WP-96, and the
    connector's result taints nothing: no Rule of Two card comes from it."""
    before = _write_decision(_session(CONNECTOR_READ, recorded=False, private=private))
    after = _write_decision(_session(CONNECTOR_READ, recorded=True, private=private))
    assert after == before
    assert after[1] is False


def test_wp96_approvals_2_without_any_read_a_toolset_write_runs() -> None:
    assert _write_decision(_session(None, recorded=True, private=False)) == ("allow", False)


@pytest.mark.parametrize("recorded", [False, True], ids=["before-wp96", "recorded"])
def test_wp96_approvals_3_an_mcp_result_keeps_its_behaviour(recorded: bool) -> None:
    assert _write_decision(_session(MCP_READ, recorded=recorded, private=False)) == ("ask", True)


def test_wp96_outcome_2_a_loop_run_journals_its_gathered_sources_from_the_ledger() -> None:
    """The loop's own outcome (no kernel ledger of its own): its facts from the journal, each call the provenance
    ledger names sources for counted by those sources, journaled once."""
    from app.harness.labels import PipeshubReplay
    from harness_core.engines.outcome.gathered import loop_outcome_events
    from harness_core.engines.outcome.ledger import ledger_from_events
    from harness_core.provenance.ledger import (
        ProvenanceFeed,
        ProvenanceLedger,
        provenance_of,
    )
    from harness_core.provenance.types import source_id

    stream = Stream()
    stream.admitted(ENGINE)
    page = WebPage("https://onyx.app/", "T")
    _call(stream, "web-1", WEB_ID, {"integrity": "external"})
    _recorded(stream, "web-1", WEB_ID, "web", ResultProvenance("public", "external", (page,)))
    _call(stream, "ask-1", "pipeshub@internaltools/ask_user_question", {"integrity": "internal"})
    _recorded(stream, "ask-1", "pipeshub@internaltools/ask_user_question", "no_content", ResultProvenance("public", "first_party", ()))
    fed = ProvenanceFeed(ProvenanceLedger(PipeshubReplay()))
    fed.apply(stream.events)
    assert provenance_of(fed) is fed.provenance
    events = loop_outcome_events(list(fed.provenance.events_of(ENGINE)), ENGINE, fed)
    for event in events:
        _ = stream.add(event.type, cast("dict[str, JsonValue]", event.data), run_id=ENGINE)
    refs = [f.ref for f in ledger_from_events(stream.events, run_id=ENGINE).sources]
    assert refs == [source_id(page), "invocation:ask-1"]
    # Journaled once: nothing is left to record on a later finalize.
    fed.apply(stream.events)
    assert loop_outcome_events(list(fed.provenance.events_of(ENGINE)), ENGINE, fed) == []
