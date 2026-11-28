"""The fake model the simulator shards run on (integration-tests/fake_model/server.py).

Every route an Ollama or OpenAI-compatible client calls must answer, the answers must be stable, and a
structured request must get a valid instance of its schema: a stack that waits on a route that is not
there hangs until the job's timeout instead of failing.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "integration-tests" / "fake_model" / "server.py"
WORKFLOW = REPO / ".github" / "workflows" / "integration-tests.yml"
APP = REPO / "backend" / "python" / "app" / "modules"
# Where each answer's prompt lives, by the answer's name in the fake's log.
PROMPT_FILES = {
    "code": APP / "extraction" / "prompt_template.py",
    "document": APP / "extraction" / "prompt_template.py",
    "merge": APP / "entity_resolution" / "prompt.py",
    "table": APP / "parsers" / "excel" / "prompt_template.py",
    "rows": APP / "parsers" / "excel" / "prompt_template.py",
    "table-summary": APP / "parsers" / "excel" / "prompt_template.py",
    "header-detection": APP / "parsers" / "excel" / "prompt_template.py",
    "headers": APP / "parsers" / "excel" / "prompt_template.py",
    "table-summary-json": APP.parent / "utils" / "indexing_helpers.py",
    "entity-select": APP / "named_entities" / "resolution.py",
}
IMPORT_TIMEOUT_S = 20

_IMPORT_ONLY = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('fake_model_server', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
)


@functools.cache
def import_problem(path: Path = SERVER) -> str | None:
    """Why importing the module is not safe, or None. In a child process, so a server started at import times out
    here instead of hanging this test run."""
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _IMPORT_ONLY, str(path)],
            capture_output=True, text=True, timeout=IMPORT_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired:
        return f"importing {path.name} did not return within {IMPORT_TIMEOUT_S} s: it starts a server at import"
    if result.returncode != 0:
        return f"importing {path.name} failed: {result.stderr.strip()[-500:]}"
    return None


def _load():
    spec = importlib.util.spec_from_file_location("fake_model_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def within(value: Any, schema: dict[str, Any]) -> bool:
    """Whether a number keeps every bound of its schema, numeric or draft 4's boolean exclusive flags."""
    low, high = schema.get("minimum"), schema.get("maximum")
    low_open, high_open = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
    if low_open is True:
        low, low_open = None, low
    if high_open is True:
        high, high_open = None, high
    return all((
        low is None or value >= low,
        high is None or value <= high,
        low_open in (None, False) or value > low_open,
        high_open in (None, False) or value < high_open,
    ))


class ImportIsSafe(unittest.TestCase):
    def test_importing_starts_no_server(self) -> None:
        problem = import_problem()
        self.assertIsNone(problem, problem)


class FakeModelServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        problem = import_problem()
        if problem:
            raise AssertionError(problem)
        cls.fake = fake = _load()

        class Quiet(fake.Handler):
            failures: ClassVar[list[str]] = []

            def _log(self, *_args: object) -> None:
                """No request lines in the test output."""

            def _log_failure(self, detail: str) -> None:
                Quiet.failures.append(detail)

        cls.handler = Quiet

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path: str) -> tuple[int, str, bytes]:
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()

    def _post(self, path: str, body: dict) -> tuple[int, str, bytes]:
        request = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()

    def _status(self, path: str, data: bytes, method: str = "POST") -> int:
        request = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def test_a_call_it_cannot_answer_is_answered_500_and_logged_as_failed(self) -> None:
        before = len(self.handler.failures)
        self.assertEqual(self._status("/api/chat", b"[1, 2]"), 500)
        self.assertEqual(self._status("/api/embed", b"{not json"), 500)
        self.assertEqual(len(self.handler.failures), before + 2, self.handler.failures)
        self.assertEqual(self._get("/api/version")[0], 200)

    def test_a_request_http_server_refuses_is_logged_as_failed(self) -> None:
        before = len(self.handler.failures)
        self.assertEqual(self._status("/api/chat", b"{}", method="PUT"), 501)
        self.assertEqual(len(self.handler.failures), before + 1, self.handler.failures)

    def test_a_failed_call_is_one_line_the_workflow_check_counts(self) -> None:
        line = self.fake.failure_line("POST", "/api/chat", "error=AttributeError")
        self.assertTrue(line.startswith(self.fake.FAILED + " "), line)
        self.assertEqual(line.count("\n"), 1, line)
        self.assertTrue(line.endswith("\n"), line)
        counted = WORKFLOW.read_text(encoding="utf-8").count(f"grep -c '^{self.fake.FAILED}'")
        self.assertEqual(counted, 2, "each leg's health check counts the fake's failed calls")

    def test_a_number_keeps_every_bound_and_an_integer_stays_whole(self) -> None:
        cases = [
            ({"type": "integer", "maximum": -1}, -1),
            ({"type": "integer", "exclusiveMaximum": 0}, -1),
            ({"type": "integer", "minimum": 0, "exclusiveMinimum": 5}, 6),
            ({"type": "integer", "minimum": 2, "maximum": 5}, 2),
            ({"type": "integer", "exclusiveMinimum": 3}, 4),
            ({"type": "integer", "minimum": 0.5}, 1),
            ({"type": "integer", "minimum": -4, "exclusiveMaximum": 2.5}, -4),
            ({"type": "integer", "maximum": 5}, 0),
            ({"type": "integer", "minimum": 0, "exclusiveMinimum": True}, 1),
            ({"type": "integer", "maximum": 0, "exclusiveMaximum": True}, -1),
            ({"type": "integer"}, 0),
            ({"type": "number", "maximum": -2.5}, -2.5),
            ({"type": "number", "exclusiveMaximum": 0}, -1),
            ({"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}, 0.5),
            ({"type": "number", "exclusiveMinimum": 0, "maximum": 0.5}, 0.25),
            ({"type": "number", "minimum": 2}, 2),
            ({"type": "number"}, 0),
        ]
        for schema, expected in cases:
            with self.subTest(schema=schema):
                value = self.fake.schema_instance(schema, {})
                self.assertEqual(value, expected)
                self.assertTrue(within(value, schema), value)
                if schema["type"] == "integer":
                    self.assertIs(type(value), int)

    def test_discovery_routes_answer(self) -> None:
        status, _, body = self._get("/api/tags")
        self.assertEqual(status, 200)
        names = {model["name"] for model in json.loads(body)["models"]}
        self.assertLessEqual({"qwen2.5:0.5b", "nomic-embed-text"}, names)
        self.assertEqual(json.loads(self._get("/api/version")[2])["version"], "0.5.0")
        self.assertIn(b"Ollama is running", self._get("/")[2])
        shown = json.loads(self._post("/api/show", {"model": "qwen2.5:0.5b"})[2])
        self.assertIn("completion", shown["capabilities"])

    def test_embedding_routes_agree_and_are_unit_length(self) -> None:
        embed = json.loads(self._post("/api/embed", {"model": "nomic-embed-text", "input": ["alpha", "beta"]})[2])
        single = json.loads(self._post("/api/embed", {"model": "nomic-embed-text", "input": "alpha"})[2])
        legacy = json.loads(self._post("/api/embeddings", {"model": "nomic-embed-text", "prompt": "alpha"})[2])
        openai = json.loads(self._post("/v1/embeddings", {"model": "nomic-embed-text", "input": ["alpha"]})[2])
        alpha = embed["embeddings"][0]
        self.assertEqual(len(alpha), 64)  # the number the fake promises; not its own constant
        self.assertAlmostEqual(sum(x * x for x in alpha), 1.0, places=9)
        self.assertEqual(single["embeddings"][0], alpha)
        self.assertEqual(legacy["embedding"], alpha)
        self.assertEqual(openai["data"][0]["embedding"], alpha)
        self.assertNotEqual(embed["embeddings"][1], alpha)

    def test_chat_streams_an_answer_then_a_done_line(self) -> None:
        status, content_type, body = self._post("/api/chat", {"model": "qwen2.5:0.5b", "messages": []})
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/x-ndjson")
        lines = [json.loads(line) for line in body.decode().splitlines()]
        self.assertEqual([line["done"] for line in lines], [False, True])
        self.assertEqual(lines[0]["message"]["content"], self.fake.PLAIN_ANSWER)

    def test_chat_without_stream_and_generate(self) -> None:
        chat = json.loads(self._post("/api/chat", {"model": "m", "messages": [], "stream": False})[2])
        self.assertTrue(chat["done"])
        self.assertEqual(chat["message"]["content"], self.fake.PLAIN_ANSWER)
        generate = json.loads(self._post("/api/generate", {"model": "m", "prompt": "x", "stream": False})[2])
        self.assertEqual(generate["response"], self.fake.PLAIN_ANSWER)

    def test_format_json_gets_the_classification_answer(self) -> None:
        reply = json.loads(self._post("/api/chat", {"model": "m", "messages": [], "stream": False, "format": "json"})[2])
        self.assertEqual(json.loads(reply["message"]["content"]), self.fake.CLASSIFICATION_ANSWER)

    def test_schema_in_format_gets_an_instance(self) -> None:
        schema = {
            "type": "object",
            "required": ["name", "tags", "kind", "item", "count"],
            "properties": {
                "name": {"type": "string", "minLength": 6},
                "tags": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                "kind": {"enum": ["a", "b"]},
                "item": {"$ref": "#/$defs/Item"},
                "count": {"type": "integer", "exclusiveMinimum": 3},
                "optional": {"type": "string"},
            },
            "$defs": {"Item": {"anyOf": [{"type": "null"}, {"type": "object", "properties": {"id": {"type": "string"}}}]}},
        }
        reply = json.loads(self._post("/api/chat", {"model": "m", "messages": [], "stream": False, "format": schema})[2])
        value = json.loads(reply["message"]["content"])
        self.assertEqual(set(value), {"name", "tags", "kind", "item", "count"})
        self.assertGreaterEqual(len(value["name"]), 6)
        self.assertEqual(len(value["tags"]), 2)
        self.assertEqual(value["kind"], "a")
        self.assertEqual(value["item"], {"id": "text"})
        self.assertEqual(value["count"], 4)

    def test_openai_chat_reads_response_format(self) -> None:
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
        body = {
            "model": "m", "messages": [],
            "response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": schema}},
        }
        reply = json.loads(self._post("/v1/chat/completions", body)[2])
        self.assertEqual(json.loads(reply["choices"][0]["message"]["content"]), {"ok": False})

    def test_schema_instance_handles_all_of_and_definitions(self) -> None:
        schema = {
            "allOf": [{"$ref": "#/definitions/Base"}, {"properties": {"extra": {"type": "number", "minimum": 2}}}],
            "required": ["base", "extra"],
            "definitions": {"Base": {"type": "object", "properties": {"base": {"type": "boolean"}}}},
        }
        self.assertEqual(self.fake.answer_text({"format": schema}), json.dumps({"base": False, "extra": 2}))

    def test_the_pipeline_prompts_get_their_classification_without_a_format(self) -> None:
        document = {"messages": [
            {"role": "system", "content": "# Task:\nYou are processing a document. Your task is to classify the document departments, ..."},
            {"role": "user", "content": "Record: x\nDocument Content: hello"},
        ]}
        code = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "You are analysing one source file and producing structured metadata for a code search index."},
        ]}]}
        self.assertEqual(json.loads(self.fake.answer_text(document)), self.fake.CLASSIFICATION_ANSWER)
        self.assertEqual(json.loads(self.fake.answer_text(code)), self.fake.CODE_CLASSIFICATION_ANSWER)
        self.assertEqual(self.fake.answer_text({"messages": [{"role": "user", "content": "hi"}]}), self.fake.PLAIN_ANSWER)
        reply = json.loads(self._post("/api/chat", {**document, "model": "m", "stream": False})[2])
        self.assertEqual(json.loads(reply["message"]["content"]), self.fake.CLASSIFICATION_ANSWER)

    def test_the_words_it_matches_are_still_in_the_pipeline_prompts(self) -> None:
        """A reworded prompt would quietly get the plain answer and fail its structured call on every record."""
        self.assertEqual({name for _, _, name in self.fake.ANSWERS}, set(PROMPT_FILES))
        for words, _, name in self.fake.ANSWERS:
            with self.subTest(prompt=name):
                text = PROMPT_FILES[name].read_text()
                self.assertIn(words, text, f"{PROMPT_FILES[name].name} no longer holds the {name} prompt's words")

    def test_answers_that_depend_on_the_prompt(self) -> None:
        merge = self.fake.merge_decisions('# Items\n[\n{"i":0,"name":"a","kind":"topic","matches":[]},\n{"i":2,"name":"b"}\n]')
        self.assertEqual([d["i"] for d in merge["decisions"]], [0, 2])
        self.assertTrue(all(d["same"] is False for d in merge["decisions"]))
        self.assertEqual(self.fake.merge_decisions("no items here"), self.fake.MERGE_DECISIONS_ANSWER)
        self.assertEqual(len(self.fake.generated_headers("REQUIRED NUMBER OF COLUMNS: 4\n")["headers"]), 4)
        self.assertEqual(len(self.fake.generated_headers("Number of columns: 2")["headers"]), 2)
        select = self.fake.named_entity_selection("For each line pick a candidate id or NEW. Return JSON.\nAcme | in Acme Corp | e1=Acme\nBolt | x | e2=Bolt")
        self.assertEqual(select, {"decisions": [{"name": "Acme", "id": "NEW"}, {"name": "Bolt", "id": "NEW"}]})

    def test_the_instructions_decide_not_a_record_that_quotes_another_prompt(self) -> None:
        request = {"messages": [
            {"role": "system", "content": "You maintain a knowledge-graph taxonomy for one organisation. ..."},
            {"role": "user", "content": "This page quotes: classify the document departments"},
        ]}
        self.assertEqual(self.fake.answer(request)[1], "merge")
        quoted = {"messages": [{"role": "user", "content": "classify the document departments, said the page"}]}
        self.assertEqual(self.fake.answer(quoted)[1], "document")  # no system message: the whole text decides


if __name__ == "__main__":
    unittest.main()
