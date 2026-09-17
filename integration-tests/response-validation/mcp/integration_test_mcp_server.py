"""
MCP server surface — integration tests
======================================

Connects to ``{PIPESHUB_BASE_URL}/mcp`` the way an MCP host does — with an
OAuth access token from an app registered in PipesHub — and checks what the
host is shown before it calls any tool:

  * the ``initialize`` result: server info, capabilities, and the
    ``instructions`` text (the system prompt for the LLM),
  * every tool's name, description, input schema, and annotations,
  * the ``pipeshub-assistant`` prompt.

The exact text and schemas come from the pinned ``@pipeshub-ai/mcp`` package,
so they are compared against ``golden/mcp_surface_<pin>.json``. Invariants that
must hold for every version are asserted separately.

Auth:
  ``mcp_access_token`` is minted with authorization_code + PKCE (see
  ``conftest.py``); a second test uses a client_credentials token.
"""

from __future__ import annotations

import difflib
import json
import re

import jsonschema
import pytest

from helper.mcp_client import ASSISTANT_PROMPT, mcp_initialize_raw, read_mcp_surface

CURATED_TOOLS = {
    "pipeshub_sources",
    "pipeshub_chat",
    "pipeshub_search",
    "pipeshub_download_record",
    "pipeshub_get_record_content",
    "pipeshub_directory",
    "pipeshub_agents",
}

# The mcp-server repo caps every description so hosts keep room for their own
# tools (its __tests__/toolDescriptions.test.ts). Mirror the ceiling here.
DESCRIPTION_MAX_CHARS = 4000

def _diff(expected: str, actual: str, name: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile=f"golden/{name}",
            tofile=f"served/{name}",
            lineterm="",
        )
    )


