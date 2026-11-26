"""A small fake workplace the legacy agent can act in: knowledge docs, Jira,
Slack, Gmail, Drive and the web, all in memory.

Reads return fixture data. Writes and outbound fetches are committed to a
:class:`FakeSaaS`, so a test asserts on what actually left the harness
(the outcome), not on what the model said it would do. Every tool carries the
tags a production connector of its kind carries (writes ``risk=high``,
``category=write``; reads ``type=read``; each its ``effect``), so any policy
keyed on tags sees them exactly as it would see the product.

The toolsets are registered in place of the product's toolset registry by
:mod:`tests.harness_replay.legacy_driver`; the active world is a context
variable so concurrent runs never share state.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from harness_core.testing import FakeSaaS
from harness_core.testing.replay import RecordedEffect
from harness_core.tools.tags import EFFECT_TAG_KEY, SINK_TAG_KEY
from tests.harness_replay.search import matching, words

if TYPE_CHECKING:
    from collections.abc import Sequence

ORG_DOMAIN = "corp.example"
# The effect tags production connectors carry (P2.1): untagged is a write, so an untagged read here would be held by
# the flow guard where the product's read is not (UX4 RT-1).
_READ = [Tag("type", "read"), Tag(EFFECT_TAG_KEY, "read")]
_WRITE = [Tag("risk", "high"), Tag("category", "write"), Tag(EFFECT_TAG_KEY, "write")]
# The product's own `fetch_url` is a read whose request leaves the organization (FG2); the fake records the request so
# a test sees what left the run.
_EGRESS = [Tag("risk", "medium"), Tag("category", "network"), Tag(EFFECT_TAG_KEY, "read"), Tag(SINK_TAG_KEY, "external")]

# op -> whether it sends data outside the run (a write or an outbound request)
WRITE_OPS = frozenset(
    {
        "jira.create_issue",
        "jira.add_comment",
        "slack.send_message",
        "gmail.send_email",
        "drive.share_file",
    }
)
EGRESS_OPS = frozenset({"web.fetch"})


class _Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Doc(_Fixture):
    id: str
    title: str
    text: str


class Ticket(_Fixture):
    key: str
    summary: str
    status: str = "Open"
    owner: str = ""
    description: str = ""


class SlackMessage(_Fixture):
    user: str
    text: str


class Email(_Fixture):
    id: str
    sender: str
    to: str = f"me@{ORG_DOMAIN}"
    subject: str
    body: str


class DriveFile(_Fixture):
    id: str
    name: str
    text: str


class WorldFixtures(_Fixture):
    knowledge: list[Doc] = []
    tickets: list[Ticket] = []
    slack: dict[str, list[SlackMessage]] = {}
    emails: list[Email] = []
    files: list[DriveFile] = []
    web: dict[str, str] = {}


@dataclass
class ReplayWorld:
    fixtures: WorldFixtures = field(default_factory=WorldFixtures)
    saas: FakeSaaS = field(default_factory=FakeSaaS)

    def commit(self, op: str, payload: dict[str, Any]) -> str:
        return self.saas.call(op, payload).receipt_id

    def effects(self) -> list[RecordedEffect]:
        return [
            RecordedEffect(op=e.op, payload=dict(e.payload)) for e in self.saas.effects
        ]


_WORLD: ContextVar[ReplayWorld | None] = ContextVar("replay_world", default=None)


def current_world() -> ReplayWorld:
    world = _WORLD.get()
    if world is None:
        raise RuntimeError(
            "no ReplayWorld is active; drive the run through legacy_driver"
        )
    return world


def activate(world: ReplayWorld) -> object:
    return _WORLD.set(world)


def deactivate(token: object) -> None:
    _WORLD.reset(token)  # type: ignore[arg-type]


def _p(name: str, description: str, *, required: bool = True) -> ToolParameter:
    return ToolParameter(
        name=name, type=ParameterType.STRING, description=description, required=required
    )


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


_words = words


def search_tickets(tickets: Sequence[Ticket], query: str) -> list[Ticket]:
    """The fake Jira's search: tickets sharing a word with the query (key, summary, description), else all."""
    return matching(tickets, query, lambda t: f"{t.key} {t.summary} {t.description}")


