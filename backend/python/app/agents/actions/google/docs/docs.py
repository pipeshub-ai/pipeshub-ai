import json
import logging
from typing import Any, Optional

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.util.google_api_errors import GoogleToolWording, google_error_message
from app.agents.actions.util.tool_summaries import args_template, confirmation, entity_summary
from app.connectors.core.constants import IconPaths
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)
from app.connectors.core.registry.types import DocumentationLink
from app.connectors.sources.google.common.scopes import GOOGLE_TOOLSET_SCOPES
from app.sources.client.google.google import GoogleClient
from app.sources.client.google.serialized_execute import SerializedExecute

logger = logging.getLogger(__name__)

# Text longer than this is cut from get_document's answer; the rest stays readable in the doc.
_MAX_DOCUMENT_TEXT_CHARS = 60_000

_DOCS_WORDING = GoogleToolWording(
    product="Google Docs",
    toolset="Docs",
    access="Google Docs access",
    not_found=(
        "that document. Check the document id (the part of the link after /document/d/), "
        "and that the connected account can open it."
    ),
    gone="that document has been deleted.",
)


class _DocsInputError(ValueError):
    """Bad tool arguments; the message says what to change and is safe to show the agent."""


def _failure(error: Exception, action: str) -> tuple[bool, str]:
    logger.error("Failed to %s: %s", action, error)
    message = str(error) if isinstance(error, _DocsInputError) else google_error_message(error, action, _DOCS_WORDING)
    return False, json.dumps({"error": message})


def _document_url(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{document_id}/edit"


def _body_text(body: dict[str, Any]) -> str:
    """A body's text in reading order; a table is one line per row, its cells split by " | "."""
    parts: list[str] = []

    def walk(content: list[dict[str, Any]]) -> None:
        for element in content:
            parts.extend(
                (run.get("textRun") or {}).get("content", "")
                for run in (element.get("paragraph") or {}).get("elements", [])
            )
            for row in (element.get("table") or {}).get("tableRows", []):
                for i, cell in enumerate(row.get("tableCells", [])):
                    if i:
                        parts.append(" | ")
                    # A cell's paragraphs end in a newline; inside a row that would split the row.
                    start = len(parts)
                    walk(cell.get("content", []))
                    parts[start:] = ["".join(parts[start:]).rstrip("\n")]
                parts.append("\n")

    walk(body.get("content", []))
    return "".join(parts)


def _tabs(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Every tab, child tabs included, in the order the document shows them.

    Read with includeTabsContent, which leaves ``body`` empty and puts each tab's text
    under ``tabs[].documentTab.body``.
    """
    found: list[dict[str, Any]] = []

    def walk(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            props = tab.get("tabProperties") or {}
            found.append({
                "tab_id": props.get("tabId", ""),
                "title": props.get("title", ""),
                "parent_tab_id": props.get("parentTabId"),
                "text": _body_text((tab.get("documentTab") or {}).get("body") or {}),
            })
            walk(tab.get("childTabs", []))

    walk(document.get("tabs", []))
    return found


def _capped_tabs(tabs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Tabs whose text fits in the reply, the last one cut if needed, and whether any was cut."""
    kept: list[dict[str, Any]] = []
    room = _MAX_DOCUMENT_TEXT_CHARS
    for tab in tabs:
        if room <= 0:
            return kept, True
        text = tab["text"]
        kept.append({**tab, "text": text[:room]})
        if len(text) > room:
            return kept, True
        room -= len(text)
    return kept, False


@ToolsetBuilder("Docs")\
    .in_group("Google Workspace")\
    .with_description("Google Docs integration for reading, creating and editing documents")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Docs",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            redirect_uri="toolsets/oauth/callback/docs",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=list(GOOGLE_TOOLSET_SCOPES["docs"]),
            ),
            token_access_type="offline",
            additional_params={
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true"
            },
            fields=[
                CommonFields.client_id("Google Cloud Console"),
                CommonFields.client_secret("Google Cloud Console")
            ],
            icon_path=IconPaths.connector_icon("docs"),
            app_group="Google Workspace",
            app_description="Docs OAuth application for agent integration"
        )
    ])\
    .configure(lambda builder: builder.with_icon(IconPaths.connector_icon("docs"))
        .add_documentation_link(DocumentationLink(
            "Google Docs API Setup",
            "https://developers.google.com/workspace/guides/auth-overview",
            "setup",
        ))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Documentation",
            "https://docs.pipeshub.com/toolsets/google-workspace/docs",
            "pipeshub",
        )))\
    .build_decorator()
