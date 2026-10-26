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
