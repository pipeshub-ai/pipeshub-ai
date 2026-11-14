"""The precision each response names, as the client reads it from a real HTTP server."""

import asyncio
import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.exceptions.indexing_exceptions import EmbeddingPrecisionUnknownError
from app.utils.embedding_server_client import EmbeddingServerEmbeddings
from app.utils.precision_witness import PrecisionWitness, set_precision_witness


class _Server:
    def __init__(self, headers_for: Callable[[list[str]], dict[str, str]], announces: bool = True) -> None:
        outer = self
        self.posts = 0

        class Handler(BaseHTTPRequestHandler):
            def _send(self, body: dict, headers: dict[str, str] | None = None) -> None:
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                item = {"id": "m", "object": "model", "created": 1, "owned_by": "x", "max_seq_length": 512}
                if announces:
                    item |= {"precision": "fp32", "precision_intent": "fp32", "precision_fallback": False}
                self._send({"object": "list", "data": [item]})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
                outer.posts += 1
                self._send(
                    {"object": "list", "model": "m", "usage": {"prompt_tokens": 1, "total_tokens": 1},
                     "data": [{"object": "embedding", "index": i, "embedding": [0.1, 0.2]} for i in range(len(texts))]},
                    headers_for(texts),
                )

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., _Server]]:
    started: list[_Server] = []

    def start(headers_for, announces: bool = True) -> _Server:
        made = _Server(headers_for, announces)
        started.append(made)
        monkeypatch.setenv("EMBEDDING_SERVER_URL", made.url)
        return made

    set_precision_witness(PrecisionWitness())
    yield start
    for made in started:
        made.httpd.shutdown()
        made.httpd.server_close()
    set_precision_witness(None)


def _client() -> EmbeddingServerEmbeddings:
    return EmbeddingServerEmbeddings(model="m", max_retries=0)


class TestWitness:
    @pytest.mark.asyncio
    async def test_the_precision_of_a_call_is_read_from_its_response(self, server) -> None:
        server(lambda _t: {"X-Embedding-Precision": "int8-w"})
        vectors, precision, fallback = await _client().aembed_documents_witnessed(["a"])
        assert vectors == [[0.1, 0.2]] and precision == "int8-w" and fallback is False

    @pytest.mark.asyncio
    async def test_a_fallback_flag_travels_with_the_precision(self, server) -> None:
        server(lambda _t: {"X-Embedding-Precision": "fp32", "X-Embedding-Precision-Fallback": "1"})
        _, precision, fallback = await _client().aembed_documents_witnessed(["a"])
        assert (precision, fallback) == ("fp32", True)

    @pytest.mark.asyncio
    async def test_a_restart_with_no_connection_error_is_seen_on_the_next_call(self, server) -> None:
        answers = iter(["int8-w", "fp32"])
        server(lambda _t: {"X-Embedding-Precision": next(answers)})
        client = _client()
        assert (await client.aembed_documents_witnessed(["a"]))[1] == "int8-w"
        assert (await client.aembed_documents_witnessed(["a"]))[1] == "fp32"

    @pytest.mark.asyncio
    async def test_two_responses_of_one_call_that_disagree_are_refused(self, server) -> None:
        answers = iter(["int8-w", "fp32"])
        server(lambda _t: {"X-Embedding-Precision": next(answers)})
        client = _client()
        client._inner.chunk_size = 1
        with pytest.raises(EmbeddingPrecisionUnknownError, match="different precision"):
            await client.aembed_documents_witnessed(["a", "b"])

    @pytest.mark.asyncio
    async def test_concurrent_calls_each_get_their_own_precision(self, server) -> None:
        server(lambda texts: {"X-Embedding-Precision": "int8-w" if texts == ["b"] else "fp32"})
        client = _client()
        results = await asyncio.gather(
            *[client.aembed_documents_witnessed(["a" if i % 2 == 0 else "b"]) for i in range(20)]
        )
        assert [r[1] for r in results] == ["fp32" if i % 2 == 0 else "int8-w" for i in range(20)]

    def test_the_sync_twin_reads_the_same_headers(self, server) -> None:
        server(lambda _t: {"X-Embedding-Precision": "int8-w"})
        _, precision, _ = _client().embed_documents_witnessed(["a"])
        assert precision == "int8-w"


class TestLegacyServer:
    @pytest.mark.asyncio
    async def test_a_header_less_response_from_an_older_server_is_fp32(self, server) -> None:
        server(lambda _t: {}, announces=False)
        _, precision, fallback = await _client().aembed_documents_witnessed(["a"])
        assert (precision, fallback) == ("fp32", False)

    @pytest.mark.asyncio
    async def test_a_header_less_response_from_a_server_that_announces_is_refused(self, server) -> None:
        server(lambda _t: {}, announces=True)
        with pytest.raises(EmbeddingPrecisionUnknownError, match="announces"):
            await _client().aembed_documents_witnessed(["a"])

    @pytest.mark.asyncio
    async def test_an_answer_is_remembered_per_model_for_the_next_check(self, server) -> None:
        from app.utils.precision_witness import get_precision_witness

        server(lambda _t: {"X-Embedding-Precision": "int8-w"})
        await _client().aembed_documents_witnessed(["a"])
        assert get_precision_witness().latest("m").precision == "int8-w"


class TestPrecisionWitness:
    @pytest.mark.asyncio
    async def test_a_recent_witness_is_reused_and_an_old_one_is_replaced(self) -> None:
        now = [0.0]
        witness = PrecisionWitness(clock=lambda: now[0])
        calls: list[int] = []

        async def probe() -> tuple[str, bool]:
            calls.append(1)
            return "fp32", False

        witness.record("m", "int8-w")
        assert (await witness.fresh("m", 5, probe)).precision == "int8-w" and calls == []
        now[0] = 6.0
        assert (await witness.fresh("m", 5, probe)).precision == "fp32" and calls == [1]

    @pytest.mark.asyncio
    async def test_fifty_waiters_cause_one_probe(self) -> None:
        witness = PrecisionWitness()
        calls: list[int] = []

        async def probe() -> tuple[str, bool]:
            calls.append(1)
            await asyncio.sleep(0.05)
            return "int8-w", False

        results = await asyncio.gather(*[witness.fresh("m", 0, probe) for _ in range(50)])
        assert len(calls) == 1 and {r.precision for r in results} == {"int8-w"}

    @pytest.mark.asyncio
    async def test_the_witness_is_kept_per_model(self) -> None:
        witness = PrecisionWitness()
        witness.record("records", "int8-w")
        witness.record("entities", "fp32", fallback=True)
        assert witness.latest("records").precision == "int8-w" and witness.latest("records").fallback is False
        assert witness.latest("entities").fallback is True

    @pytest.mark.asyncio
    async def test_a_failed_probe_is_raised_to_every_waiter(self) -> None:
        witness = PrecisionWitness()

        async def probe() -> tuple[str, bool]:
            await asyncio.sleep(0.02)
            raise RuntimeError("down")

        results = await asyncio.gather(*[witness.fresh("m", 0, probe) for _ in range(3)], return_exceptions=True)
        assert all(isinstance(r, RuntimeError) for r in results)
