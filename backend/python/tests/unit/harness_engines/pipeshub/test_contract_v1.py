"""The ``/pipeshub`` contract v1 (OWD-6; C3.01, C3.02, C3.03, C3.11): WS-01, WS-04, WS-12.

The image's half of the folder is laid out by ``install.sh``, the one script every engine image runs, so these tests
run it under a temporary root and seed a turn's session folder into it as the controller does. The engine images
themselves are checked on Docker by ``test_recs_audit_real_engine::test_r3_*``.
"""

from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from harness_core.engines.controller import EngineControllerConfig
from harness_core.engines.errors import EngineContractUnsupported
from harness_core.engines.types import INPUTS_DIR
from harness_core.gateway.types import McpTool
from harness_core.sandbox.tar import config_tar
from harness_core.security.token_patterns import find_forbidden
from harness_engines import pipeshub
from harness_engines.claude_code import launch as claude_code_launch
from harness_engines.codex import launch as codex_launch
from harness_engines.opencode import launch as opencode_launch
from harness_engines.pipeshub import (
    AGENTS_PATH,
    AGENTS_POINTER,
    FOLDER,
    SCHEMA_DIALECT,
    SESSION_DIR,
    VERSION,
    pipeshub_tool_folder,
    session_files,
    tools_files,
)
from tests.unit.harness_core.engines.test_controller import IMAGE, World, task

PACKAGE = Path(pipeshub.__file__).parent
CONFIG = EngineControllerConfig(images={})
ENDPOINTS = CONFIG.endpoints("pxh-model-aaaa", "pxh-tools-bbbb")
TOOLS = (
    McpTool(
        "jira__search_issues",
        "Search Jira issues with JQL. Returns keys and the fields asked for.",
        {"type": "object", "properties": {"jql": {"type": "string"}}, "required": ["jql"]},
    ),
    McpTool("knowledgegraph__search", "Search indexed knowledge by meaning.", {"type": "object", "properties": {}}),
    McpTool("search_tools", "Find tools.", {}),
    McpTool("odd name/../x", "A name no file system path may carry as is.", {}),
)


async def _bytes(stream: object) -> bytes:
    return b"".join([chunk async for chunk in stream])  # type: ignore[attr-defined]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An engine image's root after ``install.sh``, with one turn's session folder seeded as the controller seeds it."""
    subprocess.run(
        ["sh", str(PACKAGE / "install.sh")], env={"DESTDIR": str(tmp_path), "PATH": "/usr/bin:/bin"}, check=True, timeout=60
    )
    return tmp_path


async def _seed(root: Path, tools: tuple[McpTool, ...] = TOOLS) -> None:
    folder = pipeshub_tool_folder()
    files = folder.render(tools, ENDPOINTS, ("report",))
    links = {} if folder.links is None else folder.links(files)
    archive = await _bytes(config_tar(files, links=links))
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root / SESSION_DIR.lstrip("/"), filter="tar")


async def test_ws01_the_folder_lays_out_contract_v1(root: Path) -> None:
    """WS-01: VERSION, AGENTS.md, tools/INDEX.md, per-tool schemas, skills/, inputs/, outputs/, workspace/, bin/ph."""
    await _seed(root)
    folder = root / FOLDER.lstrip("/")
    assert (folder / "VERSION").read_text().strip() == VERSION == "1"
    assert (folder / "AGENTS.md").read_bytes() == (PACKAGE / "AGENTS.md").read_bytes()
    assert (root / "usr/local/bin/ph").read_bytes() == (PACKAGE / "ph.py").read_bytes()
    links = {
        "README.md": "AGENTS.md",
        "bin/ph": "/usr/local/bin/ph",
        "tools": "session/tools",
        "tools.txt": "session/tools.txt",
        "knowledge": "session/knowledge",
        "skills": CONFIG.skills_dir,
        "workspace": CONFIG.workspace_root,
        "inputs": f"{CONFIG.workspace_root}/{INPUTS_DIR}",
        "outputs": f"{CONFIG.workspace_root}/{CONFIG.outputs_dir}",
    }
    assert {name: os.readlink(folder / name) for name in links} == links
    # Each turn's files, reached through the image's links.
    index = (folder / "tools" / "INDEX.md").read_text()
    assert "- jira (1): search_issues" in index and "- knowledgegraph (1): search" in index
    assert (folder / "tools" / "jira" / "search_issues.schema.json").is_file()
    assert (folder / "tools.txt").read_text().startswith("jira__search_issues: Search Jira issues with JQL\n")


