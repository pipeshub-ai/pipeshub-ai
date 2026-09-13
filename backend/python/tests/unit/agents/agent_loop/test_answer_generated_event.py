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
        "email": "dev@example.com",
        "domain": "example.com",
        "chat_mode": "internal_search",
        "citation_count": 3,
        "connectors": ["GITHUB", "JIRA", "UPLOAD"],
        "demo_sources": True,
    }
    serialized = str(events[0])
    assert "secret text" not in serialized
    assert "PR #482" not in serialized


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
