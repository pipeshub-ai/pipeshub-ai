"""A fake Ollama (and OpenAI-compatible) model server for the simulator shards. Standard library only.

Simulator suites check what a connector syncs and who may read it, not what a model says, so their
stack does not need a real model: it needs one that answers every call the indexing pipeline and the
AI-model health check make, quickly and the same way every time.

* ``GET /api/tags``, ``GET /api/version``, ``POST /api/show``: what an Ollama client asks before use.
* ``POST /api/chat`` and ``POST /api/generate``: with a JSON schema in ``format``, a smallest instance of
  that schema. Without one, the indexing pipeline's own prompts get a valid JSON answer: its structured
  calls send no ``format`` to Ollama (``backend/python/app/utils/streaming.py`` ``_apply_structured_output``)
  and parse the reply text, so the reply is picked from the prompt's own words (``ANSWERS``: document and
  code classification, entity merge decisions, the table prompts; the system message is read first, so a
  record that quotes another prompt does not steer the answer; ``format: "json"`` gets the document one).
  Anything else gets a short text. Streaming (the Ollama default) sends the answer and then a final
  ``done`` line.
* ``POST /api/embed``, ``POST /api/embeddings``, ``POST /v1/embeddings``: a unit-length vector of
  ``DIMENSIONS`` numbers derived from a hash of the text, so equal texts get equal vectors.
* ``POST /v1/chat/completions``: the same answers in the OpenAI shape; a JSON schema can come in
  ``response_format.json_schema.schema`` or in ``format``.
* A call it cannot answer (an error in a route, or a request http.server refuses) is answered 500 and logged as
  one ``fake-model FAILED`` line. The suites cannot see a failed model call, so the workflow fails the leg when
  the fake's log holds one (``Check the fake model stayed up`` in .github/workflows/integration-tests.yml).

Each chat, generate and embedding call is logged as one line (method, path, model, which answer or how
many texts), so a stack's logs show what the model was asked. Run: ``python3 -I server.py [--host 0.0.0.0] [--port 11434]``. Importing this module
starts nothing. ``backend/python/tests/unit/utils/test_structured_output_fake_ollama.py`` runs each of the
app's structured calls in ``ANSWERS`` against it, so a reworded prompt or a changed schema fails there.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DIMENSIONS = 64
MAX_SCHEMA_DEPTH = 12
MODELS = ("qwen2.5:0.5b", "nomic-embed-text", "nomic-embed-text:latest")
PLAIN_ANSWER = "OK. This is a test reply."
FAILED = "fake-model FAILED"
# A valid DocumentClassification (backend/python/app/modules/transformers/document_extraction.py).
CLASSIFICATION_ANSWER = {
    "departments": [],
    "category": "Other",
    "subcategories": {"level1": "", "level2": "", "level3": ""},
    "languages": ["English"],
    "topics": ["testing", "simulation"],
    "organizations": [],
    "summary": "A test document.",
}
# A valid CodeClassification (same module), for the code prompt connectors' source files get.
CODE_CLASSIFICATION_ANSWER = {
    "architecture_role": "Service",
    "category": "Other",
    "subcategories": {"level1": "", "level2": "", "level3": ""},
    "topics": ["testing", "simulation", "fixtures"],
    "summary": "A test source file.",
    "design_patterns": [],
    "external_dependencies": [],
}
# A valid MergeDecisions (backend/python/app/modules/entity_resolution/models.py) with no decision: what a prompt
# whose items cannot be read gets. merge_decisions() answers each listed item instead.
MERGE_DECISIONS_ANSWER = {"decisions": []}
# Valid answers to the table prompts (backend/python/app/modules/parsers/excel/prompt_template.py).
TABLE_ENRICHMENT_ANSWER = {"summary": "A test table.", "headers": [], "header_row_count": 0, "descriptions": []}
TABLE_SUMMARY_HEADERS_ANSWER = {"summary": "A test table.", "headers": [], "header_row_count": 0}
KEYED_ROW_DESCRIPTIONS_ANSWER = {"descriptions": []}
# Valid ExcelHeaderDetection and CSVHeaderDetection (same file).
HEADER_DETECTION_ANSWER = {"has_headers": True, "num_header_rows": 1, "confidence": "high", "reasoning": "A test table."}
# A valid TableSummary (backend/python/app/utils/indexing_helpers.py).
TABLE_SUMMARY_JSON_ANSWER = {"summary": "A test table.", "headers": []}
_MERGE_ITEM = re.compile(r'^\{"i":(\d+),', re.MULTILINE)
_COLUMN_COUNT = re.compile(r"(?:REQUIRED NUMBER OF COLUMNS|Number of columns):\s*(\d+)")


def merge_decisions(prompt: str) -> dict[str, Any]:
    """A MergeDecisions that decides every item the prompt lists (one ``{"i":N,...}`` line each): all new
    concepts, so the resolver takes its normal path, not the one for a model that answered nothing."""
    indexes = sorted({int(index) for index in _MERGE_ITEM.findall(prompt)})
    decisions = [{"i": i, "same": False, "target": "", "same_as_item": -1, "canonical_name": ""} for i in indexes]
    return {"decisions": decisions} if decisions else MERGE_DECISIONS_ANSWER


def generated_headers(prompt: str) -> dict[str, Any]:
    """A TableHeaders with exactly the number of headers the prompt asks for."""
    match = _COLUMN_COUNT.search(prompt)
    count = int(match.group(1)) if match else 0
    return {"headers": [f"Column {n}" for n in range(1, count + 1)]}


def named_entity_selection(prompt: str) -> dict[str, Any]:
    """The named-entity select answer: every listed name is NEW (``name | snippet | options`` lines)."""
    names = [line.split(" | ", 1)[0] for line in prompt.splitlines()[1:] if " | " in line]
    return {"decisions": [{"name": name, "id": "NEW"} for name in names]}
# (words the prompt holds, the answer, its name in the log): the first match wins, so a prompt whose words are
# inside another's comes after it. The words are each prompt's own opening, in the files named above;
# scripts/test_fake_model.py fails when a prompt no longer holds them.
# An answer is a JSON value, or a function of the prompt's text that returns one.
ANSWERS = (
    ("structured metadata for a code search index", CODE_CLASSIFICATION_ANSWER, "code"),
    ("classify the document departments", CLASSIFICATION_ANSWER, "document"),
    ("You maintain a knowledge-graph taxonomy for one organisation", merge_decisions, "merge"),
    ("who analyses tables and converts structured data", TABLE_ENRICHMENT_ANSWER, "table"),
    ("who converts structured data into natural language descriptions", KEYED_ROW_DESCRIPTIONS_ANSWER, "rows"),
    ("You are a data analysis expert who analyses tables.", TABLE_SUMMARY_HEADERS_ANSWER, "table-summary"),
    ("to detect if headers exist", HEADER_DETECTION_ANSWER, "header-detection"),
    ("Generate descriptive, professional column headers", generated_headers, "headers"),
    ("Provide a clear summary of this table's purpose and content.", TABLE_SUMMARY_JSON_ANSWER, "table-summary-json"),
    ("For each line pick a candidate id or NEW.", named_entity_selection, "entity-select"),
)


def embedding(text: str) -> list[float]:
    """A unit-length vector seeded by the text's hash: deterministic, and different texts differ."""
    rng = random.Random(hashlib.sha256(text.encode()).hexdigest())
    vector = [rng.uniform(-1, 1) for _ in range(DIMENSIONS)]
    norm = sum(x * x for x in vector) ** 0.5
    return [x / norm for x in vector]