def _pretty(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def _by_name(items: list[dict]) -> dict[str, dict]:
    return {item["name"]: item for item in items}


# ====================================================================
# initialize
# ====================================================================
@pytest.mark.integration
@pytest.mark.mcp
class TestMcpHandshake:
    def test_server_identity(self, mcp_surface: dict) -> None:
        assert mcp_surface["serverInfo"]["name"] == "pipeshub-mcp"
        assert mcp_surface["protocolVersion"]

    def test_capabilities_offer_tools_and_prompts(self, mcp_surface: dict) -> None:
        assert "tools" in mcp_surface["capabilities"]
        assert "prompts" in mcp_surface["capabilities"]

    def test_instructions_are_present(self, mcp_surface: dict) -> None:
        instructions = mcp_surface["instructions"]
        assert instructions.startswith("# PipesHub MCP"), instructions[:120]


# ====================================================================
# Golden comparison: instructions, descriptions, schemas, prompts
# ====================================================================
@pytest.mark.integration
@pytest.mark.mcp
class TestMcpSurfaceMatchesGolden:
    def test_instructions(self, mcp_surface: dict, mcp_golden: dict) -> None:
        expected, actual = mcp_golden["instructions"], mcp_surface["instructions"]
        assert actual == expected, "instructions differ:\n" + _diff(expected, actual, "instructions")

    def test_tool_names(self, mcp_surface: dict, mcp_golden: dict) -> None:
        assert [t["name"] for t in mcp_surface["tools"]] == [t["name"] for t in mcp_golden["tools"]]

    def test_tool_descriptions(self, mcp_surface: dict, mcp_golden: dict) -> None:
        served, golden = _by_name(mcp_surface["tools"]), _by_name(mcp_golden["tools"])
        diffs = [
            _diff(golden[n].get("description", ""), served[n].get("description", ""), f"{n}.description")
            for n in golden
            if n in served and served[n].get("description") != golden[n].get("description")
        ]
        assert not diffs, "tool descriptions differ:\n\n" + "\n\n".join(diffs)

    def test_tool_input_schemas(self, mcp_surface: dict, mcp_golden: dict) -> None:
        served, golden = _by_name(mcp_surface["tools"]), _by_name(mcp_golden["tools"])
        diffs = [
            _diff(_pretty(golden[n]["inputSchema"]), _pretty(served[n]["inputSchema"]), f"{n}.inputSchema")
            for n in golden
            if n in served and served[n]["inputSchema"] != golden[n]["inputSchema"]
        ]
        assert not diffs, "tool input schemas differ:\n\n" + "\n\n".join(diffs)

    def test_tool_annotations(self, mcp_surface: dict, mcp_golden: dict) -> None:
        served, golden = _by_name(mcp_surface["tools"]), _by_name(mcp_golden["tools"])
        mismatched = {
            n: {"golden": golden[n].get("annotations"), "served": served[n].get("annotations")}
            for n in golden
            if n in served and served[n].get("annotations") != golden[n].get("annotations")
        }
        assert not mismatched, "tool annotations differ:\n" + _pretty(mismatched)

    def test_prompts(self, mcp_surface: dict, mcp_golden: dict) -> None:
        served, golden = _by_name(mcp_surface["prompts"]), _by_name(mcp_golden["prompts"])
        assert sorted(served) == sorted(golden)
        diffs = [
            _diff(golden[n]["text"], served[n]["text"], f"prompt {n}")
            for n in golden
            if served[n]["text"] != golden[n]["text"]
        ]
        assert not diffs, "prompt text differs:\n\n" + "\n\n".join(diffs)


# ====================================================================
# Invariants that hold for every package version
# ====================================================================
@pytest.mark.integration
@pytest.mark.mcp
class TestMcpSurfaceInvariants:
    def test_exactly_the_curated_tools(self, mcp_surface: dict) -> None:
        assert {t["name"] for t in mcp_surface["tools"]} == CURATED_TOOLS

    def test_every_tool_has_description_schema_and_annotations(self, mcp_surface: dict) -> None:
        for tool in mcp_surface["tools"]:
            assert tool.get("description"), f"{tool['name']} has no description"
            assert tool["inputSchema"].get("type") == "object", f"{tool['name']} schema is not an object"
            assert tool.get("annotations"), f"{tool['name']} has no annotations"
            assert "readOnlyHint" in tool["annotations"], f"{tool['name']} lacks readOnlyHint"

    def test_input_schemas_are_valid_json_schema(self, mcp_surface: dict) -> None:
        for tool in mcp_surface["tools"]:
            jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])

    def test_descriptions_fit_the_host_budget(self, mcp_surface: dict) -> None:
        too_long = {
            t["name"]: len(t["description"])
            for t in mcp_surface["tools"]
            if len(t["description"]) > DESCRIPTION_MAX_CHARS
        }
        assert not too_long, f"descriptions over {DESCRIPTION_MAX_CHARS} chars: {too_long}"

    def test_every_referenced_tool_exists(self, mcp_surface: dict) -> None:
        """Descriptions route between tools by name. A dangling name is a call the host cannot make."""
        texts = {"instructions": mcp_surface["instructions"]}
        for tool in mcp_surface["tools"]:
            texts[tool["name"]] = tool["description"] + json.dumps(tool["inputSchema"])
        names = {t["name"] for t in mcp_surface["tools"]}
        dangling = {
            where: sorted(set(re.findall(r"pipeshub_[a-z_]+", text)) - names)
            for where, text in texts.items()
        }
        dangling = {k: v for k, v in dangling.items() if v}
        assert not dangling, f"unknown tool names referenced: {dangling}"

    def test_assistant_prompt_is_the_instructions(self, mcp_surface: dict) -> None:
        prompts = _by_name(mcp_surface["prompts"])
        assert ASSISTANT_PROMPT in prompts, sorted(prompts)
        assert prompts[ASSISTANT_PROMPT]["text"].strip() == mcp_surface["instructions"].strip()



# ====================================================================
# Auth on /mcp
# ====================================================================
@pytest.mark.integration
@pytest.mark.mcp
class TestMcpAuth:
    def test_no_token_is_401(self, mcp_base_url: str) -> None:
        assert mcp_initialize_raw(mcp_base_url, None).status_code == 401

    def test_bad_token_is_401(self, mcp_base_url: str) -> None:
        assert mcp_initialize_raw(mcp_base_url, "not-a-token").status_code == 401

    def test_authorization_code_token_is_accepted(self, mcp_base_url: str, mcp_access_token: str) -> None:
        resp = mcp_initialize_raw(mcp_base_url, mcp_access_token)
        assert resp.status_code == 200, resp.text[:300]

    def test_client_credentials_token_sees_the_same_tools(
        self, mcp_base_url: str, mcp_client_credentials_token: str, mcp_surface: dict
    ) -> None:
        """The route resolves a client_credentials token to the app owner and lists every tool."""
        other = read_mcp_surface(mcp_base_url, mcp_client_credentials_token)
        assert [t["name"] for t in other["tools"]] == [t["name"] for t in mcp_surface["tools"]]

