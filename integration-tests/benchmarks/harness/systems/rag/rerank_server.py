"""The RAG baselines' cross-encoder, served over HTTP so competitor products
rerank with exactly the model and code the baselines use.

    python -m benchmarks.harness.systems.rag.rerank_server --port 8787

Two request shapes, the ones the products send:
  POST /v1/rerank  {model, query, documents, top_n}  -> {results: [{index, relevance_score}]}
      (Jina/Cohere style: Open WebUI's external reranker)
  POST /rerank     {query, texts}                    -> [{index, score}]
      (Text Embeddings Inference style)
Scores are the sigmoid of the cross-encoder logit: the order is the model's,
and every score is in (0, 1), so a relevance threshold of 0 drops nothing.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any

from benchmarks.harness.systems.rag.rerank import DEFAULT_RERANKER, CrossEncoderReranker

logger = logging.getLogger(__name__)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _text(document: Any) -> str:  # noqa: ANN401
    return str(document.get("text", "")) if isinstance(document, dict) else str(document)


def rerank_response(reranker: CrossEncoderReranker, path: str, body: dict[str, Any]) -> Any:  # noqa: ANN401
    """The response body for one request, or raises ValueError on a bad one."""
    query = body.get("query")
    if not isinstance(query, str):
        raise ValueError("`query` must be a string")
    if path.rstrip("/").endswith("/v1/rerank"):
        documents = [_text(d) for d in body.get("documents") or []]
        scores = [_sigmoid(s) for s in reranker.scores(query, documents)]
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        top_n = body.get("top_n")
        if isinstance(top_n, int) and top_n > 0:
            order = order[:top_n]
        return {"model": reranker.model_name, "results": [{"index": i, "relevance_score": scores[i]} for i in order]}
    if path.rstrip("/").endswith("/rerank"):
        texts = [str(t) for t in body.get("texts") or []]
        scores = [_sigmoid(s) for s in reranker.scores(query, texts)]
        return sorted(({"index": i, "score": s} for i, s in enumerate(scores)), key=lambda r: -r["score"])
    raise LookupError(path)


def make_handler(reranker: CrossEncoderReranker) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: Any) -> None:  # noqa: ANN401
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            self._send(200, {"model": reranker.model_name}) if self.path.rstrip("/") in ("", "/health") else self._send(404, {})

        def do_POST(self) -> None:  # noqa: N802
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                self._send(200, rerank_response(reranker, self.path, body))
            except LookupError:
                self._send(404, {"error": f"no route {self.path}"})
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"error": str(exc)})

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ANN401
            logger.debug(fmt, *args)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 — reached from containers
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--model", default=DEFAULT_RERANKER)
    parser.add_argument("--device", default="cpu", help="cpu, mps or cuda")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    reranker = CrossEncoderReranker(args.model, device=args.device)
    reranker.scores("warm up", ["the model loads before the first request"])
    logger.info("reranking with %s on %s, %s:%d", args.model, args.device, args.host, args.port)
    # torch's MPS backend hangs off the main thread, so a GPU server answers
    # one request at a time on it; the model lock serialises them anyway.
    server = ThreadingHTTPServer if args.device == "cpu" else HTTPServer
    server((args.host, args.port), make_handler(reranker)).serve_forever()


if __name__ == "__main__":
    main()