def test_ws01_files_are_read_only_and_the_sandbox_user_can_enter_every_folder(root: Path) -> None:
    folder = root / FOLDER.lstrip("/")
    for path in (folder, folder / "session", folder / "bin"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o755, path
    for path in (folder / "AGENTS.md", folder / "VERSION"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o444, path
    assert stat.S_IMODE((root / "usr/local/bin/ph").stat().st_mode) == 0o555


def test_ws01_each_tool_schema_is_json_schema_2020_12_named_for_ph_tools_call() -> None:
    folder, files = tools_files(TOOLS)
    assert folder.startswith("tools-")
    schemas = {path: json.loads(content) for path, content in files.items() if path.endswith(".schema.json")}
    assert set(schemas) == {
        "jira/search_issues.schema.json",
        "knowledgegraph/search.schema.json",
        "pipeshub/search_tools.schema.json",
        "pipeshub/odd_name_.._x.schema.json",
    }
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)
        assert schema["$schema"] == SCHEMA_DIALECT and schema["type"] == "object"
    jira = schemas["jira/search_issues.schema.json"]
    assert jira["title"] == "jira__search_issues" and jira["required"] == ["jql"]
    assert jira["description"] == TOOLS[0].description
    Draft202012Validator(jira).validate({"jql": "project = PP"})
    # No schema path can leave the tools folder.
    assert all(".." not in Path(path).parts and not path.startswith("/") for path in files)


def test_ws01_names_that_clean_to_one_file_keep_a_file_each() -> None:
    clash = (McpTool("a__b c", "One.", {}), McpTool("a__b_c", "Two.", {}))
    _, files = tools_files(clash)
    schemas = sorted(path for path in files if path.endswith(".schema.json"))
    assert len(schemas) == 2 and all(re.fullmatch(r"a/b_c-[0-9a-f]{8}\.schema\.json", path) for path in schemas)


def test_ws01_the_turns_folder_is_byte_identical_for_the_same_grant_in_any_order() -> None:
    """WS-02/03 for the session folder: the same grant renders the same bytes, whatever order it arrives in; another
    grant renders another tools folder, so a re-seeded sandbox never shows a tool it no longer has."""
    first = session_files(TOOLS, ENDPOINTS, ("report",))
    assert session_files(tuple(reversed(TOOLS)), ENDPOINTS, ("report",)) == first
    assert tools_files(TOOLS[:2])[0] != tools_files(TOOLS)[0]


def test_ws04_agents_md_is_short_curated_and_holds_no_secret_time_or_id() -> None:
    """WS-04 (C3.02): at most 100 lines, a fixed file (the same bytes in every image and turn), and nothing that is
    a credential, a placeholder, a time or a per-request id."""
    text = (PACKAGE / "AGENTS.md").read_text()
    assert len(text.splitlines()) <= 100
    assert text.startswith("# PipesHub in this sandbox\n")
    assert find_forbidden(text) == ()
    for pattern in (
        r"pxh-(tools|model)-",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}:\d{2}(:\d{2})?\b",
        r"\b[0-9a-f]{16,}\b",
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        r"(?i)bearer\s+\S",
    ):
        assert re.search(pattern, text) is None, pattern


def _prompts(value: object) -> list[str]:
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in ([v] if k == "prompt" and isinstance(v, str) else _prompts(v))]
    return [p for v in value for p in _prompts(v)] if isinstance(value, list) else []


def test_c3_03_engines_are_pointed_at_agents_md_and_their_own_instruction_files_stay_empty() -> None:
    """C3.03 as decided (SEC1): the engines' instruction files stay empty and read-only (Claude Code's CLAUDE.md is
    off, Codex reads no project doc), and each engine's base prompt, which its system prompt is appended with, names
    PipesHub's read-only guide. The ACP opencode profile carries the same prompt as the native opencode driver."""
    assert AGENTS_PATH == f"{FOLDER}/AGENTS.md" and AGENTS_PATH in AGENTS_POINTER
    for launch in (claude_code_launch, codex_launch, opencode_launch):
        assert launch.BASE_PROMPT.endswith(AGENTS_POINTER), launch.__name__
    assert claude_code_launch.system_prompt("Task.").startswith(claude_code_launch.BASE_PROMPT)
    assert claude_code_launch.FIXED_ENV["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    agents = json.loads((PACKAGE.parent / "acp_agents.json").read_text())["agents"]
    (acp_opencode,) = [a for a in agents if a["id"] == "opencode"]
    prompts = _prompts(acp_opencode)
    assert prompts and set(prompts) == {opencode_launch.BASE_PROMPT}


@pytest.mark.parametrize("declared", [None, b"1.1.0\n", b"2\n", b"\n"])
async def test_ws12_an_image_built_for_another_contract_is_refused_before_the_engine_starts(declared: bytes | None) -> None:
    """WS-12 (C3.11): the controller reads the image's /pipeshub/VERSION before it writes anything; an image without
    one, or with a version this worker does not write (a -ph2 image says 1.1.0), ends the turn with
    ``engine_contract_unsupported`` and the engine never starts."""
    files = {} if declared is None else {f"{FOLDER}/VERSION": declared}
    world = World(config_overrides={"tool_folder": pipeshub_tool_folder()}, image_files={IMAGE: files})
    with pytest.raises(EngineContractUnsupported) as refused:
        await world.controller.run_turn(world.run(), task(0, "say ok"))
    assert refused.value.reason_code == "engine_contract_unsupported"
    assert "this worker writes contract 1" in str(refused.value)
    assert "deployment/sandbox-images/pipeshub/build.sh" in str(refused.value)
    assert world.adapter.files_at_open == []  # pyright: ignore[reportAttributeAccessIssue]
    assert not any(call[0] == "seed_tar" for call in world.broker.calls)
    assert len(world.broker.released) == 1


async def test_ws12_the_image_this_worker_writes_for_runs() -> None:
    world = World(config_overrides={"tool_folder": pipeshub_tool_folder()}, image_files={IMAGE: {f"{FOLDER}/VERSION": b"1\n"}})
    outcome = await world.controller.run_turn(world.run(), task(0, "say ok"))
    assert outcome.reason == "completed"
