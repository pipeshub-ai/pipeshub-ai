"""Evernote toolset: every tool reaches the Thrift API through the generated datasource.

The fakes stand in for the SDK's NoteStore/UserStore clients only, so these tests
exercise the real EvernoteDataSource wrapper and assert the exact Thrift calls
(token first, then real ``ttypes`` objects).
"""

import inspect
import json
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from evernote.edam.notestore.ttypes import NoteFilter, NoteMetadata, NotesMetadataList
from evernote.edam.type.ttypes import Note, Notebook, User

from app.agents.actions.evernote import evernote as module
from app.agents.actions.evernote.evernote import Evernote

TOKEN = "S=s1:U=1:E=token"
TOOL_NAMES = [
    "create_note",
    "get_note",
    "update_note",
    "delete_note",
    "create_notebook",
    "get_notebook",
    "update_notebook",
    "get_default_notebook",
    "search_notes",
    "get_user_info",
]


@pytest.fixture
def stores() -> tuple[MagicMock, MagicMock]:
    note_store, user_store = MagicMock(name="NoteStore"), MagicMock(name="UserStore")
    return note_store, user_store


@pytest.fixture
def evernote(stores: tuple[MagicMock, MagicMock]) -> Iterator[Evernote]:
    note_store, user_store = stores
    client = MagicMock(name="EvernoteClient")
    client.get_token.return_value = TOKEN
    client.get_note_store.return_value = note_store
    client.get_user_store.return_value = user_store
    tools = Evernote(client)
    yield tools
    tools.shutdown()


def _ok(result: tuple[bool, str]) -> dict:
    success, payload = result
    assert success, payload
    return json.loads(payload)


def test_every_tool_is_an_async_tool() -> None:
    for name in TOOL_NAMES:
        assert inspect.iscoroutinefunction(getattr(Evernote, name)), name


def test_the_toolset_registers_with_a_current_category() -> None:
    assert module.ToolsetCategory.APP.value == "app"


async def test_search_notes_uses_find_notes_metadata_with_a_note_filter(evernote, stores) -> None:
    note_store, _ = stores
    note_store.findNotesMetadata.return_value = NotesMetadataList(
        startIndex=0, totalNotes=1, notes=[NoteMetadata(guid="n1", title="Q3 plan")]
    )
    data = _ok(
        await evernote.search_notes(query="plan", notebook_guid="nb1", tag_guids=["t1"], max_results=5, order=2)
    )
    token, note_filter, offset, max_notes, spec = note_store.findNotesMetadata.call_args.args
    assert token == TOKEN
    assert note_filter == NoteFilter(words="plan", notebookGuid="nb1", tagGuids=["t1"], order=2)
    assert (offset, max_notes) == (0, 5)
    assert spec.includeTitle and spec.includeUpdated and spec.includeNotebookGuid
    assert data["data"]["notes"][0] == {"guid": "n1", "title": "Q3 plan"}


async def test_search_notes_defaults_and_caps_the_page_size(evernote, stores) -> None:
    note_store, _ = stores
    note_store.findNotesMetadata.return_value = NotesMetadataList(startIndex=0, totalNotes=0, notes=[])
    await evernote.search_notes(query="x")
    assert note_store.findNotesMetadata.call_args.args[3] == module.DEFAULT_SEARCH_RESULTS
    await evernote.search_notes(query="x", max_results=10_000)
    assert note_store.findNotesMetadata.call_args.args[3] == module.MAX_SEARCH_RESULTS


async def test_get_user_info_calls_get_user(evernote, stores) -> None:
    _, user_store = stores
    user_store.getUser.return_value = User(id=1, username="ada")
    data = _ok(await evernote.get_user_info())
    user_store.getUser.assert_called_once_with(TOKEN)
    assert data["data"] == {"id": 1, "username": "ada"}


async def test_get_note_passes_every_thrift_flag(evernote, stores) -> None:
    note_store, _ = stores
    note_store.getNote.return_value = Note(guid="n1", title="T", contentHash=b"\x01\xff")
    data = _ok(await evernote.get_note(note_guid="n1"))
    note_store.getNote.assert_called_once_with(TOKEN, "n1", True, False, False, False)
    # Thrift binary fields must not break the JSON reply.
    assert data["data"]["contentHash"] == "01ff"


