"""The search routes and the retrieval service hand the content locator on, so a locator that stamps or renumbers hits is
honoured at the serialisation boundary (``/search`` and ``/retrieve``) and in the flattened results."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.api.routes.search import RetrieveQuery, _retrieve_chunks
from app.modules.retrieval.content_locator import (
    SingleHolderLocator,
    set_locator_factory,
)
from tests.unit.api.routes.test_retrieve import _hit, _results, _retrieve
from tests.unit.modules.retrieval.test_internal_keys_never_leave import _corpus

APP = Path(__file__).resolve().parents[4] / "app"


class RenumberingLocator(SingleHolderLocator):
    """A locator whose holders number their blocks apart: the client's index is not the local one."""

    def public_index(self, holder_index: int | None, local: int) -> int:
        return 1000 * (holder_index or 0) + local


def _calls(path: Path, name: str) -> list[ast.Call]:
    return [
        node for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == name
    ]


def test_the_chunk_list_gives_the_client_the_locators_index() -> None:
    (chunk,) = _retrieve_chunks(_corpus(True), None, RenumberingLocator())
    assert chunk["citation"]["blockIndex"] == 1003
    assert chunk["id"].endswith(":1003")


def test_negative_control_without_the_locator_the_local_index_reaches_the_client() -> None:
    """Why the wiring matters: the boundary can only renumber a hit when it is handed the locator."""
    (chunk,) = _retrieve_chunks(_corpus(True), None)
    assert chunk["citation"]["blockIndex"] == 3


def test_every_call_of_the_boundary_in_the_search_routes_passes_a_locator() -> None:
    path = APP / "api/routes/search.py"
    calls = [c for name in ("public_results", "public_hit") for c in _calls(path, name)]
    assert len(calls) >= 2
    unwired = [c.lineno for c in calls if len(c.args) + len(c.keywords) < 2]
    assert not unwired, f"public_results/public_hit without a locator at search.py:{unwired}"


def test_the_retrieve_route_builds_the_chunk_list_with_a_locator() -> None:
    calls = _calls(APP / "api/routes/search.py", "_retrieve_chunks")
    assert calls and all(len(c.args) + len(c.keywords) >= 3 for c in calls)


def test_the_retrieval_service_hands_its_locator_to_the_flattening() -> None:
    calls = _calls(APP / "modules/retrieval/retrieval_service.py", "get_flattened_results")
    assert calls
    missing = [c.lineno for c in calls if "content_locator" not in {k.arg for k in c.keywords}]
    assert not missing, f"get_flattened_results without content_locator at retrieval_service.py:{missing}"


async def _retrieved_block_index() -> int:
    hit = _hit("r1", 3, "text")
    hit["holder_index"] = 1
    status, body, *_ = await _retrieve(RetrieveQuery(query="q"), _results(hit))
    assert status == 200
    return body["chunks"][0]["citation"]["blockIndex"]


@pytest.mark.asyncio
async def test_the_retrieve_route_gives_the_client_the_installed_locators_index() -> None:
    set_locator_factory(lambda _graph: RenumberingLocator())
    assert await _retrieved_block_index() == 1003


@pytest.mark.asyncio
async def test_the_retrieve_route_with_no_locator_installed_keeps_the_local_index() -> None:
    assert await _retrieved_block_index() == 3
