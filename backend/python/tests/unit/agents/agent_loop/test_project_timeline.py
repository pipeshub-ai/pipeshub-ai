"""PROJECT_TIMELINE: a project's or policy's records, each with its date, in the search's order."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from app.agents.agent_loop import project_timeline as pt
from app.agents.agent_loop.project_timeline import (
    MAX_RECORDS,
    Timeline,
    TimelineEntry,
    TimelineSubject,
    collect_timeline,
    date_entry,
    finish_project_timeline,
    parse_subject,
    render_timeline,
    start_project_timeline,
)
from app.agents.agent_loop.protocol.run_usage import UsageLedger
from app.core import org_context
from app.core.org_context import OrgContext

_KB = {"connectorName": "KB", "origin": "UPLOAD"}
_JUN_02_MS = 1_748_822_400_000
_SEP_30_MS = 1_759_190_400_000


def _graph(rid: str, name: str, **extra: object) -> dict[str, Any]:
    return {"_key": rid, "recordName": name, "virtualRecordId": f"v-{rid}", **extra}


def _stored(*texts: str) -> dict[str, Any]:
    return {"block_containers": {"blocks": [{"index": i, "type": "text", "data": t} for i, t in enumerate(texts)]}}


def _entry(rid: str, day: str | None) -> TimelineEntry:
    return TimelineEntry(f"v-{rid}", rid, f"name-{rid}", f"opening {rid}", day, "log entry" if day else None)


def _org(org_id: str = "o") -> Any:  # noqa: ANN401 - a context manager
    return org_context.bind(OrgContext(org_id, "u", "user", "jwt", "-"))


def _context(**state: object) -> SimpleNamespace:
    return SimpleNamespace(
        tool_state=dict(state), include_retrieval_context=False, event_sink=None, conversation_id="c",
    )


# --- the subject -------------------------------------------------------------------------------------------------


def test_none_and_malformed_replies_name_no_subject() -> None:
    assert parse_subject(None) is None
    assert parse_subject({"kind": "none", "subject": "anything"}) is None
    assert parse_subject({"kind": "project", "subject": "  "}) is None
    assert parse_subject({"kind": "project"}) is None
    assert parse_subject(["project"]) is None  # type: ignore[arg-type]


def test_a_project_reply_keeps_two_distinct_other_phrasings() -> None:
    subject = parse_subject({
        "kind": "project", "subject": " Badge reader rollout ",
        "searches": ["badge reader rollout", "door access upgrade", "reader firmware", "a third"],
    })
    assert subject == TimelineSubject("project", "Badge reader rollout", ("door access upgrade", "reader firmware"))


def test_a_policy_reply_without_searches() -> None:
    assert parse_subject({"kind": "policy", "subject": "Travel booking standard", "searches": "x"}) == (
        TimelineSubject("policy", "Travel booking standard", ())
    )


class _Llm:
    def __init__(self, reply: object = None, error: Exception | None = None) -> None:
        self.reply, self.error, self.calls = reply, error, []

    async def ainvoke(self, messages: list[Any]) -> object:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.reply


def _use_model(monkeypatch: pytest.MonkeyPatch, llm: object) -> None:
    async def _model(_state: object) -> tuple[object, str]:
        return llm, "m"

    monkeypatch.setattr(pt, "verifier_model", _model)


def test_the_trigger_switch_picks_the_history_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(pt.PROJECT_TIMELINE_TRIGGER_ENV_VAR, raising=False)
    assert pt.subject_prompt() is pt._SUBJECT_PROMPT
    monkeypatch.setenv(pt.PROJECT_TIMELINE_TRIGGER_ENV_VAR, " History ")
    assert pt.subject_prompt() is pt._SUBJECT_PROMPT_HISTORY
    assert "something that history can change" in pt.subject_prompt()
    monkeypatch.setenv(pt.PROJECT_TIMELINE_TRIGGER_ENV_VAR, "always")
    assert pt.subject_prompt() is pt._SUBJECT_PROMPT


async def test_the_subject_call_reads_a_fenced_reply_and_reports_its_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    content = '```json\n{"kind": "project", "subject": "Badge reader rollout", "searches": []}\n```'
    llm = _Llm(AIMessage(content=content, usage_metadata={"input_tokens": 40, "output_tokens": 9, "total_tokens": 49}))
    _use_model(monkeypatch, llm)
    ledger = UsageLedger()
    context = _context(usage_ledger=ledger)

    subject = await pt.detect_subject(context, "Where does the badge reader rollout stand?")

    assert subject == TimelineSubject("project", "Badge reader rollout")
    assert "Where does the badge reader rollout stand?" in llm.calls[0][-1].content
    assert [source for source, _usage in ledger.entries] == ["project_timeline"]


async def test_the_subject_call_counts_a_one_record_question_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = _Llm(AIMessage(content='{"kind": "none"}'))
    _use_model(monkeypatch, llm)
    assert await pt.detect_subject(_context(), "Who owns the reader firmware repo?") is None
    assert "a single record answers" in llm.calls[0][0].content


async def test_a_reply_that_is_not_json_names_no_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_model(monkeypatch, _Llm(AIMessage(content="It is about a project.")))
    assert await pt.detect_subject(_context(), "q") is None


# --- dating ------------------------------------------------------------------------------------------------------


def test_a_connector_record_takes_its_source_system_date() -> None:
    graph = _graph("r1", "PR-7", connectorName="GITHUB", sourceLastModifiedTimestamp=_JUN_02_MS)
    entry = date_entry("v-r1", graph, _stored("Retry the reader sync"))
    assert entry is not None
    assert (entry.day, entry.basis) == ("2025-06-02", "source")


def test_an_upload_with_content_dates_takes_its_last_stated_date() -> None:
    graph = _graph(
        "r2", "memo", **_KB, sourceTimestampOrigin="content",
        sourceCreatedAtTimestamp=_JUN_02_MS, sourceLastModifiedTimestamp=_SEP_30_MS,
    )
    entry = date_entry("v-r2", graph, _stored("Memo"))
    assert entry is not None
    assert (entry.day, entry.basis) == ("2025-09-30", "content")


def test_an_upload_dated_only_by_its_upload_is_read_from_its_text() -> None:
    graph = _graph(
        "r3", "DESK-12", **_KB, sourceTimestampOrigin="upload",
        sourceCreatedAtTimestamp=_SEP_30_MS, sourceLastModifiedTimestamp=_SEP_30_MS,
    )
    entry = date_entry("v-r3", graph, _stored(
        "Reader offline at the loading dock", "2025-05-02 11:10 UTC - Marta: reported\n2025-05-07 15:45 UTC - Felix: fixed",
    ))
    assert entry is not None
    assert (entry.day, entry.basis, entry.first_line) == ("2025-05-07", "log entry", "Reader offline at the loading dock")


def test_table_rows_count_as_text_and_images_do_not() -> None:
    stored = {"block_containers": {"blocks": [
        {"index": 0, "type": "image", "data": {"uri": "data:image/png;base64,AAAA"}},
        {"index": 1, "type": "table_row", "data": {"row_natural_language_text": "Last updated: 2025-04-09, by Felix"}},
    ]}}
    entry = date_entry("v-r6", _graph("r6", "sheet", **_KB, sourceTimestampOrigin="upload"), stored)
    assert entry is not None
    assert (entry.day, entry.basis) == ("2025-04-09", "labelled date")
    assert entry.first_line == "Last updated: 2025-04-09, by Felix"


def test_an_upload_that_states_no_date_stays_undated() -> None:
    graph = _graph("r4", "notes", **_KB, sourceTimestampOrigin="upload", sourceCreatedAtTimestamp=_SEP_30_MS)
    entry = date_entry("v-r4", graph, _stored("Nothing dated in here."))
    assert entry is not None
    assert (entry.day, entry.basis) == (None, None)


def test_a_record_that_could_not_be_read_keeps_its_stored_date() -> None:
    graph = _graph("r5", "page", connectorName="CONFLUENCE", sourceLastModifiedTimestamp=_JUN_02_MS)
    entry = date_entry("v-r5", graph, None)
    assert entry is not None
    assert (entry.day, entry.first_line) == ("2025-06-02", "")


def test_a_record_without_an_id_is_dropped() -> None:
    assert date_entry("v-x", {"recordName": "x"}, _stored("2025-01-01 - a: b")) is None


# --- rendering ---------------------------------------------------------------------------------------------------


class _Shortener:
    def get_or_create_short_id(self, record_id: str) -> str:
        return f"s{record_id}"


def test_render_keeps_the_search_order_with_each_date_ids_a_read_call_and_the_rule() -> None:
    entries = (_entry("a", None), _entry("d", "2025-02-01"), _entry("b", "2024-12-01"))
    text = render_timeline(Timeline(TimelineSubject("policy", "Travel booking standard"), entries), _Shortener())
    lines = text.splitlines()
    assert lines[0].startswith('Records found by searching for the policy "Travel booking standard" (3), in the order')
    assert lines[1:4] == [
        "- no date stated | name-a | record_id=sa | opening a",
        "- 2025-02-01 (last log entry) | name-d | record_id=sd | opening d",
        "- 2024-12-01 (last log entry) | name-b | record_id=sb | opening b",
    ]
    assert 'knowledgegraph__fetch_record(record_ids=["sa", "sd", "sb"])' in text
    assert "superseded and give both dates" in text
    assert "is not another version" in text
    assert "are accounts of it, not versions" in text


# --- collecting --------------------------------------------------------------------------------------------------


class _Retrieval:
    def __init__(self, hits: list[dict[str, Any]], graph: dict[str, Any], status_code: int = 200) -> None:
        self.hits, self.graph, self.status_code, self.calls = hits, graph, status_code, []

    async def search_with_filters(self, **kwargs: object) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"searchResults": self.hits, "virtual_to_record_map": self.graph, "status_code": self.status_code}


def _patch_reader(monkeypatch: pytest.MonkeyPatch, stored: dict[str, dict[str, Any]]) -> list[Any]:
    lookups: list[Any] = []

    class _Reader:
        async def lookup_many(self, vrids: list[str]) -> dict[str, dict[str, Any]]:
            lookups.append(list(vrids))
            return {vrid: {"record_doc_id": f"doc-{vrid}"} for vrid in vrids}

        async def read_record(
            self, vrid: str, org_id: str, *, lookup: dict[str, Any] | None = None,
        ) -> dict[str, Any] | None:
            assert lookup == {"record_doc_id": f"doc-{vrid}"}
            if vrid == "boom":
                raise RuntimeError("unreadable")
            return stored.get(vrid)

    monkeypatch.setattr("app.modules.artifacts.reader.record_artifact_reader", lambda _blob_store: _Reader())
    return lookups


def _hit(vrid: str, score: float) -> dict[str, Any]:
    return {"score": score, "metadata": {"virtualRecordId": vrid}}


def _state(retrieval: _Retrieval, **extra: object) -> dict[str, Any]:
    return {
        "retrieval_service": retrieval, "blob_store": object(), "org_id": "not-the-bound-org", "user_id": "u",
        "filters": {"kb": ["k1"]}, **extra,
    }


async def test_collect_dates_the_found_records_once_each_in_search_order(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = {
        "v1": _graph("r1", "old ticket", **_KB, sourceTimestampOrigin="upload"),
        "v2": _graph("r2", "new spec", connectorName="CONFLUENCE", sourceLastModifiedTimestamp=_JUN_02_MS),
        "v3": _graph("r3", "thread", **_KB, sourceTimestampOrigin="upload"),
        "boom": _graph("r4", "lost", **_KB, sourceTimestampOrigin="upload"),
    }
    stored = {"v1": _stored("Ticket", "2024-11-02 10:00 - Jun: opened"), "v2": _stored("Spec"), "v3": _stored("chat")}
    lookups = _patch_reader(monkeypatch, stored)
    retrieval = _Retrieval(
        [_hit("v1", 0.9), _hit("v3", 0.8), _hit("v1", 0.7), _hit("boom", 0.6), _hit("v2", 0.5), _hit("vx", 0.4)],
        graph,
    )
    subject = TimelineSubject("project", "Badge reader rollout", ("door access upgrade",))

    with _org():
        timeline = await collect_timeline(_state(retrieval), subject)

    assert timeline is not None
    assert [e.record_id for e in timeline.entries] == ["r1", "r3", "r4", "r2"]
    assert [e.day for e in timeline.entries] == ["2024-11-02", None, None, "2025-06-02"]
    assert lookups == [["v1", "v3", "boom", "v2"]]
    call = retrieval.calls[0]
    assert call["queries"] == ["Badge reader rollout", "door access upgrade"]
    assert call["filter_groups"] == {"kb": ["k1"]}
    assert (call["org_id"], call["user_id"]) == ("o", "u")


async def test_collect_keeps_at_most_the_record_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = {f"v{i}": _graph(f"r{i}", f"rec {i}", **_KB) for i in range(MAX_RECORDS + 5)}
    _patch_reader(monkeypatch, {vrid: _stored("text") for vrid in graph})
    retrieval = _Retrieval([_hit(vrid, 1.0 - i / 100) for i, vrid in enumerate(graph)], graph)
    with _org():
        timeline = await collect_timeline(_state(retrieval), TimelineSubject("project", "Rollout"))
    assert timeline is not None
    assert len(timeline.entries) == MAX_RECORDS


async def test_collect_without_services_hits_or_a_good_search_gives_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_reader(monkeypatch, {})
    subject = TimelineSubject("project", "x")
    failed = _Retrieval([_hit("v1", 0.9)], {"v1": _graph("r1", "a")}, status_code=500)
    with _org():
        assert await collect_timeline({}, subject) is None
        assert await collect_timeline(_state(_Retrieval([], {})), subject) is None
        assert await collect_timeline(_state(failed), subject) is None


async def test_collect_searches_nothing_without_a_bound_org(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_reader(monkeypatch, {})
    retrieval = _Retrieval([_hit("v1", 0.9)], {"v1": _graph("r1", "a")})
    with pytest.raises(org_context.MissingOrgContext):
        await collect_timeline(_state(retrieval), TimelineSubject("project", "x"))
    assert retrieval.calls == []


# --- switch, build and finish ------------------------------------------------------------------------------------


def test_the_switch_is_off_unless_set_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PROJECT_TIMELINE", raising=False)
    monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
    assert start_project_timeline(_context(), "What changed in the rollout?") is None
    monkeypatch.setenv("PROJECT_TIMELINE", "true")
    assert pt.project_timeline_enabled()


async def test_a_failed_subject_call_builds_no_timeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROJECT_TIMELINE", "true")
    _use_model(monkeypatch, _Llm(error=RuntimeError("provider down")))
    task = start_project_timeline(_context(), "Where does the badge reader rollout stand?")
    assert task is not None
    assert await finish_project_timeline(_context(), task) is None


async def test_a_slow_build_is_given_up(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _slow(_context: object, _question: str) -> None:
        await asyncio.sleep(5)

    monkeypatch.setenv("PROJECT_TIMELINE", "true")
    monkeypatch.setattr(pt, "BUILD_TIMEOUT_S", 0.01)
    monkeypatch.setattr(pt, "detect_subject", _slow)
    task = start_project_timeline(_context(), "q")
    assert task is not None
    assert await finish_project_timeline(_context(), task) is None


async def _done(timeline: Timeline | None) -> asyncio.Task[Timeline | None]:
    async def _value() -> Timeline | None:
        return timeline

    return asyncio.create_task(_value())


async def test_finish_makes_the_listed_records_readable_and_renders_them() -> None:
    timeline = Timeline(TimelineSubject("project", "Rollout"), (_entry("d", "2025-02-01"), _entry("a", None)))
    context = _context()

    text = await finish_project_timeline(context, await _done(timeline))

    assert text is not None
    assert text.startswith('Records found by searching for the project "Rollout"')
    assert context.tool_state["known_record_ids"] == {"d", "a"}


async def test_finish_without_a_task_or_a_timeline_adds_nothing() -> None:
    context = _context()
    assert await finish_project_timeline(context, None) is None
    assert await finish_project_timeline(context, await _done(None)) is None
    assert "known_record_ids" not in context.tool_state
