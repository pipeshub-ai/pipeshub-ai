"""The page addresses a durable or engine run was given (``app.web.addresses``), bound to the run's session: its
recorded web pages and the links journaled beside them, so a resumed attempt and a later turn keep them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.harness.citations import EVENT as CITATIONS_EVENT
from app.web.addresses import MAX_SESSION_LINKS, RunAddresses, user_turns, web_addresses
from harness_core.provenance.types import WebPage

if TYPE_CHECKING:
    from app.harness.assembly.context import RunContext
    from app.harness.labels import SessionSourceLedger


def session_given(sessions: SessionSourceLedger) -> list[str]:
    """What the session's journal recorded as given: every web page its runs read, and the links journaled beside them
    (``webLinks``), at most ``MAX_SESSION_LINKS`` of those."""
    given = [source.url for source in sessions.provenance.citable() if isinstance(source, WebPage)]
    links = 0
    for run in sessions.provenance.runs:
        for event in sessions.provenance.events_of(run):
            if event.type != CITATIONS_EVENT:
                continue
            for entry in event.data.get("webLinks") or []:
                for url in entry.get("links") or [] if isinstance(entry, dict) else []:
                    if links >= MAX_SESSION_LINKS:
                        return given
                    if isinstance(url, str):
                        given.append(url)
                        links += 1
    return given


def run_addresses(tool_state: dict[str, Any], run: RunContext, sessions: SessionSourceLedger) -> RunAddresses:
    """What ``run`` may open after a private read: the pages its session's journal recorded (a resumed attempt and a
    later turn keep them), what its own web calls and the citation ref mapper hold, and the person's own words. A child
    run's query is its parent model's goal, not the person's, so a child counts only what it was given itself."""
    web_addresses(tool_state).bind_session(lambda: session_given(sessions))
    person = () if run.sub_agent is not None else (run.query, *user_turns(run.previous_conversations))
    return RunAddresses(tool_state, user_text=person)


__all__ = ["run_addresses", "session_given"]
