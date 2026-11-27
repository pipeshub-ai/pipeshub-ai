"""The ``/pipeshub`` folder and ``ph`` CLI of every engine image: contract v1 (OWD-6, recs audit R3).

The image carries what never changes within a release, installed by ``install.sh``: ``/usr/local/bin/ph``
(``ph.py``), ``/pipeshub/VERSION`` (the contract version, an integer), the read-only ``/pipeshub/AGENTS.md``, and
links into the session folder, the skills and the workspace. At each turn's start the controller first checks the
image's ``VERSION`` (an image built for another contract is refused, WS-12), then seeds ``/pipeshub/session/``
(read-only to the engine) from the run's pinned grant: the tool index, the per-tool schemas, the typed stubs (``sdk``), the gateway ``ph``
talks to with the attempt's placeholder, the pinned skills, and the contract version. Nothing a run can call is
ever baked into the image. The layout is written down in ``docs/agent-harness-v2/guides/pipeshub-contract-reference.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
from importlib import resources
from typing import TYPE_CHECKING, Final

from harness_core.engines.tool_folder import ImageContract, Shelf, ToolFolder
from harness_engines.pipeshub import sdk
from harness_engines.pipeshub.toolsets import OWN_TOOLSET, toolset_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.engines.types import GatewayEndpoints
    from harness_core.gateway.types import McpTool

FOLDER: Final = "/pipeshub"
SESSION_DIR: Final = f"{FOLDER}/session"
PH_PATH: Final = "/usr/local/bin/ph"
AGENTS_FILE: Final = "AGENTS.md"
AGENTS_PATH: Final = f"{FOLDER}/{AGENTS_FILE}"
INDEX_FILE: Final = "tools.txt"
TOOLS_DIR: Final = "tools"
TOOLS_INDEX: Final = "INDEX.md"
# The typed stubs of the run's tools (WS-14). The session folder holds them on every image, so this is the path
# engines are told; the image's AGENTS.md and a /pipeshub/sdk link come with the next engine image.
SDK_PATH: Final = f"{SESSION_DIR}/{sdk.SDK_DIR}"
# C3.03 as decided by SEC1: the engines' own instruction files (CLAUDE.md, AGENTS.md project docs) stay empty and
# read-only, so each engine's base prompt names PipesHub's read-only guide instead, and the stubs beside ``ph``.
AGENTS_POINTER: Final = (
    f"PipesHub's tools also work from the shell with the ph command: {AGENTS_PATH} (read-only) says how. "
    f"Scripts can call them as typed Python or TypeScript functions under {SDK_PATH}: "
    f"{FOLDER}/{TOOLS_DIR}/{TOOLS_INDEX} says how to import them."
)
GATEWAY_FILE: Final = "gateway.json"
SKILLS_FILE: Final = "skills.txt"
VERSION_FILE: Final = "VERSION"
# The managed policy hook's program (G-25): this worker's own ``ph``, seeded read-only with each turn's session folder,
# so the hook always speaks the gateway's policy check whatever ``ph`` the engine image was built with.
HOOK_PROGRAM_FILE: Final = "ph.py"
# WP-70: this worker's ph, first on every engine's PATH (``ToolFolder.bin_dir``), so a fix to ph reaches a sandbox at
# the next turn instead of with the next signed image; the image's /usr/local/bin/ph stays behind it.
BIN_DIR: Final = "bin"
SESSION_PH: Final = f"{BIN_DIR}/ph"
SESSION_PH_PATH: Final = f"{SESSION_DIR}/{SESSION_PH}"
POLICY_HOOK_PATH: Final = f"{SESSION_DIR}/{HOOK_PROGRAM_FILE}"
SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"
# Over-cap knowledge results, written under the session folder and read through the image's ``knowledge`` link (C3.22).
KNOWLEDGE_DIR: Final = "knowledge"
KNOWLEDGE_PATH: Final = f"{FOLDER}/{KNOWLEDGE_DIR}"
# The platform's knowledge toolset (``knowledgegraph__search``, ``knowledgegraph__fetch_record``...).
KNOWLEDGE_TOOLSET: Final = "knowledgegraph"


def _resource(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


def _resource_bytes(name: str) -> bytes:
    return resources.files(__package__).joinpath(name).read_bytes()


# The contract version: the image's /pipeshub/VERSION is built from this same file. Additive changes keep it; a
# change a skill or script written for it could trip over is a new version, written beside the old one.
VERSION: Final = _resource(VERSION_FILE).strip()
CONTRACT_VERSION: Final = int(VERSION)
SUPPORTED_CONTRACTS: Final = frozenset({VERSION})


def index_line(name: str, description: str) -> str:
    """One tool as the index lists it: its name and the first sentence of its description, on one line."""
    first = " ".join(description.split()).split(". ")[0].rstrip(".")
    return f"{name}: {first}" if first else name


def tool_index(tools: Iterable[McpTool]) -> str:
    """The session's index: one line per granted tool, in name order (the shape measured at 5,620 tokens for 385)."""
    lines = sorted(index_line(t.name, t.description) for t in tools)
    return "".join(f"{line}\n" for line in lines)


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", part).lstrip(".") or "_"


