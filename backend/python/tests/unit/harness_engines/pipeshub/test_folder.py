"""PF-1..PF-9 (recs audit R3; contract v1, OWD-6): the ``/pipeshub`` folder.

The session folder is written at each turn from the run's pinned grant (so it lists what ``search_tools`` and
``call_tool`` reach, never a stale baked list) into a read-only mount; it names the gateway and the attempt's
placeholder, never a token. The images carry ``ph`` and the static files, built from the same package files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness_core.engines.controller import (
    EngineControllerConfig,
    engine_sandbox_spec,
    engine_warm_shapes,
)
from harness_core.gateway.types import McpTool
from harness_core.sandbox.types import MountSpec, RuntimeMode
from harness_core.workspace.sync.strategies import TAR
from harness_engines import pipeshub
from harness_engines.pipeshub import (
    CONTRACT_VERSION,
    GATEWAY_FILE,
    HOOK_PROGRAM_FILE,
    INDEX_FILE,
    SESSION_DIR,
    SESSION_PH,
    SKILLS_FILE,
    VERSION,
    VERSION_FILE,
    index_line,
    ph,
    pipeshub_tool_folder,
    session_files,
    tool_index,
    tools_files,
)
from harness_engines.pipeshub.sdk import sdk_files
from harness_engines.release import release_allowlist
from tests.unit.harness_core.engines.test_controller import GRANTED, IMAGE, World, task

REPO = Path(__file__).resolve().parents[6]
IMAGES = REPO / "deployment" / "sandbox-images"
PACKAGE = Path(pipeshub.__file__).parent
ENDPOINTS = EngineControllerConfig(images={}).endpoints("pxh-model-aaaa", "pxh-tools-bbbb")
TOOLS = (
    McpTool("slack__send_message", "Send a Slack message.\nTo a channel. Needs a channel id.", {}),
    McpTool("search", "Search the knowledge base. Answers cited passages.", {}),
    McpTool("bare", "", {}),
)


def test_pf1_the_index_is_one_line_per_tool_name_and_first_sentence_in_name_order() -> None:
    assert tool_index(TOOLS) == "bare\nsearch: Search the knowledge base\nslack__send_message: Send a Slack message\n"
    assert index_line("x", "One.  Two. ") == "x: One"


def test_pf2_the_session_folder_names_the_gateway_and_placeholder_never_a_token() -> None:
    files = session_files(TOOLS, ENDPOINTS, ("report", "deck"))
    folder, tools = tools_files(TOOLS)
    stubs_folder, stubs = sdk_files(TOOLS)
    # WP70-SESSIONPH: the worker's own ph, first on the engine's PATH.
    expected = {INDEX_FILE, GATEWAY_FILE, SKILLS_FILE, VERSION_FILE, HOOK_PROGRAM_FILE, SESSION_PH}
    assert set(files) == expected | {f"{folder}/{p}" for p in tools} | {f"{stubs_folder}/{p}" for p in stubs}
    gateway = json.loads(files[GATEWAY_FILE])
    assert gateway == {"version": VERSION, "url": ENDPOINTS.tools_mcp_url, "token": "pxh-tools-bbbb"}
    assert files[SKILLS_FILE] == b"deck\nreport\n"
    assert files[VERSION_FILE].decode().strip() == VERSION == str(CONTRACT_VERSION)
    # What ph reads is what the controller writes.
    assert ph.gateway(env={ph.URL_ENV: gateway["url"], ph.TOKEN_ENV: gateway["token"]}) == (
        ENDPOINTS.tools_mcp_url,
        "pxh-tools-bbbb",
    )


def test_pf3_the_engine_sandbox_mounts_the_session_folder_read_only_and_warm_shapes_match() -> None:
    config = EngineControllerConfig(images={}, tool_folder=pipeshub_tool_folder())
    spec = engine_sandbox_spec(
        config, image="i", mode=RuntimeMode.DOCKER_HARDENED, workspace=TAR.mount("/workspace"), labels={},
        placeholders=("pxh-model-x", "pxh-tools-y"), tools_upstream="http://tool-host/x",
    )
    assert MountSpec(SESSION_DIR, read_only=True) in spec.mounts
    assert spec.seedable(f"{SESSION_DIR}/tools.txt")
    (shape,) = engine_warm_shapes(config, ["i"], RuntimeMode.DOCKER_HARDENED)
    assert (SESSION_DIR, True) in shape.mounts


async def test_pf4_each_turn_seeds_the_folder_from_the_runs_grant() -> None:
    world = World(config_overrides={"tool_folder": pipeshub_tool_folder()}, image_files={IMAGE: {"/pipeshub/VERSION": b"1\n"}})
    outcome = await world.controller.run_turn(world.run(), task(0, "say ok"))
    assert outcome.reason == "completed"
    [(spec, _)] = world.broker.acquired
    assert MountSpec(SESSION_DIR, read_only=True) in spec.mounts
    seen = world.adapter.files_at_open[0]  # pyright: ignore[reportAttributeAccessIssue]
    index = seen[f"{SESSION_DIR}/{INDEX_FILE}"].decode()
    assert sorted(line.split(":")[0] for line in index.splitlines()) == sorted(t.name for t in GRANTED)
    gateway = json.loads(seen[f"{SESSION_DIR}/{GATEWAY_FILE}"])
    assert gateway["token"].startswith("pxh-tools-") and gateway["url"] == world.config.tools_mcp_url
    # WS-01: /pipeshub/tools is a link into the session folder, which links ``tools`` to this turn's schemas.
    folder = seen[f"{SESSION_DIR}/tools"].decode()
    schemas = [p for p in seen if p.startswith(f"{SESSION_DIR}/{folder}/") and p.endswith(".schema.json")]
    assert folder.startswith("tools-") and f"{SESSION_DIR}/{folder}/INDEX.md" in seen
    assert len(schemas) == len(GRANTED)


async def test_pf9_the_index_is_the_whole_grant_even_when_tools_list_defers_most_of_it() -> None:
    from harness_core.engines.tool_folder import granted_tools
    from harness_core.gateway.disclosure import LazyDisclosure
    from harness_core.testing.contracts.tool_endpoint import (
        BINDING,
        build_endpoint,
        claims_for,
    )
    from harness_core.testing.contracts.tool_endpoint import GRANTED as ENDPOINT_GRANT

    async def rank(query: str, limit: int) -> list[str]:
        return []

    world = build_endpoint(disclosure=LazyDisclosure(frozenset({"create", "transfer"}), rank))
    listed = {t.name for t in await world.endpoint.list_tools(claims_for(BINDING))}
    assert {"search_tools", "call_tool"} <= listed and "create" not in listed
    assert sorted(t.name for t in granted_tools(world.endpoint)) == sorted(t.name for t in ENDPOINT_GRANT)
    assert granted_tools(object()) == ()


@pytest.mark.parametrize("engine", ["opencode", "codex", "claude-code"])
def test_pf5_every_engine_image_installs_ph_and_the_static_folder_in_its_run_target(engine: str) -> None:
    dockerfile = (IMAGES / engine / "Dockerfile").read_text()
    tools_stage = dockerfile.split("FROM engine AS tools", 1)[1]
    # One script lays out the folder in every image (its layout: test_contract_v1.py, WS-01).
    for line in ("RUN --mount=type=bind,source=pipeshub,target=/build-pipeshub", "sh /build-pipeshub/install.sh"):
        assert line in tools_stage, (engine, line)
    build = (IMAGES / "engine-build.sh").read_text()
    assert "for name in ph.py AGENTS.md VERSION install.sh; do" in build
    assert 'cp "$REPO/backend/python/harness_engines/pipeshub/$name"' in build
    overlay = (IMAGES / "pipeshub" / "Dockerfile").read_text()
    assert "sh /build-pipeshub/install.sh" in overlay and "/pipeshub/VERSION" in overlay
    script = (IMAGES / "pipeshub" / "build.sh").read_text()
    assert '"$SRC/ph.py" "$SRC/AGENTS.md" "$SRC/VERSION" "$SRC/install.sh"' in script


def test_pf6_the_run_images_are_new_tags_whose_base_is_the_previous_run_image() -> None:
    script = (IMAGES / "pipeshub" / "build.sh").read_text()
    assert "sed 's/-ph[0-9][0-9]*$//'" in script
    for release in release_allowlist():
        assert re.fullmatch(rf"pxh-[a-z-]+-tools:{re.escape(release.version.version)}-ph7", release.image), release.image
        assert release.variants["tools"] == release.image


def test_pf7_agents_md_documents_every_command_and_exit_code_ph_has() -> None:
    guide = (PACKAGE / "AGENTS.md").read_text()
    for command in (
        "ph search", "ph fetch", "ph tools list", "ph tools search", "ph tools describe", "ph tools call", "--json -",
        "ph --json", "ph describe", "ph call", "/pipeshub/tools/INDEX.md", "/pipeshub/tools.txt", "/pipeshub/skills/",
        "ph skill list", "ph skill show", "ph policy check",
    ):
        assert command in guide, command
    for code in (ph.EX_OK, *ph.ERROR_CODES):
        assert f"| {code} |" in guide, code
    for name in ph.ERROR_CODES.values():
        assert f"`{name}`" in guide, name


def test_pf8_opencode_may_read_the_folder_and_names_it_as_the_package_does() -> None:
    from harness_engines.opencode.launch import PIPESHUB_DIR

    assert PIPESHUB_DIR == pipeshub.FOLDER and SESSION_DIR.startswith(f"{PIPESHUB_DIR}/")


def test_r39res_fu1_exit_75_row_says_what_the_gateway_says() -> None:
    """R39RES-FU-1 (ADR-RUN-06 D1): the image's page and the gateway's held-call result are one text. The page said "End
    your turn" while the gateway said to keep working; the row now carries the gateway's own words (``WHILE_WAITING``),
    so a change to one fails here until the other follows (and the run images move to a new ``-ph<N>`` tag)."""
    from harness_core.gateway.endpoint import WHILE_WAITING

    guide = (PACKAGE / "AGENTS.md").read_text()
    row = next(line for line in guide.splitlines() if line.startswith(f"| {ph.EX_TEMPFAIL} |"))
    assert "`approval_pending`" in row
    assert WHILE_WAITING in row
    assert "End your turn;" not in row
    source = (PACKAGE / "ph.py").read_text()
    assert "the caller must end its turn" not in source