def failure_line(command: Any, path: Any, detail: str) -> str:
    """The log line for a call the fake did not answer; the workflow counts these after each leg's tests."""
    return f"{FAILED} {command} {path} {detail}\n"


def _bound(value: Any) -> int | float | None:
    """A numeric bound; None when missing, and for draft 4's boolean ``exclusiveMinimum``/``exclusiveMaximum``."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def number_instance(schema: dict[str, Any], integer: bool) -> int | float:
    """The lowest value a number schema allows (a whole one for an integer schema), or 0 when it has no lower
    bound and allows 0. Exclusive bounds count, numeric or draft 4's boolean flags."""
    low, low_open = _bound(schema.get("minimum")), False
    exclusive = schema.get("exclusiveMinimum")
    if exclusive is True:
        low_open = low is not None
    elif _bound(exclusive) is not None and (low is None or exclusive >= low):
        low, low_open = exclusive, True
    high, high_open = _bound(schema.get("maximum")), False
    exclusive = schema.get("exclusiveMaximum")
    if exclusive is True:
        high_open = high is not None
    elif _bound(exclusive) is not None and (high is None or exclusive <= high):
        high, high_open = exclusive, True
    if integer:
        if low is not None:
            return math.floor(low) + 1 if low_open else math.ceil(low)
        if high is not None:
            return min(0, math.ceil(high) - 1 if high_open else math.floor(high))
        return 0
    if low is not None:
        if not low_open:
            return low
        if high is None or low + 1 < high or (low + 1 == high and not high_open):
            return low + 1
        return (low + high) / 2
    if high is not None and (high < 0 or (high == 0 and high_open)):
        return high - 1 if high_open else high
    return 0