async def test_create_note_builds_a_note_and_wraps_plain_text_as_enml(evernote, stores) -> None:
    note_store, _ = stores
    note_store.createNote.return_value = Note(guid="n1", title="T")
    _ok(await evernote.create_note(title="T", content="a < b & c", notebook_guid="nb1", tag_guids=["t1"]))
    token, note = note_store.createNote.call_args.args
    assert token == TOKEN
    assert isinstance(note, Note)
    assert (note.title, note.notebookGuid, note.tagGuids) == ("T", "nb1", ["t1"])
    assert note.content.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<en-note>a &lt; b &amp; c</en-note>" in note.content


async def test_create_note_keeps_enml_as_given(evernote, stores) -> None:
    note_store, _ = stores
    note_store.createNote.return_value = Note(guid="n1")
    enml = '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd"><en-note><b>x</b></en-note>'
    await evernote.create_note(title="T", content=enml)
    assert note_store.createNote.call_args.args[1].content == enml


async def test_update_note_without_a_title_keeps_the_existing_title(evernote, stores) -> None:
    note_store, _ = stores
    note_store.getNote.return_value = Note(guid="n1", title="Existing")
    note_store.updateNote.return_value = Note(guid="n1", title="Existing")
    _ok(await evernote.update_note(note_guid="n1", content="new body"))
    note_store.getNote.assert_called_once_with(TOKEN, "n1", False, False, False, False)
    token, note = note_store.updateNote.call_args.args
    assert token == TOKEN
    assert (note.guid, note.title) == ("n1", "Existing")
    assert "<en-note>new body</en-note>" in note.content


async def test_update_note_with_a_title_does_not_read_the_note(evernote, stores) -> None:
    note_store, _ = stores
    note_store.updateNote.return_value = Note(guid="n1", title="New")
    await evernote.update_note(note_guid="n1", title="New")
    note_store.getNote.assert_not_called()
    note = note_store.updateNote.call_args.args[1]
    assert (note.guid, note.title, note.content) == ("n1", "New", None)


async def test_delete_note(evernote, stores) -> None:
    note_store, _ = stores
    note_store.deleteNote.return_value = 42
    _ok(await evernote.delete_note(note_guid="n1"))
    note_store.deleteNote.assert_called_once_with(TOKEN, "n1")


async def test_notebook_tools(evernote, stores) -> None:
    note_store, _ = stores
    note_store.createNotebook.return_value = Notebook(guid="nb1", name="Work")
    _ok(await evernote.create_notebook(name="Work", stack="Jobs", default_notebook=True))
    token, notebook = note_store.createNotebook.call_args.args
    assert token == TOKEN
    assert (notebook.name, notebook.stack, notebook.defaultNotebook) == ("Work", "Jobs", True)

    note_store.getNotebook.return_value = Notebook(guid="nb1", name="Work")
    _ok(await evernote.get_notebook(notebook_guid="nb1"))
    note_store.getNotebook.assert_called_with(TOKEN, "nb1")

    note_store.updateNotebook.return_value = 7
    _ok(await evernote.update_notebook(notebook_guid="nb1", stack="Archive"))
    notebook = note_store.updateNotebook.call_args.args[1]
    # updateNotebook requires the name; an omitted name keeps the current one.
    assert (notebook.guid, notebook.name, notebook.stack) == ("nb1", "Work", "Archive")

    note_store.getDefaultNotebook.return_value = Notebook(guid="nb0", name="Inbox")
    data = _ok(await evernote.get_default_notebook())
    note_store.getDefaultNotebook.assert_called_once_with(TOKEN)
    assert data["data"]["name"] == "Inbox"


async def test_a_thrift_error_is_a_failed_tool_result(evernote, stores) -> None:
    note_store, _ = stores
    note_store.deleteNote.side_effect = RuntimeError("EDAMNotFoundException")
    success, payload = await evernote.delete_note(note_guid="missing")
    assert not success
    assert "EDAMNotFoundException" in json.loads(payload)["error"]


async def test_list_results_are_valid_responses(evernote, stores) -> None:
    note_store, _ = stores
    note_store.listNotebooks.return_value = [Notebook(guid="nb1", name="Work")]
    response = await evernote._call(evernote.client.list_notebooks(TOKEN))
    assert response.success, response.error
    assert response.data == [{"guid": "nb1", "name": "Work"}]


def test_the_registry_discovers_all_ten_tools() -> None:
    # The module is off by default in the registry's standard list; loading it
    # explicitly must work and expose every tool.
    from app.agents.registry.toolset_registry import ToolsetRegistry

    registry = ToolsetRegistry()
    registry.discover_toolsets(["app.agents.actions.evernote.evernote"])
    metadata = registry.get_toolset_metadata("evernote")
    assert metadata is not None
    assert sorted(t["name"] for t in metadata["tools"]) == sorted(TOOL_NAMES)