def tool_schema(tool: McpTool) -> dict[str, JsonValue]:
    """A tool's ``<tool>.schema.json``: its input schema as a JSON Schema 2020-12 document, titled with the name
    ``ph tools call`` takes and described as the gateway describes it."""
    body = {k: v for k, v in tool.input_schema.items() if k not in ("$schema", "title", "description")}
    return {"$schema": SCHEMA_DIALECT, "title": tool.name, "description": tool.description, "type": "object", **body}


def schema_paths(tools: Iterable[McpTool]) -> dict[str, str]:
    """Tool name -> its schema's path under ``tools/``. Two names that clean to one file name keep both, each
    with a digest of its name."""
    wanted: dict[str, str] = {}
    for tool in sorted(tools, key=lambda t: t.name):
        toolset, short = toolset_of(tool.name)
        wanted[tool.name] = f"{_safe(toolset)}/{_safe(short)}"
    taken: dict[str, int] = {}
    for path in wanted.values():
        taken[path] = taken.get(path, 0) + 1
    return {
        name: (path if taken[path] == 1 else f"{path}-{hashlib.sha256(name.encode()).hexdigest()[:8]}") + ".schema.json"
        for name, path in wanted.items()
    }


def tools_index_md(tools: Iterable[McpTool]) -> str:
    """``tools/INDEX.md``: each toolset on one line, with its tools' short names, and how to use ``ph tools``."""
    by_toolset: dict[str, list[str]] = {}
    for tool in tools:
        toolset, short = toolset_of(tool.name)
        by_toolset.setdefault(toolset, []).append(short)
    count = sum(len(names) for names in by_toolset.values())
    lines = [
        "# Tools of this run",
        "",
        f"{count} tools in {len(by_toolset)} toolsets. A tool's full name is <toolset>__<tool> (PipesHub's own "
        "tools have no prefix).",
        "Find one with `ph tools search <words>`, read its input with `ph tools describe <name>` or "
        "tools/<toolset>/<tool>.schema.json, and call it with `ph tools call <name> --json '<arguments>'`.",
        f"Scripts call the same tools as typed functions (each is one `ph tools call`): Python with "
        f"`PYTHONPATH={SDK_PATH}/python` and `from {sdk.PACKAGE} import <toolset>`, TypeScript or JavaScript with "
        f"`import {{ <toolset> }} from \"{SDK_PATH}/ts/index.mjs\"`.",
        "",
    ]
    lines += [f"- {toolset} ({len(names)}): {', '.join(sorted(names))}" for toolset, names in sorted(by_toolset.items())]
    return "\n".join(lines) + "\n"


