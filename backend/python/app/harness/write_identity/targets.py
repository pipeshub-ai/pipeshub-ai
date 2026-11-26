"""Which connector writes carry the AI disclosure (OWD-18), in which argument and markup.

Every tool a durable run can reach whose effect is an external write (``external_write``) is either named here with
the argument that holds the text people will read, or exempt with its reason. ``test_wp23_write_identity`` holds the
two lists to the real catalog: a new connector write fails until it is placed in one of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from harness_core.contracts.generated.enums import WriteAction

# How the text the disclosure joins is read by the target: plain text, Markdown, HTML (or a storage format that is
# HTML), Slack's mrkdwn.
Markup = Literal["text", "markdown", "html", "slack"]


@dataclass(frozen=True, slots=True)
class DisclosureTarget:
    action: WriteAction
    # The argument holding the text people read.
    field: str
    markup: Markup = "text"
    # A create without the field still gets the disclosure as the field's whole text; an update that leaves the field
    # alone changes nothing.
    creates: bool = True
    # An argument that switches the field's markup (``body_type: HTML``, ``is_rich_text: true``) and the markup it
    # switches to.
    markup_switch: tuple[str, Markup] | None = None


def _t(action: WriteAction, field: str, markup: Markup = "text", *, creates: bool = True,
       switch: tuple[str, Markup] | None = None) -> DisclosureTarget:
    return DisclosureTarget(action, field, markup, creates, switch)


# (toolset, tool) as the tool's path ends: ``/tools/<toolset>/<tool>``.
DISCLOSED: Final[dict[tuple[str, str], DisclosureTarget]] = {
    # Chat messages.
    ("slack", "send_message"): _t("message", "message", "slack"),
    ("slack", "send_direct_message"): _t("message", "message", "slack"),
    ("slack", "reply_to_message"): _t("message", "message", "slack"),
    ("slack", "schedule_message"): _t("message", "message", "slack"),
    ("slack", "send_message_to_multiple_channels"): _t("message", "message", "slack"),
    ("slack", "send_message_with_mentions"): _t("message", "message", "slack"),
    ("slack", "update_message"): _t("message", "text", "slack", creates=False),
    ("slack", "upload_file_to_channel"): _t("message", "initial_comment", "slack"),
    ("teams", "send_channel_message"): _t("message", "message"),
    ("teams", "send_user_message"): _t("message", "message"),
    ("teams", "reply_to_message"): _t("message", "message"),
    ("teams", "send_message_to_multiple_channels"): _t("message", "message"),
    ("teams", "update_message"): _t("message", "message", creates=False),
    ("salesforce", "post_chatter_comment"): _t("message", "text", switch=("is_rich_text", "markdown")),
    ("salesforce", "post_chatter_to_record"): _t("message", "text", switch=("is_rich_text", "markdown")),
    # Email.
    ("gmail", "send_email"): _t("email", "mail_body"),
    ("gmail", "reply"): _t("email", "mail_body"),
    ("outlook", "send_email"): _t("email", "body", switch=("body_type", "html")),
    ("outlook", "reply_to_message"): _t("email", "comment"),
    ("outlook", "reply_all_to_message"): _t("email", "comment"),
    ("outlook", "forward_message"): _t("email", "comment"),
    ("onedrive", "share_item"): _t("email", "message", creates=False),
    # Issues, tickets, cases and their comments.
    ("jira", "add_comment"): _t("ticket", "comment"),
    ("jira", "create_issue"): _t("ticket", "description"),
    ("jira", "update_issue"): _t("ticket", "description", creates=False),
    ("jira_data_center", "add_comment"): _t("ticket", "comment"),
    ("jira_data_center", "create_issue"): _t("ticket", "description"),
    ("jira_data_center", "update_issue"): _t("ticket", "description", creates=False),
    ("github", "create_issue"): _t("ticket", "body", "markdown"),
    ("github", "update_issue"): _t("ticket", "body", "markdown", creates=False),
    ("github", "create_issue_comment"): _t("ticket", "body", "markdown"),
    ("github", "create_pull_request"): _t("ticket", "body", "markdown"),
    ("github", "create_pull_request_review"): _t("ticket", "body", "markdown"),
    ("github", "create_pull_request_review_comment"): _t("ticket", "body", "markdown"),
    ("clickup", "create_task"): _t("ticket", "description"),
    ("clickup", "update_task"): _t("ticket", "description", creates=False),
    ("clickup", "create_task_comment"): _t("ticket", "comment_text"),
    ("salesforce", "create_case"): _t("ticket", "description"),
    ("salesforce", "create_task"): _t("ticket", "description"),
    ("confluence", "comment_on_page"): _t("ticket", "comment_text", "html"),
    ("confluence_data_center", "add_comment"): _t("ticket", "comment_text", "html"),
    # Pages and documents.
    ("confluence", "create_page"): _t("document", "page_content", "html"),
    ("confluence", "update_page"): _t("document", "page_content", "html", creates=False),
    ("confluence_data_center", "create_page"): _t("document", "page_content", "html"),
    ("confluence_data_center", "update_page"): _t("document", "page_content", "html", creates=False),
    ("clickup", "create_doc_page"): _t("document", "content", "markdown"),
    ("clickup", "update_doc_page"): _t("document", "content", "markdown", creates=False),
    ("docs", "create_document"): _t("document", "text"),
    ("sharepoint", "create_page"): _t("document", "content_html", "html"),
    ("sharepoint", "update_page"): _t("document", "content_html", "html", creates=False),
    ("sharepoint", "create_onenote_notebook"): _t("document", "page_content_html", "html"),
    ("onedrive", "create_onenote_page"): _t("document", "page_body_html", "html"),
    ("onedrive", "create_word_file"): _t("document", "content"),
    ("sharepoint", "create_word_document"): _t("document", "content_text"),
    # Calendar events and meetings people are invited to.
    ("calendar", "create_calendar_event"): _t("event", "event_description"),
    ("calendar", "update_calendar_event"): _t("event", "event_description", creates=False),
    ("calendar", "create_meet_link"): _t("event", "event_description"),
    ("outlook", "create_calendar_event"): _t("event", "body"),
    ("outlook", "update_calendar_event"): _t("event", "body", creates=False),
    ("teams", "create_event"): _t("event", "body"),
    ("teams", "edit_event"): _t("event", "description", creates=False),
    ("zoom", "create_meeting"): _t("event", "agenda"),
    ("zoom", "update_meeting"): _t("event", "agenda", creates=False),
    # Commits.
    ("github", "merge_pull_request"): _t("commit", "commit_message"),
}

_NO_TEXT = "writes no text anyone reads as the user's message (a reaction, pin, delete, move, rename, status, member)"
_RECORD = "writes structured data (a CRM, spreadsheet, list or access record), not a message attributed to a person"
_RUNS = "runs code or a query; nothing is posted as the user"
_EDIT = "edits inside an existing document at a position; a disclosure would land in the middle of its text"
_NAME = "names a container (a doc, space, channel, chat, team, folder, repository); it holds no message"
_READ = "reads only; it is classed as a write because it stages or lists data the provider holds"

# (toolset, tool) -> why its write carries no disclosure.
EXEMPT: Final[dict[tuple[str, str], str]] = {
    **dict.fromkeys(
        [
            ("slack", "add_reaction"), ("slack", "remove_reaction"), ("slack", "pin_message"),
            ("slack", "unpin_message"), ("slack", "delete_message"), ("slack", "set_user_status"),
            ("teams", "add_reaction"), ("teams", "remove_reaction"), ("teams", "add_member"),
            ("calendar", "delete_calendar_event"), ("outlook", "delete_calendar_event"),
            ("outlook", "delete_recurring_event_occurrence"), ("zoom", "delete_meeting"),
            ("github", "close_issue"), ("confluence", "update_page_title"), ("onedrive", "copy_item"),
            ("onedrive", "move_item"), ("onedrive", "rename_item"), ("sharepoint", "move_item"),
            ("clickup", "update_checklist_item"), ("lumos", "add_user_role"), ("lumos", "remove_user_role"),
            ("lumos", "cancel_access_request"), ("lumos", "delete_access_policy"),
            ("lumos", "delete_requestable_permission"),
            # Main's attachment tools upload files the user shared; inline display links them, it writes no text.
            ("confluence", "add_attachments"), ("confluence_data_center", "add_attachments"),
            ("jira", "add_attachments"), ("jira_data_center", "add_attachments"),
        ],
        _NO_TEXT,
    ),
    **dict.fromkeys(
        [
            ("salesforce", "add_product_to_opportunity"), ("salesforce", "attach_file_to_record"),
            ("salesforce", "create_account"), ("salesforce", "create_contact"), ("salesforce", "create_lead"),
            ("salesforce", "create_opportunity"), ("salesforce", "create_pricebook_entry"),
            ("salesforce", "create_product"), ("salesforce", "create_record"), ("salesforce", "update_record"),
            ("salesforce", "upload_file_to_salesforce"), ("sheets", "append_values"), ("sheets", "clear_values"),
            ("sheets", "update_values"), ("sheets", "create_spreadsheet"), ("clickup", "create_checklist"),
            ("clickup", "create_checklist_item"), ("clickup", "create_list"), ("clickup", "update_list"),
            ("lumos", "create_access_policy"), ("lumos", "update_access_policy"),
            ("lumos", "create_access_request"), ("lumos", "create_requestable_permission"),
            ("lumos", "update_requestable_permission"),
        ],
        _RECORD,
    ),
    **dict.fromkeys(
        [
            ("coding_sandbox", "execute_python"), ("coding_sandbox", "execute_typescript"),
            ("coding_sandbox", "install_packages"), ("database_sandbox", "execute_postgresql"),
            ("database_sandbox", "execute_sqlite"), ("mariadb", "execute_query"), ("redshift", "execute_query"),
            ("salesforce", "soql_query"), ("clickup", "search_tasks"),
        ],
        _RUNS,
    ),
    **dict.fromkeys(
        [("docs", "batch_update_document"), ("docs", "insert_text"), ("docs", "replace_text")],
        _EDIT,
    ),
    **dict.fromkeys(
        [
            ("clickup", "create_doc"), ("clickup", "create_folder"), ("clickup", "create_space"),
            ("teams", "create_channel"), ("teams", "update_channel"), ("teams", "create_chat"),
            ("teams", "create_team"), ("teams", "create_channel_meeting"), ("onedrive", "create_folder"),
            ("onedrive", "create_onenote_section"), ("sharepoint", "create_folder"),
            ("github", "create_repository"),
        ],
        _NAME,
    ),
    **dict.fromkeys(
        [
            ("slack", "get_dm_history"), ("outlook", "stage_attachment_to_blob"),
            ("confluence", "download_attachment"),
        ],
        _READ,
    ),
}

# MCP servers are not PipesHub connectors: no argument is known to hold text people read.
EXEMPT_PREFIXES: Final[dict[str, str]] = {
    "/mcp/": "an MCP server's tool: PipesHub does not know which argument, if any, people read",
}


def target_of(tool_path: str) -> DisclosureTarget | None:
    """The disclosure a tool's call carries, by its path (``/tools/<toolset>/<tool>``); ``None`` for any other."""
    parts = tool_path.rstrip("/").split("/")
    if len(parts) < 2:
        return None
    return DISCLOSED.get((parts[-2], parts[-1]))


__all__ = ["DISCLOSED", "EXEMPT", "EXEMPT_PREFIXES", "DisclosureTarget", "Markup", "target_of"]