def _resolve(ref: str, defs: dict[str, Any]) -> dict[str, Any]:
    return defs.get(ref.rsplit("/", 1)[-1], {})


def schema_instance(schema: Any, defs: dict[str, Any], depth: int = 0) -> Any:
    """A smallest instance of a JSON schema: required properties only, minimum lengths and counts."""
    if depth > MAX_SCHEMA_DEPTH or not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        return schema_instance(_resolve(schema["$ref"], defs), defs, depth + 1)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if schema.get(key):
            options = [option for option in schema[key] if option.get("type") != "null"] or schema[key]
            return schema_instance(options[0], defs, depth + 1)
    if schema.get("allOf"):
        merged: dict[str, Any] = {}
        properties: dict[str, Any] = {}
        required: list[str] = []
        for part in [*schema["allOf"], {key: value for key, value in schema.items() if key != "allOf"}]:
            part = _resolve(part["$ref"], defs) if "$ref" in part else part
            properties.update(part.get("properties", {}))
            required.extend(name for name in part.get("required", []) if name not in required)
            merged.update(part)
        if properties:
            merged["properties"] = properties
        if required:
            merged["required"] = required
        return schema_instance(merged, defs, depth + 1)
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((name for name in kind if name != "null"), "null")
    if kind == "string":
        return "text".ljust(schema.get("minLength", 0), "x")[: schema.get("maxLength", 10**6)]
    if kind in ("integer", "number"):
        return number_instance(schema, kind == "integer")
    if kind == "boolean":
        return False
    if kind == "array":
        return [schema_instance(schema.get("items", {}), defs, depth + 1) for _ in range(schema.get("minItems", 0))]
    if kind == "null":
        return None
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        required = schema.get("required", list(properties))
        return {key: schema_instance(value, defs, depth + 1) for key, value in properties.items() if key in required}
    return None


def _prompt_text(request: dict[str, Any], *, system_only: bool = False) -> str:
    """The text parts of a chat (Ollama or OpenAI shape) or generate request, joined; only the system
    messages when ``system_only``, where the app puts its instructions."""
    parts: list[str] = []
    if isinstance(request.get("system"), str):
        parts.append(request["system"])
    if isinstance(request.get("prompt"), str) and not system_only:
        parts.append(request["prompt"])
    for message in request.get("messages") or []:
        if not isinstance(message, dict) or (system_only and message.get("role") != "system"):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(part.get("text", "") for part in content if isinstance(part, dict))
    return "\n".join(parts)


def answer(request: dict[str, Any]) -> tuple[str, str]:
    """The reply text for a chat or generate request, and what kind of answer it is (for the log)."""
    wanted = request.get("format")
    response_format = request.get("response_format")
    if isinstance(response_format, dict):
        schema = (response_format.get("json_schema") or {}).get("schema")
        if isinstance(schema, dict):
            wanted = schema
    if isinstance(wanted, dict):
        return json.dumps(schema_instance(wanted, wanted.get("$defs", wanted.get("definitions", {})))), "schema"
    whole = _prompt_text(request)
    for text in (_prompt_text(request, system_only=True), whole):
        for words, reply, name in ANSWERS:
            if words in text:
                return json.dumps(reply(whole) if callable(reply) else reply), name
    if wanted:
        return json.dumps(CLASSIFICATION_ANSWER), "document"
    return PLAIN_ANSWER, "plain"