def tools_files(tools: tuple[McpTool, ...]) -> tuple[str, dict[str, bytes]]:
    """The ``tools/`` folder: its name in the session folder (a digest of what it holds, so a re-seeded sandbox
    switches the ``tools`` link at once and never shows a tool the turn no longer has) and its files."""
    paths = schema_paths(tools)
    files = {TOOLS_INDEX: tools_index_md(tools).encode()}
    for tool in tools:
        files[paths[tool.name]] = (json.dumps(tool_schema(tool), indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode() + b"\0" + hashlib.sha256(files[path]).digest())
    return f"{TOOLS_DIR}-{digest.hexdigest()[:16]}", files


def session_files(
    tools: tuple[McpTool, ...], endpoints: GatewayEndpoints, skills: tuple[str, ...] = ()
) -> Mapping[str, bytes]:
    """``/pipeshub/session/`` for one turn: the index, the tools folder, the gateway and placeholder (never a token:
    the egress proxy swaps the placeholder on the tools route only), the pinned skills, the contract version and the
    managed policy hook's program.
    ``tools`` is a link to the tools folder (``session_links``)."""
    gateway = {"version": VERSION, "url": endpoints.tools_mcp_url, "token": endpoints.tools_placeholder}
    folder, tool_files = tools_files(tools)
    stubs_folder, stub_files = sdk.sdk_files(tools, (folder, schema_paths(tools)))
    return {
        **{f"{stubs_folder}/{path}": content for path, content in stub_files.items()},
        INDEX_FILE: tool_index(tools).encode(),
        GATEWAY_FILE: (json.dumps(gateway, indent=2) + "\n").encode(),
        SKILLS_FILE: "".join(f"{name}\n" for name in sorted(skills)).encode(),
        VERSION_FILE: f"{VERSION}\n".encode(),
        HOOK_PROGRAM_FILE: _resource_bytes(HOOK_PROGRAM_FILE),
        SESSION_PH: _resource_bytes(HOOK_PROGRAM_FILE),
        **{f"{folder}/{path}": content for path, content in tool_files.items()},
    }


def session_links(files: Mapping[str, bytes]) -> Mapping[str, str]:
    """The links the session folder holds beside ``files``: ``tools`` to this turn's tools folder and ``sdk`` to its
    stubs (WS-14)."""
    links: dict[str, str] = {}
    for name in (TOOLS_DIR, sdk.SDK_DIR):
        for folder in {path.split("/", 1)[0] for path in files if path.startswith(f"{name}-")}:
            links[name] = folder
    return links


def pipeshub_tool_folder() -> ToolFolder:
    contract = ImageContract(
        f"{FOLDER}/{VERSION_FILE}",
        SUPPORTED_CONTRACTS,
        remedy="rebuild the engine images (deployment/sandbox-images/pipeshub/build.sh)",
    )
    return ToolFolder(
        SESSION_DIR,
        session_files,
        contract,
        links=session_links,
        shelf=knowledge_shelf(),
        policy_hook=POLICY_HOOK_PATH,
        executables=(SESSION_PH,),
        bin_dir=BIN_DIR,
    )


def knowledge_tool(name: str) -> bool:
    return toolset_of(name)[0] == KNOWLEDGE_TOOLSET


def knowledge_shelf() -> Shelf:
    """Knowledge results past the engine's result cap, kept whole under ``/pipeshub/knowledge`` (C3.22, KN-06)."""
    return Shelf(KNOWLEDGE_DIR, KNOWLEDGE_PATH, knowledge_tool)


__all__ = [
    "AGENTS_FILE",
    "AGENTS_PATH",
    "AGENTS_POINTER",
    "CONTRACT_VERSION",
    "FOLDER",
    "GATEWAY_FILE",
    "BIN_DIR",
    "SESSION_PH",
    "SESSION_PH_PATH",
    "HOOK_PROGRAM_FILE",
    "INDEX_FILE",
    "KNOWLEDGE_DIR",
    "KNOWLEDGE_PATH",
    "KNOWLEDGE_TOOLSET",
    "OWN_TOOLSET",
    "PH_PATH",
    "POLICY_HOOK_PATH",
    "SCHEMA_DIALECT",
    "SDK_PATH",
    "SESSION_DIR",
    "SKILLS_FILE",
    "SUPPORTED_CONTRACTS",
    "TOOLS_DIR",
    "TOOLS_INDEX",
    "VERSION",
    "VERSION_FILE",
    "index_line",
    "knowledge_shelf",
    "knowledge_tool",
    "pipeshub_tool_folder",
    "schema_paths",
    "session_files",
    "session_links",
    "tool_index",
    "tool_schema",
    "tools_files",
    "tools_index_md",
    "toolset_of",
]
