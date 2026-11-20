"""The spec a body is checked against takes a downstream build's spec files, merged as the app merges them, and no
extension may reopen an object the shared spec closes (response-validation/helper/openapi_schema_validator.py)."""

from __future__ import annotations

import copy
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "response-validation" / "helper") not in sys.path:
    sys.path.insert(0, str(ROOT / "response-validation" / "helper"))

import openapi_schema_validator as validator

from helper import suite_extensions

pytestmark = pytest.mark.unit

# A createAgent body as the shared agent service returns it (backend/python app/modules/agents/service).
CREATED = {
    "status": "success",
    "message": "Agent created successfully",
    "agent": {
        "_key": "108fab95-b95f-4e3a-ae35-29f87da4790d",
        "name": "Offer drafter",
        "description": "AI agent for task automation",
        "startMessage": "Hello! How can I help you today?",
        "systemPrompt": "You are a workplace productivity assistant. Help users with their connected work tools.",
        "instructions": None,
        "models": [],
        "tags": [],
        "webSearch": None,
        "defaultReasoningEffort": None,
        "apiToolsetTools": [],
        "isActive": True,
        "isServiceAccount": False,
        "serviceAccountUserId": None,
        "sendUserContext": True,
        "createdBy": "u-alice",
        "updatedBy": None,
        "createdAtTimestamp": 1795182801853,
        "updatedAtTimestamp": 1795182801853,
        "isDeleted": False,
        "toolsets": [],
        "mcpServers": [],
        "knowledge": [],
        "skills": [],
    },
    "warnings": None,
}


def _with_agent_fields(**fields: Any) -> dict[str, Any]:
    body = copy.deepcopy(CREATED)
    body["agent"].update(fields)
    return body


def _check(monkeypatch: pytest.MonkeyPatch, doc: dict[str, Any], body: dict[str, Any]) -> None:
    monkeypatch.setattr(validator, "load_openapi_document", lambda: doc)
    validator.assert_response_matches_openapi_operation(body, "createAgent", status_code="201")


def _shared_spec() -> dict[str, Any]:
    with validator._OPENAPI_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _fragment(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fragment.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def test_the_spec_files_an_extension_lists_exist() -> None:
    overlays, fragments = validator._extension_spec_files()
    assert [p for p in (*overlays, *fragments) if not p.is_file()] == []


def test_without_an_extension_the_shared_spec_is_used_as_written(tmp_path: Path) -> None:
    assert suite_extensions.modules("openapi_schema_validator", tmp_path) == []
    shared = _shared_spec()
    assert validator.merged_spec(shared, [], []) == shared


def test_a_create_response_matches_the_spec_this_tree_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    _check(monkeypatch, validator.load_openapi_document(), CREATED)


def test_a_field_no_spec_names_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    for doc in (_shared_spec(), validator.load_openapi_document()):
        with pytest.raises(AssertionError, match="Additional properties are not allowed"):
            _check(monkeypatch, doc, _with_agent_fields(surprise="x"))


def test_a_fragment_adds_fields_to_a_closed_object(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fragment = _fragment(tmp_path, """
        components:
          schemas:
            AgentCreateResponseAgent:
              properties:
                nickname:
                  type: string
    """)
    shared = _shared_spec()
    merged = validator.merged_spec(shared, [], [fragment])

    _check(monkeypatch, merged, _with_agent_fields(nickname="drafter"))
    with pytest.raises(AssertionError, match="Additional properties are not allowed"):
        _check(monkeypatch, merged, _with_agent_fields(nickname="drafter", surprise="x"))
    with pytest.raises(AssertionError, match="Additional properties are not allowed"):
        _check(monkeypatch, shared, _with_agent_fields(nickname="drafter"))


@pytest.mark.parametrize("value", ["true", "{}", "null"])
def test_an_extension_may_not_reopen_a_closed_object(tmp_path: Path, value: str) -> None:
    fragment = _fragment(tmp_path, f"""
        components:
          schemas:
            AgentCreateResponseAgent:
              additionalProperties: {value}
    """)
    with pytest.raises(ValueError, match="loosens additionalProperties at components/schemas/AgentCreateResponseAgent"):
        validator.merged_spec(_shared_spec(), [], [fragment])
    with pytest.raises(ValueError, match="loosens additionalProperties"):
        validator.merged_spec(_shared_spec(), [fragment], [])


def test_overlays_and_fragments_merge_as_the_app_merges_them() -> None:
    target: dict[str, Any] = {"a": {"list": [1, 2], "keep": "x"}, "s": "old"}
    validator.merge_overlay(target, {"a": {"list": [2, 3]}, "s": "new"})
    assert target == {"a": {"list": [1, 2, 3], "keep": "x"}, "s": "new"}

    base = {"tags": [{"name": "A", "description": "a"}, {"name": "B"}], "gone": 1, "obj": {"list": [1], "x": 1}}
    merged = validator.merge_fragment(base, {"tags": [{"name": "A", "description": "A2"}, {"name": "C"}], "gone": None, "obj": {"list": [2]}})
    assert merged == {"tags": [{"name": "A", "description": "A2"}, {"name": "B"}, {"name": "C"}], "obj": {"list": [2], "x": 1}}
    assert base["gone"] == 1