def answer_text(request: dict[str, Any]) -> str:
    return answer(request)[0]


def _texts(request: dict[str, Any]) -> list[str]:
    value = request.get("input")
    if isinstance(value, str):
        return [value]
    return list(value or [])


class Handler(BaseHTTPRequestHandler):
    """Answers the Ollama and OpenAI-compatible routes listed in the module docstring."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        """The request lines are written by ``_log`` instead, one per call with what was answered."""

    def _log(self, model: Any, kind: str) -> None:
        sys.stderr.write(f"fake-model {self.command} {self.path} model={model} answer={kind}\n")

    def _log_embed(self, model: Any, count: int) -> None:
        sys.stderr.write(f"fake-model {self.command} {self.path} model={model} texts={count}\n")

    def _log_failure(self, detail: str) -> None:
        sys.stderr.write(failure_line(getattr(self, "command", None), getattr(self, "path", None), detail))

    def log_error(self, format: str, *args: Any) -> None:
        """http.server's own refusals (a bad request line, an unsupported method) are failed calls too."""
        self._log_failure(format % args)

    def _answer_or_fail(self, route: Callable[[], None]) -> None:
        """Runs one route. An error is a failed call: logged, and answered 500 rather than left as the dropped
        connection http.server would leave, which a client can read as an empty answer or retry unseen."""
        try:
            route()
        except Exception as error:  # noqa: BLE001 - any error in a route is a failed call, reported like the rest
            self._log_failure(f"error={type(error).__name__}")
            self.close_connection = True
            try:
                self._send({"error": type(error).__name__}, code=500)
            except OSError:
                pass

    def _send(self, body: Any, code: int = 200, content_type: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self) -> None:
        self._answer_or_fail(lambda: self._send(b""))

    def do_GET(self) -> None:
        self._answer_or_fail(self._get)

    def do_POST(self) -> None:
        self._answer_or_fail(self._post)

    def _get(self) -> None:
        if self.path.startswith("/api/tags"):
            self._send({"models": [{"name": name, "model": name} for name in MODELS]})
        elif self.path.startswith("/api/version"):
            self._send({"version": "0.5.0"})
        else:
            self._send(b"Ollama is running", content_type="text/plain")

    def _post(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        path = self.path
        model = request.get("model")
        if path == "/api/show":
            self._send({
                "details": {"family": "qwen2"},
                "model_info": {"general.architecture": "qwen2", "qwen2.context_length": 32768},
                "capabilities": ["completion", "tools"],
            })
            return
        if path == "/api/embed":
            texts = _texts(request)
            self._log_embed(model, len(texts))
            self._send({
                "model": model, "embeddings": [embedding(text) for text in texts],
                "total_duration": 1, "load_duration": 1, "prompt_eval_count": 1,
            })
            return
        if path == "/api/embeddings":
            self._log_embed(model, 1)
            self._send({"embedding": embedding(request.get("prompt", ""))})
            return
        if path == "/v1/embeddings":
            texts = _texts(request)
            self._log_embed(model, len(texts))
            self._send({
                "object": "list", "model": model,
                "data": [{"object": "embedding", "index": i, "embedding": embedding(text)} for i, text in enumerate(texts)],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            })
            return
        text, kind = answer(request)
        self._log(model, kind)
        if path == "/v1/chat/completions":
            self._send({
                "id": "fake", "object": "chat.completion", "created": 1, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
            return
        chat = path == "/api/chat"
        done = {
            "model": model, "created_at": "2026-01-01T00:00:00Z", "done": True, "done_reason": "stop",
            "total_duration": 1, "load_duration": 1, "prompt_eval_count": 1, "prompt_eval_duration": 1,
            "eval_count": 1, "eval_duration": 1,
        }
        body = {"message": {"role": "assistant", "content": text}} if chat else {"response": text}
        if request.get("stream", True):
            empty = {"message": {"role": "assistant", "content": ""}} if chat else {"response": ""}
            lines = [{**done, **body, "done": False, "done_reason": None}, {**done, **empty}]
            self._send(("\n".join(json.dumps(line) for line in lines) + "\n").encode(), content_type="application/x-ndjson")
            return
        self._send({**done, **body})


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11434)
    args = parser.parse_args(argv)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
