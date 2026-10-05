"""The answer_generated activation event carries counts and source types only."""

from __future__ import annotations

from types import SimpleNamespace

from app.agents.agent_loop.respond import _record_answer_generated
from app.telemetry.event_buffer import event_buffer


def _drain() -> list[dict]:
    return event_buffer.drain()


def _context() -> SimpleNamespace:
    return SimpleNamespace(org_id="org-1", user_id="user-1", user_email="dev@example.com")


def test_records_counts_source_types_and_demo_flag() -> None:
    _drain()
    state = {
        "chat_mode": "internal_search",
        "agent_knowledge": [
            {"connectorId": "demo-1", "type": "Demo", "name": "Acme Corp demo data"},
            {"connectorId": "kb-1", "type": "KB"},
        ],
    }
    citations = [
        {"content": "secret text", "metadata": {"connector": "GITHUB", "connectorId": "demo-1", "recordName": "PR #482"}},
        {"content": "more text", "metadata": {"connector": "JIRA", "connectorId": "demo-1"}},
        {"content": "kb text", "metadata": {"origin": "UPLOAD", "connectorId": "kb-1"}},
    ]

    _record_answer_generated(_context(), state, citations)  # type: ignore[arg-type]

    events = _drain()
    assert len(events) == 1
    assert events[0]["event"] == "answer_generated"
    assert events[0]["props"] == {
        "orgId": "org-1",
        "userId": "user-1",
        "domain": "example.com",
        "chat_mode": "internal_search",
        "citation_count": 3,
        "connectors": ["GITHUB", "JIRA", "UPLOAD"],
        "demo_sources": True,
        "stopped": False,
    }
    serialized = str(events[0])
    assert "secret text" not in serialized
    assert "PR #482" not in serialized
    assert "dev@example.com" not in serialized


def test_demo_flag_from_the_chat_route_source_list() -> None:
    """The chat route has no agent_knowledge; its sources are `available_connectors`
    with `id` and `type`, and the citation's connectorId has to match one of them."""
    _drain()
    state = {
        "chat_mode": "internal_search",
        "available_connectors": [
            {"id": "demo-1", "name": "Acme Corp demo data", "type": "Demo"},
            {"id": "gh-1", "name": "GitHub", "type": "GitHub"},
        ],
    }
    citations = [{"metadata": {"connector": "DRIVE", "connectorId": "demo-1", "origin": "CONNECTOR"}}]
    _record_answer_generated(_context(), state, citations)  # type: ignore[arg-type]
    (event,) = _drain()
    assert event["props"]["demo_sources"] is True
    assert event["props"]["connectors"] == ["DRIVE"]


def test_demo_flag_false_when_the_cited_connector_is_a_real_one() -> None:
    _drain()
    state = {
        "chat_mode": "internal_search",
        "available_connectors": [
            {"id": "demo-1", "type": "Demo"},
            {"id": "gh-1", "type": "GitHub"},
        ],
    }
    citations = [{"metadata": {"connector": "GITHUB", "connectorId": "gh-1", "origin": "CONNECTOR"}}]
    _record_answer_generated(_context(), state, citations)  # type: ignore[arg-type]
    (event,) = _drain()
    assert event["props"]["demo_sources"] is False


def test_no_citations_and_no_demo_sources() -> None:
    _drain()
    _record_answer_generated(_context(), {"chat_mode": "agent"}, [])  # type: ignore[arg-type]
    (event,) = _drain()
    assert event["props"]["citation_count"] == 0
    assert event["props"]["connectors"] == []
    assert event["props"]["demo_sources"] is False


def test_never_raises_on_malformed_input() -> None:
    _drain()
    _record_answer_generated(SimpleNamespace(), {"agent_knowledge": "not a list"}, [{"metadata": None}, "junk"])  # type: ignore[arg-type,list-item]
    # Either an event was recorded from what could be read, or nothing was — but no exception.
    assert len(_drain()) <= 1


def test_answer_also_counts_on_the_grafana_activity_counter(monkeypatch) -> None:
    """The same step is counted for dashboards with org/domain only — no user id or address."""
    calls: list[dict] = []
    import app.agents.agent_loop.respond as respond

    monkeypatch.setattr(respond, "record_service_activity", lambda *a, **k: calls.append({"args": a, **k}))
    _drain()
    state = {"chat_mode": "internal_search", "available_connectors": [{"id": "demo-1", "type": "Demo"}]}
    citations = [{"metadata": {"connector": "DRIVE", "connectorId": "demo-1"}}]
    respond._record_answer_generated(_context(), state, citations)  # type: ignore[arg-type]

    assert calls == [{
        "args": ("query_service", "answer_generated"),
        "connector": "demo",
        "status": "cited",
        "org": "org-1",
        "domain": "example.com",
    }]
    assert "dev@example.com" not in str(calls)


def test_a_stopped_answer_is_flagged_and_counted_under_its_own_status(monkeypatch) -> None:
    calls: list[dict] = []
    import app.agents.agent_loop.respond as respond

    monkeypatch.setattr(respond, "record_service_activity", lambda *a, **k: calls.append(k))
    _drain()
    citations = [{"metadata": {"connector": "SLACK", "connectorId": "c-1"}}]
    respond._record_answer_generated(_context(), {"chat_mode": "agent"}, citations, stopped=True)  # type: ignore[arg-type]

    events = _drain()
    assert events[0]["props"]["stopped"] is True
    assert events[0]["props"]["citation_count"] == 1
    assert calls[0]["status"] == "stopped"
