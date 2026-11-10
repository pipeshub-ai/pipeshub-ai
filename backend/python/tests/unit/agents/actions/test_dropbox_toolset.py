"""Unit tests for the Dropbox search tool (app.agents.actions.dropbox.dropbox)."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

from dropbox.files import SearchOptions

from app.agents.actions.dropbox.dropbox import Dropbox


def _make_tool() -> tuple[Dropbox, MagicMock]:
    tool = object.__new__(Dropbox)
    data_source = MagicMock()
    response = MagicMock(success=True)
    response.to_json.return_value = json.dumps({"matches": []})
    data_source.files_search_v2 = AsyncMock(return_value=response)
    tool.client = data_source
    # The real tool runs coroutines on a background loop thread; mirror that.
    tool._run_async = lambda coro: ThreadPoolExecutor(max_workers=1).submit(asyncio.run, coro).result()
    return tool, data_source


class TestSearch:
    def test_query_only_succeeds(self) -> None:
        tool, data_source = _make_tool()
        success, _ = asyncio.run(tool.search(query="report"))
        assert success is True
        call_kwargs = data_source.files_search_v2.call_args.kwargs
        assert call_kwargs["query"] == "report"
        assert "path" not in call_kwargs
        assert "max_results" not in call_kwargs

    def test_path_and_max_results_go_into_search_options(self) -> None:
        tool, data_source = _make_tool()
        success, _ = asyncio.run(tool.search(query="report", path="/docs", max_results=5))
        assert success is True
        options = data_source.files_search_v2.call_args.kwargs["options"]
        assert isinstance(options, SearchOptions)
        assert options.path == "/docs"
        assert options.max_results == 5

    def test_failed_response_returns_false(self) -> None:
        tool, data_source = _make_tool()
        failed = MagicMock(success=False)
        failed.to_json.return_value = json.dumps({"error": "boom"})
        data_source.files_search_v2 = AsyncMock(return_value=failed)
        success, payload = asyncio.run(tool.search(query="report"))
        assert success is False
        assert json.loads(payload) == {"error": "boom"}


def _tool_with(data_source: MagicMock) -> Dropbox:
    tool = object.__new__(Dropbox)
    tool.client = data_source
    tool._run_async = lambda coro: ThreadPoolExecutor(max_workers=1).submit(asyncio.run, coro).result()
    return tool


class TestAnswersAreJson:
    def test_a_real_sdk_result_is_written_as_json(self) -> None:
        """to_json of an SDK object raised TypeError, so every successful call reported an error."""
        from datetime import datetime, timezone

        from dropbox.files import FileMetadata, ListFolderResult

        from app.sources.client.dropbox.dropbox_ import DropboxResponse

        listing = ListFolderResult(entries=[FileMetadata(
            name="a.txt", id="id:a", path_lower="/a.txt", path_display="/a.txt", rev="0123456789",
            client_modified=datetime(2026, 1, 1, tzinfo=timezone.utc), server_modified=datetime(2026, 1, 1, tzinfo=timezone.utc), size=3)],
            cursor="c", has_more=False)
        data_source = MagicMock()
        data_source.files_list_folder = AsyncMock(return_value=DropboxResponse(success=True, data=listing))

        success, payload = asyncio.run(_tool_with(data_source).list_folder(path=""))

        assert success is True
        body = json.loads(payload)["data"]
        assert body["entries"][0][".tag"] == "file" and body["entries"][0]["name"] == "a.txt"


class TestDownload:
    def test_the_content_is_returned_up_to_a_bound(self) -> None:
        from app.agents.actions.dropbox import dropbox as module
        from app.sources.client.dropbox.dropbox_ import DropboxResponse

        http = MagicMock()
        http.iter_content = MagicMock(return_value=iter([b"x" * module._MAX_DOWNLOAD_BYTES, b"more"]))
        data_source = MagicMock()
        data_source.files_download = AsyncMock(return_value=DropboxResponse(success=True, data=(None, http)))

        success, payload = asyncio.run(_tool_with(data_source).download_file(path="/big.bin"))

        body = json.loads(payload)
        assert success is True and body["truncated"] is True
        assert len(body["content"]) == module._MAX_DOWNLOAD_BYTES
        http.close.assert_called_once()
        assert body["encoding"] == "utf-8"

    def test_a_binary_file_comes_back_unchanged_as_base64(self) -> None:
        import base64

        from app.sources.client.dropbox.dropbox_ import DropboxResponse

        raw = bytes(range(256))
        http = MagicMock()
        http.iter_content = MagicMock(return_value=iter([raw]))
        data_source = MagicMock()
        data_source.files_download = AsyncMock(return_value=DropboxResponse(success=True, data=(None, http)))

        success, payload = asyncio.run(_tool_with(data_source).download_file(path="/photo.png"))

        body = json.loads(payload)
        assert success is True and body["encoding"] == "base64" and body["truncated"] is False
        assert base64.b64decode(body["content"]) == raw

    def test_a_cut_through_a_character_keeps_the_text(self) -> None:
        from app.agents.actions.dropbox import dropbox as module
        from app.sources.client.dropbox.dropbox_ import DropboxResponse

        text = ("a" * (module._MAX_DOWNLOAD_BYTES - 1) + "é").encode()  # the bound falls inside "é"
        http = MagicMock()
        http.iter_content = MagicMock(return_value=iter([text, b"more"]))
        data_source = MagicMock()
        data_source.files_download = AsyncMock(return_value=DropboxResponse(success=True, data=(None, http)))

        success, payload = asyncio.run(_tool_with(data_source).download_file(path="/notes.txt"))

        body = json.loads(payload)
        assert body["encoding"] == "utf-8" and body["content"] == "a" * (module._MAX_DOWNLOAD_BYTES - 1)


class TestSharedLink:
    def test_an_existing_link_is_returned_without_making_one(self) -> None:
        from types import SimpleNamespace

        from app.sources.client.dropbox.dropbox_ import DropboxResponse

        data_source = MagicMock()
        data_source.sharing_list_shared_links = AsyncMock(return_value=DropboxResponse(
            success=True, data=SimpleNamespace(links=[SimpleNamespace(url="https://www.dropbox.com/scl/fi/x")])))
        data_source.sharing_create_shared_link_with_settings = AsyncMock()

        success, payload = asyncio.run(_tool_with(data_source).get_shared_link(path="/a.txt"))

        assert success is True and json.loads(payload) == {"url": "https://www.dropbox.com/scl/fi/x", "created": False}
        data_source.sharing_create_shared_link_with_settings.assert_not_awaited()

    def test_a_new_link_opens_only_for_people_with_access(self) -> None:
        """Without settings Dropbox makes a public link: anyone with the URL could open the file."""
        from types import SimpleNamespace

        from dropbox.sharing import LinkAudience

        from app.sources.client.dropbox.dropbox_ import DropboxResponse

        data_source = MagicMock()
        data_source.sharing_list_shared_links = AsyncMock(return_value=DropboxResponse(
            success=True, data=SimpleNamespace(links=[])))
        data_source.sharing_create_shared_link_with_settings = AsyncMock(return_value=DropboxResponse(
            success=True, data=SimpleNamespace(url="https://www.dropbox.com/scl/fi/new")))

        success, _ = asyncio.run(_tool_with(data_source).get_shared_link(path="/a.txt"))

        settings = data_source.sharing_create_shared_link_with_settings.await_args.kwargs["settings"]
        assert success is True and settings.audience == LinkAudience.no_one