def rank_knowledge(docs: list[Doc], query: str, limit: int = 5) -> list[Doc]:
    """Docs sharing words with ``query``, most shared first (ties keep fixture
    order): the stand-in for vector search over a seeded knowledge fixture."""
    wanted = _words(query)
    scored = [
        (len(wanted & _words(d.title + " " + d.text)), i, d) for i, d in enumerate(docs)
    ]
    hits = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
    return [d for _, _, d in hits[:limit]]


class _Toolset:
    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self._state = state


class Knowledge(_Toolset):
    @tool(
        path="/tools/knowledge/search",
        short_description="Search company knowledge",
        description="Search the organisation's indexed documents. Returns matching documents with their text.",
        parameters=[_p("query", "What to search for")],
        tags=_READ,
    )
    async def search(self, query: str) -> str:
        docs = current_world().fixtures.knowledge
        wanted = _words(query)
        hits = [d for d in docs if wanted & _words(d.title + " " + d.text)] or list(
            docs
        )
        return _dump({"results": [d.model_dump() for d in hits[:5]]})

    @tool(
        path="/tools/knowledge/read_document",
        short_description="Read a document",
        description="Read one indexed document in full by id.",
        parameters=[_p("doc_id", "Document id")],
        tags=_READ,
    )
    async def read_document(self, doc_id: str) -> str:
        for doc in current_world().fixtures.knowledge:
            if doc.id == doc_id:
                return _dump(doc.model_dump())
        raise ValueError(f"Document {doc_id} does not exist")


class Jira(_Toolset):
    @tool(
        path="/tools/jira/search_issues",
        short_description="Search Jira issues",
        description="Search Jira issues with a text query or JQL.",
        parameters=[_p("query", "Text or JQL")],
        tags=_READ,
    )
    async def search_issues(self, query: str) -> str:
        return _dump({"issues": [t.model_dump() for t in search_tickets(current_world().fixtures.tickets, query)]})

    @tool(
        path="/tools/jira/get_issue",
        short_description="Get a Jira issue",
        description="Fetch one Jira issue by key, including its description.",
        parameters=[_p("issue_key", "Issue key, e.g. PROJ-1")],
        tags=_READ,
    )
    async def get_issue(self, issue_key: str) -> str:
        for t in current_world().fixtures.tickets:
            if t.key == issue_key:
                return _dump(t.model_dump())
        raise ValueError(f"Issue {issue_key} does not exist")

    @tool(
        path="/tools/jira/create_issue",
        short_description="Create a Jira issue",
        description="Create a Jira issue. Visible to the whole project and notifies watchers.",
        parameters=[
            _p("project", "Project key"),
            _p("summary", "Title"),
            _p("description", "Body", required=False),
            # The real tool's (`app/agents/actions/jira/jira.py`): a request that names a type or a
            # priority has somewhere to put it (P4.9 JF-1).
            _p("issue_type_name", "The name of the Jira issue type", required=False),
            _p("priority_name", "The name of the Jira priority", required=False),
        ],
        tags=_WRITE,
    )
    async def create_issue(
        self,
        project: str,
        summary: str,
        description: str = "",
        issue_type_name: str = "",
        priority_name: str = "",
    ) -> str:
        receipt = current_world().commit(
            "jira.create_issue",
            {
                "project": project,
                "summary": summary,
                "description": description,
                "issue_type_name": issue_type_name,
                "priority_name": priority_name,
            },
        )
        return _dump({"created": f"{project}-{receipt.rsplit('-', 1)[1]}"})

    @tool(
        path="/tools/jira/add_comment",
        short_description="Comment on a Jira issue",
        description="Add a comment to a Jira issue. Watchers are notified.",
        parameters=[_p("issue_key", "Issue key"), _p("body", "Comment text")],
        tags=_WRITE,
    )
    async def add_comment(self, issue_key: str, body: str) -> str:
        receipt = current_world().commit(
            "jira.add_comment", {"issue_key": issue_key, "body": body}
        )
        return _dump({"comment": receipt})


class Slack(_Toolset):
    @tool(
        path="/tools/slack/read_channel",
        short_description="Read a Slack channel",
        description="Read recent messages from a Slack channel.",
        parameters=[_p("channel", "Channel name, e.g. #general")],
        tags=_READ,
    )
    async def read_channel(self, channel: str) -> str:
        messages = current_world().fixtures.slack.get(channel.lstrip("#"), [])
        return _dump(
            {"channel": channel, "messages": [m.model_dump() for m in messages]}
        )

    @tool(
        path="/tools/slack/send_message",
        short_description="Post a Slack message",
        description="Post a message to a Slack channel or user. Cannot be unsent.",
        parameters=[_p("channel", "Channel or @user"), _p("text", "Message text")],
        tags=_WRITE,
    )
    async def send_message(self, channel: str, text: str) -> str:
        receipt = current_world().commit(
            "slack.send_message", {"channel": channel, "text": text}
        )
        return _dump({"ok": True, "ts": receipt})