class GoogleDocs:
    """Google Docs tools for agents, on a Docs v1 client built from the toolset's OAuth sign-in."""

    def __init__(self, client: GoogleClient) -> None:
        self.client = client
        self._serial = SerializedExecute()

    async def _execute(self, request: Any) -> dict[str, Any]:  # noqa: ANN401
        return await self._serial.run(request.execute)

    async def _batch_update(self, document_id: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        return await self._execute(
            self.client.documents().batchUpdate(documentId=document_id, body={"requests": requests})
        )

    @tool(
        path="/tools/docs/get_document",
        short_description="Read a Google Doc",
        description=(
            "Read a Google Doc: its title, a link, and the text of every tab (child tabs included), each "
            "labelled with its title and tab_id. Pass a tab_id to insert_text or replace_text to change one "
            "tab. Use the document id from its link (the part after /document/d/). For a file you only know "
            "by name, find it with Drive first."
        ),
        parameters=[
            ToolParameter(name="document_id", type=ParameterType.STRING, description="The document id", required=True),
        ],
        tags=[Tag(key="category", value="documents"), Tag(key="type", value="read")],
        args_summary=args_template("Reading Google Doc {document_id}", "document_id"),
        result_summary=entity_summary(lambda d: f"Read \"{d.get('title') or '?'}\"", path=()),
    )
    async def get_document(self, document_id: str) -> tuple[bool, str]:
        try:
            document = await self._execute(
                self.client.documents().get(documentId=document_id, includeTabsContent=True)
            )
            tabs, truncated = _capped_tabs(_tabs(document))
            return True, json.dumps({
                "document_id": document.get("documentId", document_id),
                "title": document.get("title", ""),
                "url": _document_url(document.get("documentId", document_id)),
                "revision_id": document.get("revisionId", ""),
                "tabs": tabs,
                "truncated": truncated,
            })
        except Exception as e:
            return _failure(e, "read the document")

    @tool(
        path="/tools/docs/create_document",
        short_description="Create a Google Doc",
        description="Create a new Google Doc in the connected account's Drive, optionally with starting text.",
        parameters=[
            ToolParameter(name="title", type=ParameterType.STRING, description="The document's title", required=True),
            ToolParameter(name="text", type=ParameterType.STRING, description="Text to put in the new document", required=False),
        ],
        tags=[Tag(key="category", value="documents"), Tag(key="type", value="write")],
        args_summary=args_template("Creating Google Doc \"{title}\"", "title"),
        result_summary=entity_summary(lambda d: f"Created \"{d.get('title') or '?'}\"", path=()),
    )
    async def create_document(self, title: str, text: Optional[str] = None) -> tuple[bool, str]:
        try:
            if not (title or "").strip():
                raise _DocsInputError("Give the new document a title.")
            document = await self._execute(self.client.documents().create(body={"title": title}))
        except Exception as e:
            return _failure(e, "create the document")
        document_id = document["documentId"]
        created = {"document_id": document_id, "title": document.get("title", title), "url": _document_url(document_id)}
        if text:
            try:
                await self._batch_update(document_id, [{"insertText": {"location": {"index": 1}, "text": text}}])
            except Exception as e:
                logger.error("Created document %s but could not add its text: %s", document_id, e)
                reason = google_error_message(e, "add the starting text", _DOCS_WORDING)
                return False, json.dumps({
                    **created,
                    "error": (
                        f"The document \"{created['title']}\" was created ({created['url']}), but its text "
                        f"was not added. {reason} Add the text with insert_text on document_id "
                        f"{document_id}; don't create another document."
                    ),
                })
        return True, json.dumps(created)

    @tool(
        path="/tools/docs/insert_text",
        short_description="Add text to a Google Doc",
        description=(
            "Insert text into a Google Doc. Without an index the text goes at the end of the tab; index 1 is "
            "its very start. Without a tab_id it goes into the document's first tab; pass a tab_id from "
            "get_document to write into another tab."
        ),
        parameters=[
            ToolParameter(name="document_id", type=ParameterType.STRING, description="The document id", required=True),
            ToolParameter(name="text", type=ParameterType.STRING, description="The text to insert", required=True),
            ToolParameter(name="index", type=ParameterType.INTEGER, description="Where to insert (1 is the start); leave out to add at the end", required=False),
            ToolParameter(name="tab_id", type=ParameterType.STRING, description="The tab to write into, from get_document; leave out for the first tab", required=False),
        ],
        tags=[Tag(key="category", value="documents"), Tag(key="type", value="write")],
        args_summary=args_template("Adding text to Google Doc {document_id}", "document_id"),
        result_summary=confirmation("Text added to Google Doc {document_id}", "document_id"),
    )
    async def insert_text(
        self, document_id: str, text: str, index: Optional[int] = None, tab_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            if not text:
                raise _DocsInputError("There is no text to insert.")
            if index is not None and index < 1:
                raise _DocsInputError("The index must be 1 or more; 1 is the start of the document.")
            tab = {"tabId": tab_id} if tab_id else {}
            location = {"location": {"index": index, **tab}} if index is not None else {"endOfSegmentLocation": tab}
            await self._batch_update(document_id, [{"insertText": {**location, "text": text}}])
            return True, json.dumps({"document_id": document_id, "url": _document_url(document_id), "inserted_characters": len(text)})
        except Exception as e:
            return _failure(e, "add the text")

    @tool(
        path="/tools/docs/replace_text",
        short_description="Replace text in a Google Doc",
        description=(
            "Replace every occurrence of some text in a Google Doc, and report how many were changed. "
            "Without a tab_id it replaces in every tab, child tabs included; pass a tab_id from get_document "
            "to change only that tab."
        ),
        parameters=[
            ToolParameter(name="document_id", type=ParameterType.STRING, description="The document id", required=True),
            ToolParameter(name="old_text", type=ParameterType.STRING, description="The text to find", required=True),
            ToolParameter(name="new_text", type=ParameterType.STRING, description="The text to put in its place (may be empty)", required=True),
            ToolParameter(name="match_case", type=ParameterType.BOOLEAN, description="Whether letter case must match", required=False, default=True),
            ToolParameter(name="tab_id", type=ParameterType.STRING, description="Only replace in this tab, from get_document; leave out for every tab", required=False),
        ],
        tags=[Tag(key="category", value="documents"), Tag(key="type", value="write")],
        args_summary=args_template("Replacing \"{old_text}\" in Google Doc {document_id}", "old_text", "document_id"),
        result_summary=entity_summary(lambda d: f"Replaced {d.get('occurrences_changed', 0)} occurrence(s)", path=()),
    )
    async def replace_text(
        self, document_id: str, old_text: str, new_text: str, *, match_case: bool = True, tab_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        try:
            if not old_text:
                raise _DocsInputError("Say which text to replace.")
            replace: dict[str, Any] = {
                "containsText": {"text": old_text, "matchCase": bool(match_case)},
                "replaceText": new_text or "",
            }
            if tab_id:
                replace["tabsCriteria"] = {"tabIds": [tab_id]}
            response = await self._batch_update(document_id, [{"replaceAllText": replace}])
            replies = response.get("replies") or [{}]
            changed = (replies[0].get("replaceAllText") or {}).get("occurrencesChanged", 0)
            return True, json.dumps({"document_id": document_id, "url": _document_url(document_id), "occurrences_changed": changed})
        except Exception as e:
            return _failure(e, "replace the text")

    @tool(
        path="/tools/docs/batch_update_document",
        short_description="Apply Google Docs API edits to a document",
        description=(
            "Apply a list of Google Docs API batchUpdate requests to a document in one step, for edits the "
            "other Docs tools don't cover (styles, tables, headings, deleting a range). Each item is one "
            "request object, for example {\"updateTextStyle\": {...}}. All apply together or none do."
        ),
        parameters=[
            ToolParameter(name="document_id", type=ParameterType.STRING, description="The document id", required=True),
            ToolParameter(
                name="requests", type=ParameterType.ARRAY, required=True, items={"type": "object"},
                description="Google Docs API batchUpdate request objects, applied in order",
            ),
        ],
        tags=[Tag(key="category", value="documents"), Tag(key="type", value="write")],
        args_summary=args_template("Editing Google Doc {document_id}", "document_id"),
        result_summary=confirmation("Google Doc {document_id} updated", "document_id"),
    )
    async def batch_update_document(self, document_id: str, requests: list[dict[str, Any]]) -> tuple[bool, str]:
        try:
            if not requests or not all(isinstance(r, dict) and r for r in requests):
                raise _DocsInputError("Pass at least one Google Docs API request object, for example {\"insertText\": {...}}.")
            response = await self._batch_update(document_id, requests)
            return True, json.dumps({
                "document_id": document_id,
                "url": _document_url(document_id),
                "applied_requests": len(requests),
                "replies": response.get("replies", []),
            })
        except Exception as e:
            return _failure(e, "edit the document")
