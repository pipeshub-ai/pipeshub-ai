"""Every external toolset tool says what it does to data in its `type` tag: the tag sets the tool's
starting approval rule (`tool_approvals`), so a wrong one makes a harmless tool ask, or lets a
deleting or irreversible one run without asking."""

import importlib
import inspect

import pytest

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR
from app.agents.mcp.tool_kind import deletes_by_name, toolset_tool_kind
from app.agents.registry.toolset_registry import STANDARD_TOOLSET_MODULES


def _external_tools() -> dict[str, str | None]:
    """Each external toolset tool's path, and its `type` tag."""
    tools: dict[str, str | None] = {}
    for module_path in STANDARD_TOOLSET_MODULES:
        module = importlib.import_module(module_path)
        for _name, cls in inspect.getmembers(module, inspect.isclass):
            metadata = getattr(cls, "_toolset_metadata", None)
            if metadata is None or cls.__module__ != module_path or metadata.get("isInternal"):
                continue
            for attr in vars(cls).values():
                meta = getattr(getattr(attr, "__func__", attr), TOOL_META_ATTR, None)
                if meta is not None:
                    tools[meta.path] = next((tag.value for tag in meta.tags if tag.key == "type"), None)
    return tools


_TOOLS = _external_tools()


def _kind(path: str) -> str:
    return toolset_tool_kind(path.rsplit("/", 1)[-1], _TOOLS[path])


def test_there_are_external_tools_to_check() -> None:
    assert len(_TOOLS) > 300


def test_every_tool_has_a_type_tag() -> None:
    assert [path for path, type_tag in _TOOLS.items() if type_tag is None] == []


# Destructive though their names don't say delete: what they do can't be undone, so they ask first.
# Kept here rather than as words in `tool_kind.py`, whose list also classifies third-party MCP tools.
_IRREVERSIBLE_WITHOUT_A_DELETING_NAME = {
    "/tools/sheets/clear_values",  # the cleared values can't be got back
    "/tools/gmail/send_email",  # sent email can't be unsent
    "/tools/gmail/reply",
    "/tools/outlook/send_email",
    "/tools/outlook/reply_to_message",
    "/tools/outlook/reply_all_to_message",
    "/tools/outlook/forward_message",
    "/tools/calendar/create_calendar_event",  # emails the attendees
    "/tools/calendar/update_calendar_event",  # emails the attendees
    "/tools/calendar/create_meet_link",  # emails the attendees
    "/tools/outlook/create_calendar_event",  # emails the attendees
    "/tools/outlook/update_calendar_event",  # emails the attendees
}


def test_deleting_tools_and_only_they_are_tagged_as_deleting() -> None:
    mismatched = [
        path for path, type_tag in _TOOLS.items()
        if (deletes_by_name(path.rsplit("/", 1)[-1]) or path in _IRREVERSIBLE_WITHOUT_A_DELETING_NAME)
        != (type_tag in ("delete", "destructive"))
    ]
    assert mismatched == []


def test_the_irreversible_list_names_real_tools() -> None:
    assert _IRREVERSIBLE_WITHOUT_A_DELETING_NAME <= set(_TOOLS)


@pytest.mark.parametrize(("path", "kind"), [
    # Were tagged destructive, which would have denied them by default.
    ("/tools/jira/create_issue", "write"),
    ("/tools/jira/update_issue", "write"),
    # Only read the fields an issue needs.
    ("/tools/jira/get_create_issue_fields", "read"),
    ("/tools/jira_data_center/get_create_issue_fields", "read"),
    ("/tools/slack/delete_message", "destructive"),
    ("/tools/zoom/delete_meeting", "destructive"),
    # Saves the attachment as a new PipesHub record, though Outlook is unchanged.
    ("/tools/outlook/stage_attachment_to_blob", "write"),
    # Runs any SQL statement.
    ("/tools/mariadb/execute_query", "write"),
    # Posting a message isn't sending email: a write.
    ("/tools/slack/send_message", "write"),
    ("/tools/jira/search_issues", "read"),
    # Can't be undone, so they ask first though their names don't say delete.
    ("/tools/sheets/clear_values", "destructive"),
    ("/tools/gmail/send_email", "destructive"),
    ("/tools/outlook/forward_message", "destructive"),
])
def test_a_tools_kind(path: str, kind: str) -> None:
    assert _kind(path) == kind
