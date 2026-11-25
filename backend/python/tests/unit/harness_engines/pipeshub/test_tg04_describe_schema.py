"""TG-04 (C3.30): ``ph tools describe`` answers the exact JSON Schema 2020-12 file of ``/pipeshub/session/tools``.

The session folder is written by ``session_files`` as the controller seeds it; ``ph`` reads it as the engine image
runs it. The file is a valid 2020-12 schema, and the gateway checks arguments against the same schema.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from harness_core.engines.types import GatewayEndpoints
from harness_core.gateway.arguments import ArgumentCheck
from harness_core.gateway.types import McpTool
from harness_engines.pipeshub import SCHEMA_DIALECT, ph, session_files, session_links

_SEARCH = McpTool(
    "jira__search_issues",
    "Search Jira issues with JQL. Answers keys and titles.",
    {
        "type": "object",
        "properties": {
            "jql": {"type": "string", "minLength": 1, "description": "The JQL query"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            "fields": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        },
        "required": ["jql"],
        "examples": [{"jql": "project = OPS AND status = Open", "limit": 10}],
    },
)
_OWN = McpTool("knowledgegraph__search", "Search company knowledge.", {"type": "object", "properties": {"query": {"type": "string"}}})
TOOLS = (_SEARCH, _OWN)


class _Listed:
    """What the gateway lists, for ``describe``'s lookup."""

    def tools(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description, "inputSchema": dict(t.input_schema)} for t in TOOLS]


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    session = tmp_path / "session"
    endpoints = GatewayEndpoints("http://model", "openai_responses", "http://gw/k/mcp", "pxh-model-0")  # type: ignore[arg-type]
    files = session_files(TOOLS, endpoints)
    for path, content in files.items():
        target = session / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for link, target in session_links(files).items():
        os.symlink(target, session / link)
    monkeypatch.setattr(ph, "FOLDER", str(tmp_path))
    return tmp_path


def _describe(name: str) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    code = ph.main(["--json", "tools", "describe", name], out=out, err=io.StringIO(), gw=_Listed())  # type: ignore[arg-type]
    return code, json.loads(out.getvalue())


@pytest.mark.parametrize("tool", TOOLS, ids=[t.name for t in TOOLS])
def test_tg04_describe_answers_the_session_schema_file_exactly(folder: Path, tool: McpTool) -> None:
    code, envelope = _describe(tool.name)
    assert code == 0, envelope
    data = envelope["data"]
    on_disk = Path(data["schemaPath"])
    assert on_disk.is_relative_to(folder / "session" / "tools")
    assert data["schema"] == json.loads(on_disk.read_text(encoding="utf-8"))
    assert data["schema"]["$schema"] == SCHEMA_DIALECT and data["schema"]["title"] == tool.name


@pytest.mark.parametrize("tool", TOOLS, ids=[t.name for t in TOOLS])
def test_tg04_the_file_is_a_valid_2020_12_schema(folder: Path, tool: McpTool) -> None:
    _, envelope = _describe(tool.name)
    Draft202012Validator.check_schema(envelope["data"]["schema"])


def test_tg04_the_examples_ride_the_file_and_validate_against_it(folder: Path) -> None:
    _, envelope = _describe(_SEARCH.name)
    schema = envelope["data"]["schema"]
    assert schema["examples"] == [{"jql": "project = OPS AND status = Open", "limit": 10}]
    for example in schema["examples"]:
        Draft202012Validator(schema).validate(example)


@pytest.mark.parametrize(
    "arguments",
    [{"jql": "x"}, {"jql": ""}, {"limit": 3}, {"jql": "x", "limit": 99}, {"jql": "x", "fields": ["a", "a"]}],
)
def test_tg04_the_file_and_the_gateway_agree_on_every_call(folder: Path, arguments: dict[str, Any]) -> None:
    """What a script validates against the file is what the gateway accepts: one schema, two readers."""
    _, envelope = _describe(_SEARCH.name)
    by_file = Draft202012Validator(envelope["data"]["schema"]).is_valid(arguments)
    by_gateway = not ArgumentCheck().check(_SEARCH.name, _SEARCH.input_schema, arguments)
    assert by_file == by_gateway


def test_tg04_the_text_form_prints_the_same_document(folder: Path) -> None:
    out = io.StringIO()
    assert ph.main(["tools", "describe", _SEARCH.name], out=out, err=io.StringIO(), gw=_Listed()) == 0  # type: ignore[arg-type]
    printed = json.loads(out.getvalue().split("Arguments (JSON schema):\n", 1)[1])
    _, envelope = _describe(_SEARCH.name)
    assert printed == envelope["data"]["schema"]


def test_tg04_outside_a_seeded_session_describe_answers_the_gateways_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ph, "FOLDER", str(tmp_path))
    code, envelope = _describe(_OWN.name)
    assert code == 0
    assert "schema" not in envelope["data"]
    assert envelope["data"]["inputSchema"] == dict(_OWN.input_schema)
