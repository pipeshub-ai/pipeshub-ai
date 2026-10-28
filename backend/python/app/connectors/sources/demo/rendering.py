"""The text of each demo record, as the system it imitates shows its people.

A record's text carries the people its source shows on the page and no
others: a pull request its author and reviewers, a Jira issue its reporter
and assignee, a mail its From/To/Cc headers. A Drive document, Confluence
page or Zendesk ticket has no author line in its content, so neither does the
demo's; who owns, wrote or works it is on the record's fields, for the
knowledge graph to answer.

Shared by the connector and the knowledge-base harness, so a question tuned
against uploads reads the same words the connector syncs. Imports nothing
from the app beyond the demo's own pure modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from app.connectors.sources.demo.fixture_loader import split_threads
    from app.connectors.sources.demo.systems import SYSTEMS, people_in
except ImportError:  # run as a script by the harness, with this folder on the path
    from fixture_loader import split_threads  # type: ignore[no-redef]
    from systems import SYSTEMS, people_in  # type: ignore[no-redef]

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    Lines = Callable[["_Context", Mapping[str, Any]], list[str]]


class _Context:
    """Name and address lookups for one fixture."""

    def __init__(self, fx: Mapping[str, Any]) -> None:
        self.people = {p["id"]: p for p in fx["people"]}
        self.orgs = {o["id"]: o for o in fx.get("organizations", [])}

    def names(self, rec: Mapping, field: str) -> str:
        return ", ".join(self.people[pid]["name"] for pid in people_in(rec, field))

    def addresses(self, rec: Mapping, field: str) -> str:
        return ", ".join(
            f"{self.people[pid]['name']} <{self.people[pid]['email']}>" for pid in people_in(rec, field)
        )


def _date(rec: Mapping) -> str:
    return str(rec["created"])[:10]


def _optional(label: str, value: str) -> list[str]:
    return [f"**{label}:** {value}"] if value else []


def _authored(ctx: _Context, rec: Mapping) -> list[str]:
    return [f"**Author:** {ctx.names(rec, 'author')} · **Date:** {_date(rec)}"]


def _dated(ctx: _Context, rec: Mapping) -> list[str]:
    return [f"**Date:** {_date(rec)}"]


def _pull_request(ctx: _Context, rec: Mapping) -> list[str]:
    return [
        *_authored(ctx, rec),
        *_optional("Assignees", ctx.names(rec, "assignees")),
        *_optional("Reviewers", ctx.names(rec, "reviewers")),
    ]


def _github_issue(ctx: _Context, rec: Mapping) -> list[str]:
    return [*_authored(ctx, rec), *_optional("Assignee", ctx.names(rec, "assignee"))]


def _jira_issue(ctx: _Context, rec: Mapping) -> list[str]:
    # Jira's reporter is the creator unless someone else is named.
    reporter = ctx.names(rec, "reporter") or ctx.names(rec, "author")
    assignee = ctx.names(rec, "assignee")
    return [" · ".join([f"**Reporter:** {reporter}", *_optional("Assignee", assignee), f"**Created:** {_date(rec)}"])]


def _mail(ctx: _Context, rec: Mapping) -> list[str]:
    return [
        f"**From:** {ctx.addresses(rec, 'from')}",
        *_optional("To", ctx.addresses(rec, "to")),
        *_optional("Cc", ctx.addresses(rec, "cc")),
        f"**Date:** {_date(rec)}",
        f"**Subject:** {rec['title']}",
    ]


def _account_name(ctx: _Context, container: Mapping) -> str:
    return ctx.orgs[container["organization"]]["name"]


# Per (system, type): the lines under the title that name people. A system
# whose content names nobody shows only the date.
_PEOPLE_LINES: dict[tuple[str, str], Lines] = {
    ("GITHUB", "PULL_REQUEST"): _pull_request,
    ("GITHUB", "TICKET"): _github_issue,
    ("GITHUB", "COMMENT"): _authored,
    ("JIRA", "TICKET"): _jira_issue,
    ("JIRA", "COMMENT"): _authored,
    ("SERVICENOW", "TICKET"): _authored,
    # A Zendesk ticket's content is its description and public comments; who
    # asked and who works it are fields, not text.
    ("ZENDESK", "TICKET"): _dated,
    ("SLACK", "MESSAGE"): _authored,
    ("DRIVE", "FILE"): _dated,
    ("CONFLUENCE", "PAGE"): _dated,
    ("GMAIL", "MAIL"): _mail,
}


def _salesforce_lines(ctx: _Context, rec: Mapping, container: Mapping) -> list[str]:
    parts = [f"**Account:** {_account_name(ctx, container)}", f"**Owner:** {ctx.names(rec, 'owner')}"]
    if rec["type"] == "CASE":
        parts += _optional("Contact", ctx.names(rec, "reporter"))
        parts.append(f"**Opened:** {_date(rec)}")
    return [" · ".join(parts)]


def _render_record(ctx: _Context, rec: Mapping, container: Mapping) -> str:
    system = container["system"]
    if system == "KB":
        # An uploaded file is only its own content.
        return f"# {rec['title']}\n\n{rec['body'].rstrip()}\n"
    spec = SYSTEMS[system]
    head = [
        f"# {rec['title']}",
        "",
        f"**System:** {spec.label} · **Type:** {spec.shapes[rec['type']].label} · **In:** {container['name']}",
    ]
    if system == "SALESFORCE":
        head += _salesforce_lines(ctx, rec, container)
    else:
        head += _PEOPLE_LINES[(system, rec["type"])](ctx, rec)
    return "\n".join([*head, ""]) + rec["body"].rstrip() + "\n"


def _render_thread(ctx: _Context, thread: Mapping, messages: list[Mapping], container: Mapping) -> str:
    lines = [f"# {thread['title']}", "", f"**System:** Slack · **Type:** Thread · **In:** {container['name']}", ""]
    for m in messages:
        lines.append(f"**{ctx.names(m, 'author')}** · {str(m['created'])[:16].replace('T', ' ')}")
        lines.append(m["body"].rstrip())
        lines.append("")
    return "\n".join(lines)


def render_bodies(fx: Mapping[str, Any]) -> dict[str, str]:
    """Markdown for every record the connector emits, keyed by its external id."""
    ctx = _Context(fx)
    containers = {c["id"]: c for c in fx["containers"]}
    threads = {t["id"]: t for t in fx.get("threads", [])}
    singles, by_thread = split_threads(fx)
    out = {rec["id"]: _render_record(ctx, rec, containers[rec["container"]]) for rec in singles}
    for tid, messages in by_thread.items():
        out[tid] = _render_thread(ctx, threads[tid], messages, containers[threads[tid]["container"]])
    return out
