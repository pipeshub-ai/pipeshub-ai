"""The web-fixtures service's stand-in embedding model.

The sign-up and onboarding browser test saves an OpenAI-compatible embedding
model pointed at this endpoint, and the product's health check rejects empty or
ragged vectors. These run the real server in a thread, so they need no stack.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import requests

if TYPE_CHECKING:
    from collections.abc import Iterator

# Loaded by path: helper/ is on sys.path in this suite, so the name
# ``web_fixtures`` already means the client module there.
_SERVER = Path(__file__).resolve().parents[1] / "web_fixtures" / "server.py"
_spec = importlib.util.spec_from_file_location("web_fixtures_server", _SERVER)
assert _spec and _spec.loader
server_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server_module
_spec.loader.exec_module(server_module)


@pytest.fixture
def embeddings_url(tmp_path: Path) -> Iterator[str]:
    handler = server_module.make_handler(server_module.Site(tmp_path), "http://fixtures")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/__fixtures__/openai/v1/embeddings"
    finally:
        server.shutdown()
        server.server_close()


def _embed(url: str, inputs: object) -> list[list[float]]:
    response = requests.post(url, json={"model": "stand-in", "input": inputs}, timeout=5)
    assert response.status_code == 200
    body = response.json()
    assert [item["index"] for item in body["data"]] == list(range(len(body["data"])))
    return [item["embedding"] for item in body["data"]]


def test_each_text_gets_a_vector_of_the_same_size(embeddings_url: str) -> None:
    vectors = _embed(embeddings_url, ["first text", "second text", "first text"])

    assert len(vectors) == 3
    assert {len(v) for v in vectors} == {server_module.STAND_IN_EMBEDDING_SIZE}
    assert vectors[0] == vectors[2]
    assert vectors[0] != vectors[1]


@pytest.mark.parametrize("single", ["one text", [101, 102, 103]])
def test_a_single_input_gets_one_vector(embeddings_url: str, single: object) -> None:
    vectors = _embed(embeddings_url, single)

    assert len(vectors) == 1
    assert len(vectors[0]) == server_module.STAND_IN_EMBEDDING_SIZE
    assert any(vectors[0])


def test_token_arrays_get_one_vector_each(embeddings_url: str) -> None:
    vectors = _embed(embeddings_url, [[1, 2], [3, 4]])

    assert len(vectors) == 2
    assert vectors[0] != vectors[1]
