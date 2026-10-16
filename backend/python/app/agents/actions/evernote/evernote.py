import asyncio
import json
import logging
import threading
from collections.abc import Coroutine
from html import escape
from typing import Any

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.sources.client.evernote.evernote import EvernoteClient, EvernoteResponse
from app.sources.external.evernote.evernote import EvernoteDataSource

logger = logging.getLogger(__name__)

DEFAULT_SEARCH_RESULTS = 20
# findNotesMetadata returns at most 250 notes per call.
MAX_SEARCH_RESULTS = 250

_ENML_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
)


def _enml(content: str) -> str:
    """Note content as ENML: ENML is kept as given, plain text is escaped and wrapped."""
    if "<en-note" in content:
        return content
    return f"{_ENML_HEADER}<en-note>{escape(content, quote=False)}</en-note>"


def _json_default(value: object) -> object:
    # Thrift binary fields (contentHash, bodyHash) are bytes.
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


def _thrift_types() -> tuple[Any, Any, Any, Any]:
    from evernote.edam.notestore.ttypes import NoteFilter, NotesMetadataResultSpec
    from evernote.edam.type.ttypes import Note, Notebook

    return Note, Notebook, NoteFilter, NotesMetadataResultSpec


# Register Evernote toolset
@ToolsetBuilder("Evernote")\
    .in_group("Productivity")\
    .with_description("Evernote integration for note-taking and organization")\
    .with_category(ToolsetCategory.APP)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Evernote",
            authorize_url="https://www.evernote.com/OAuth.action",
            token_url="https://www.evernote.com/oauth",
            redirect_uri="toolsets/oauth/callback/evernote",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[],
                agent=[
                    "read",
                    "write"
                ]
            ),
            fields=[
                CommonFields.client_id("Evernote Developer Portal"),
                CommonFields.client_secret("Evernote Developer Portal")
            ],
            icon_path="/assets/icons/connectors/evernote.svg",
            app_group="Productivity",
            app_description="Evernote OAuth application for agent integration"
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            CommonFields.api_token("Evernote Developer Token", "your-developer-token")
        ])
    ])\
    .configure(lambda builder: builder.with_icon("/assets/icons/connectors/evernote.svg"))\
    .build_decorator()