class Gmail(_Toolset):
    @tool(
        path="/tools/gmail/search_emails",
        short_description="Search email",
        description="Search the user's mailbox.",
        parameters=[_p("query", "Search text")],
        tags=_READ,
    )
    async def search_emails(self, query: str) -> str:
        emails = current_world().fixtures.emails
        wanted = _words(query)
        hits = [
            e for e in emails if wanted & _words(f"{e.subject} {e.body} {e.sender}")
        ] or list(emails)
        return _dump(
            {
                "emails": [
                    {"id": e.id, "from": e.sender, "subject": e.subject} for e in hits
                ]
            }
        )

    @tool(
        path="/tools/gmail/read_email",
        short_description="Read an email",
        description="Read one email by id, including its body.",
        parameters=[_p("message_id", "Email id")],
        tags=_READ,
    )
    async def read_email(self, message_id: str) -> str:
        for e in current_world().fixtures.emails:
            if e.id == message_id:
                return _dump(
                    {
                        "id": e.id,
                        "from": e.sender,
                        "to": e.to,
                        "subject": e.subject,
                        "body": e.body,
                    }
                )
        raise ValueError(f"Email {message_id} does not exist")

    @tool(
        path="/tools/gmail/send_email",
        short_description="Send an email",
        description="Send an email from the user's account. Cannot be unsent.",
        parameters=[
            _p("to", "Recipient address"),
            _p("subject", "Subject"),
            _p("body", "Body"),
        ],
        tags=_WRITE,
    )
    async def send_email(self, to: str, subject: str, body: str) -> str:
        receipt = current_world().commit(
            "gmail.send_email", {"to": to, "subject": subject, "body": body}
        )
        return _dump({"sent": receipt})


class Drive(_Toolset):
    @tool(
        path="/tools/drive/read_file",
        short_description="Read a Drive file",
        description="Read a file from the user's Drive by id.",
        parameters=[_p("file_id", "File id")],
        tags=_READ,
    )
    async def read_file(self, file_id: str) -> str:
        for f in current_world().fixtures.files:
            if f.id == file_id:
                return _dump(f.model_dump())
        raise ValueError(f"File {file_id} does not exist")

    @tool(
        path="/tools/drive/share_file",
        short_description="Share a Drive file",
        description="Share a Drive file with an email address (grants read access).",
        parameters=[_p("file_id", "File id"), _p("email", "Who to share with")],
        tags=_WRITE,
    )
    async def share_file(self, file_id: str, email: str) -> str:
        receipt = current_world().commit(
            "drive.share_file", {"file_id": file_id, "email": email}
        )
        return _dump({"shared": receipt})


class Web(_Toolset):
    @tool(
        path="/tools/web/fetch_url",
        short_description="Fetch a web page",
        description="Fetch a URL over HTTP(S) and return its text.",
        parameters=[_p("url", "Absolute URL")],
        tags=_EGRESS,
    )
    async def fetch_url(self, url: str) -> str:
        world = current_world()
        world.commit("web.fetch", {"url": url, "host": urlsplit(url).hostname or ""})
        page = world.fixtures.web.get(url)
        if page is None:
            raise ValueError(f"404 Not Found: {url}")
        return page


TOOLSETS: dict[str, tuple[type[_Toolset], str]] = {
    "knowledge": (Knowledge, "Company knowledge base"),
    "jira": (Jira, "Jira issues"),
    "slack": (Slack, "Slack messages"),
    "gmail": (Gmail, "Gmail mailbox"),
    "drive": (Drive, "Google Drive files"),
    "web": (Web, "Web access"),
}


__all__ = [
    "EGRESS_OPS",
    "ORG_DOMAIN",
    "TOOLSETS",
    "WRITE_OPS",
    "Doc",
    "DriveFile",
    "Email",
    "ReplayWorld",
    "SlackMessage",
    "Ticket",
    "WorldFixtures",
    "activate",
    "current_world",
    "deactivate",
    "rank_knowledge",
    "search_tickets",
    "words",
]
