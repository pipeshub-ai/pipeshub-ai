"""Every external toolset tool says what it does to data in its `type` tag: the tag sets the tool's
starting approval rule (`tool_approvals`), so a wrong one denies a harmless tool by default or
lets a deleting one run without asking."""

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


def test_deleting_tools_and_only_they_are_tagged_as_deleting() -> None:
    mismatched = [
        path for path, type_tag in _TOOLS.items()
        if deletes_by_name(path.rsplit("/", 1)[-1]) != (type_tag in ("delete", "destructive"))
    ]
    assert mismatched == []


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
    ("/tools/slack/send_message", "write"),
    ("/tools/jira/search_issues", "read"),
])
def test_a_tools_kind(path: str, kind: str) -> None:
    assert _kind(path) == kind