class Evernote:
    """Evernote tools exposed to the agents using EvernoteDataSource.

    Every NoteStore/UserStore Thrift call takes the auth token first and Thrift
    ``ttypes`` objects, not keyword dicts.
    """

    def __init__(self, client: EvernoteClient) -> None:
        self._token = client.get_token
        self.client = EvernoteDataSource(client)
        # The Thrift calls block, and one Thrift client must not be used from two
        # threads at once: they all run in order on this loop's thread.
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(target=self._start_background_loop, daemon=True)
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    async def _call(self, coro: Coroutine[Any, Any, EvernoteResponse]) -> EvernoteResponse:
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._bg_loop))

    def shutdown(self) -> None:
        """Gracefully stop the background event loop and thread."""
        try:
            if getattr(self, "_bg_loop", None) is not None and self._bg_loop.is_running():
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if getattr(self, "_bg_loop_thread", None) is not None:
                self._bg_loop_thread.join()
            if getattr(self, "_bg_loop", None) is not None:
                self._bg_loop.close()
        except Exception as exc:
            logger.warning(f"Evernote shutdown encountered an issue: {exc}")

    def _handle_response(self, response: EvernoteResponse, success_message: str) -> tuple[bool, str]:
        if response.success:
            return True, json.dumps(
                {"message": success_message, "data": response.data or {}}, default=_json_default
            )
        return False, json.dumps({"error": response.error or "Unknown error"})

    async def _run(self, action: str, success_message: str, coro: Coroutine[Any, Any, EvernoteResponse]) -> tuple[bool, str]:
        try:
            return self._handle_response(await self._call(coro), success_message)
        except Exception as e:
            logger.error(f"Error {action}: {e}")
            return False, json.dumps({"error": str(e)})

    async def _current(self, fetch: Coroutine[Any, Any, EvernoteResponse], field: str) -> str | None:
        """A field of the stored note or notebook, for updates the API requires it on."""
        response = await self._call(fetch)
        if not response.success or not isinstance(response.data, dict):
            raise RuntimeError(response.error or f"could not read the current {field}")
        return response.data.get(field)

    @tool(
        path="/tools/evernote/create_note",
        short_description="Create a new note in Evernote",
        description="Create a new note in Evernote with a title and content, optionally in a notebook and with tags.",
        parameters=[
            ToolParameter(name="title", type=ParameterType.STRING, description="The title of the note (required)"),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="The note body: plain text, or ENML (Evernote's XHTML subset)",
            ),
            ToolParameter(
                name="notebook_guid",
                type=ParameterType.STRING,
                description="The GUID of the notebook to create the note in (default notebook if omitted)",
                required=False,
            ),
            ToolParameter(
                name="tag_guids",
                type=ParameterType.ARRAY,
                description="GUIDs of tags to assign to the note",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="create")],
    )
    async def create_note(
        self,
        title: str,
        content: str,
        notebook_guid: str | None = None,
        tag_guids: list[str] | None = None,
    ) -> tuple[bool, str]:
        Note, _, _, _ = _thrift_types()
        note = Note(title=title, content=_enml(content), notebookGuid=notebook_guid, tagGuids=tag_guids)
        return await self._run(
            "creating note", "Note created successfully", self.client.create_note(self._token(), note)
        )

    @tool(
        path="/tools/evernote/get_note",
        short_description="Get details of a specific note",
        description="Get an Evernote note by its GUID, optionally including its content and attachment data.",
        parameters=[
            ToolParameter(name="note_guid", type=ParameterType.STRING, description="The GUID of the note to retrieve (required)"),
            ToolParameter(
                name="include_content",
                type=ParameterType.BOOLEAN,
                description="Whether to include the note content (default true)",
                required=False,
            ),
            ToolParameter(
                name="include_resources_data",
                type=ParameterType.BOOLEAN,
                description="Whether to include attachment data (default false)",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def get_note(
        self,
        note_guid: str,
        include_content: bool | None = None,
        include_resources_data: bool | None = None,
    ) -> tuple[bool, str]:
        return await self._run(
            "getting note",
            "Note retrieved successfully",
            self.client.get_note(
                self._token(),
                note_guid,
                True if include_content is None else include_content,
                bool(include_resources_data),
                False,
                False,
            ),
        )

    @tool(
        path="/tools/evernote/update_note",
        short_description="Update an existing note",
        description="Update an Evernote note's title, content, notebook or tags; omitted fields are left as they are.",
        parameters=[
            ToolParameter(name="note_guid", type=ParameterType.STRING, description="The GUID of the note to update (required)"),
            ToolParameter(name="title", type=ParameterType.STRING, description="New title", required=False),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="New body: plain text, or ENML (replaces the whole body)",
                required=False,
            ),
            ToolParameter(name="notebook_guid", type=ParameterType.STRING, description="Move the note to this notebook", required=False),
            ToolParameter(name="tag_guids", type=ParameterType.ARRAY, description="Replace the note's tags with these", required=False),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="update")],
    )
    async def update_note(
        self,
        note_guid: str,
        title: str | None = None,
        content: str | None = None,
        notebook_guid: str | None = None,
        tag_guids: list[str] | None = None,
    ) -> tuple[bool, str]:
        try:
            if title is None:
                # updateNote requires the title, even when it is not changing.
                title = await self._current(
                    self.client.get_note(self._token(), note_guid, False, False, False, False), "title"
                )
        except Exception as e:
            logger.error(f"Error updating note: {e}")
            return False, json.dumps({"error": str(e)})
        Note, _, _, _ = _thrift_types()
        note = Note(
            guid=note_guid,
            title=title,
            content=None if content is None else _enml(content),
            notebookGuid=notebook_guid,
            tagGuids=tag_guids,
        )
        return await self._run(
            "updating note", "Note updated successfully", self.client.update_note(self._token(), note)
        )

    @tool(
        path="/tools/evernote/delete_note",
        short_description="Delete a note",
        description="Move an Evernote note to the trash.",
        parameters=[
            ToolParameter(name="note_guid", type=ParameterType.STRING, description="The GUID of the note to delete (required)"),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="delete")],
    )
    async def delete_note(self, note_guid: str) -> tuple[bool, str]:
        return await self._run(
            "deleting note", "Note deleted successfully", self.client.delete_note(self._token(), note_guid)
        )

    @tool(
        path="/tools/evernote/create_notebook",
        short_description="Create a new notebook",
        description="Create an Evernote notebook, optionally in a stack or as the default notebook.",
        parameters=[
            ToolParameter(name="name", type=ParameterType.STRING, description="The notebook name (required)"),
            ToolParameter(name="stack", type=ParameterType.STRING, description="The stack to put the notebook in", required=False),
            ToolParameter(
                name="default_notebook",
                type=ParameterType.BOOLEAN,
                description="Make this the default notebook",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="create")],
    )
    async def create_notebook(
        self,
        name: str,
        stack: str | None = None,
        default_notebook: bool | None = None,
    ) -> tuple[bool, str]:
        _, Notebook, _, _ = _thrift_types()
        notebook = Notebook(name=name, stack=stack, defaultNotebook=default_notebook)
        return await self._run(
            "creating notebook",
            "Notebook created successfully",
            self.client.create_notebook(self._token(), notebook),
        )

    @tool(
        path="/tools/evernote/get_notebook",
        short_description="Get details of a specific notebook",
        description="Get an Evernote notebook by its GUID.",
        parameters=[
            ToolParameter(name="notebook_guid", type=ParameterType.STRING, description="The GUID of the notebook (required)"),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def get_notebook(self, notebook_guid: str) -> tuple[bool, str]:
        return await self._run(
            "getting notebook",
            "Notebook retrieved successfully",
            self.client.get_notebook(self._token(), notebook_guid),
        )

    @tool(
        path="/tools/evernote/update_notebook",
        short_description="Update a notebook",
        description="Rename an Evernote notebook, change its stack, or make it the default; omitted fields are left as they are.",
        parameters=[
            ToolParameter(name="notebook_guid", type=ParameterType.STRING, description="The GUID of the notebook (required)"),
            ToolParameter(name="name", type=ParameterType.STRING, description="New name", required=False),
            ToolParameter(name="stack", type=ParameterType.STRING, description="New stack", required=False),
            ToolParameter(
                name="default_notebook",
                type=ParameterType.BOOLEAN,
                description="Make this the default notebook",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="update")],
    )
    async def update_notebook(
        self,
        notebook_guid: str,
        name: str | None = None,
        stack: str | None = None,
        default_notebook: bool | None = None,
    ) -> tuple[bool, str]:
        try:
            if name is None:
                # updateNotebook requires the name, even when it is not changing.
                name = await self._current(self.client.get_notebook(self._token(), notebook_guid), "name")
        except Exception as e:
            logger.error(f"Error updating notebook: {e}")
            return False, json.dumps({"error": str(e)})
        _, Notebook, _, _ = _thrift_types()
        notebook = Notebook(guid=notebook_guid, name=name, stack=stack, defaultNotebook=default_notebook)
        return await self._run(
            "updating notebook",
            "Notebook updated successfully",
            self.client.update_notebook(self._token(), notebook),
        )

    @tool(
        path="/tools/evernote/get_default_notebook",
        short_description="Get the default notebook",
        description="Get the Evernote notebook new notes go to by default.",
        parameters=[],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def get_default_notebook(self) -> tuple[bool, str]:
        return await self._run(
            "getting default notebook",
            "Default notebook retrieved successfully",
            self.client.get_default_notebook(self._token()),
        )

    @tool(
        path="/tools/evernote/search_notes",
        short_description="Search for notes",
        description="Search Evernote notes with Evernote's search grammar, optionally within a notebook or tags. Returns note titles and GUIDs; use get_note for content.",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description='Search query in Evernote search grammar (e.g. "budget intitle:plan")',
            ),
            ToolParameter(name="notebook_guid", type=ParameterType.STRING, description="Only search this notebook", required=False),
            ToolParameter(name="tag_guids", type=ParameterType.ARRAY, description="Only notes with these tag GUIDs", required=False),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description=f"Maximum notes to return (default {DEFAULT_SEARCH_RESULTS}, at most {MAX_SEARCH_RESULTS})",
                required=False,
            ),
            ToolParameter(
                name="order",
                type=ParameterType.INTEGER,
                description="Sort order (1=Created, 2=Updated, 3=Relevance, 4=UpdateSequenceNumber, 5=Title)",
                required=False,
            ),
        ],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="search")],
    )
    async def search_notes(
        self,
        query: str,
        notebook_guid: str | None = None,
        tag_guids: list[str] | None = None,
        max_results: int | None = None,
        order: int | None = None,
    ) -> tuple[bool, str]:
        _, _, NoteFilter, NotesMetadataResultSpec = _thrift_types()
        note_filter = NoteFilter(words=query, notebookGuid=notebook_guid, tagGuids=tag_guids, order=order)
        spec = NotesMetadataResultSpec(
            includeTitle=True,
            includeUpdated=True,
            includeNotebookGuid=True,
            includeTagGuids=True,
        )
        limit = min(max(int(max_results or DEFAULT_SEARCH_RESULTS), 1), MAX_SEARCH_RESULTS)
        return await self._run(
            "searching notes",
            "Note search completed successfully",
            self.client.find_notes_metadata(self._token(), note_filter, 0, limit, spec),
        )

    @tool(
        path="/tools/evernote/get_user_info",
        short_description="Get information about the authenticated user",
        description="Get information about the authenticated Evernote user.",
        parameters=[],
        tags=[Tag(key="category", value="productivity"), Tag(key="type", value="read")],
    )
    async def get_user_info(self) -> tuple[bool, str]:
        return await self._run(
            "getting user info", "User information retrieved successfully", self.client.get_user(self._token())
        )
