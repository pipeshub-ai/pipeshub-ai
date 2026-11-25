"""WP98-FU-4: the producer declares scratch.

``ph tools call`` saves a large result itself into one harness-owned folder (``notes/tool-results/``), prints its path
and an excerpt, and the scratch rule matches that folder only, so a person's own ``notes/plan.json`` is durable. The
engine instructions, the result cap's hint and ``ph`` all name the folder from the one constant.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING, Any

from app.harness.engines import instructions
from harness_core.gateway import result_cap
from harness_core.gateway.result_cap import cap_engine_result
from harness_core.gateway.types import McpResult
from harness_core.workspace.sync.storage_classes import (
    DURABLE,
    SCRATCH,
    TOOL_RESULTS_DIR,
    StorageClassRegistry,
)
from harness_engines.pipeshub import ph
from tests.unit.harness_engines.pipeshub.test_ph_cli import (
    PH,
    Gateway,
    folder,  # noqa: F401 - the fixture
    gateway,  # noqa: F401 - the fixture
    run,
    session,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

BIG = json.dumps({"rows": [{"id": i, "text": "x" * 40} for i in range(800)]})


def _answers(text: str) -> Any:  # noqa: ANN401
    return lambda name, arguments: {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"disposition": "executed", "retryable": False},
        "isError": False,
    }


def _workspace(root: Path) -> Path:
    workspace = root / "workspace"
    workspace.mkdir()
    return workspace


def test_wp98_fu4_1_ph_s_folder_is_the_registry_s_and_the_cap_s() -> None:
    assert ph.TOOL_RESULTS_DIR == TOOL_RESULTS_DIR
    assert ph.SAVE_ABOVE_CHARS == result_cap.ENGINE_RESULT_CAP_CHARS
    registry = StorageClassRegistry.default()
    assert registry.declared(f"{TOOL_RESULTS_DIR}/search-1.json") == SCRATCH
    assert registry.declared(f"{TOOL_RESULTS_DIR}/fetch_url-3.txt") == SCRATCH
    for person in ("notes/plan.json", "notes/web/sources.json", "notes/data.jsonl", "notes/tool-results.json"):
        assert registry.declared(person) == DURABLE, person


def test_wp98_fu4_2_a_large_result_is_saved_and_its_path_printed(
    gateway: tuple[Gateway, str],  # noqa: F811
    folder: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, url = gateway
    state.answer = _answers(BIG)
    monkeypatch.setattr(ph, "FOLDER", str(folder))
    workspace = _workspace(folder)
    code, out, err = run(["tools", "call", "jira__search_issues", "--json", "{}"], url)
    assert (code, err) == (0, "")
    path = f"{TOOL_RESULTS_DIR}/search_issues-1.json"
    assert (workspace / path).read_text() == BIG
    assert path in out and str(folder / "workspace" / path) in out
    assert len(out) < 3_000 and out.startswith(BIG[:100])
    # A second call never overwrites the first.
    run(["call", "jira__search_issues", "--json", "{}"], url)
    assert (workspace / f"{TOOL_RESULTS_DIR}/search_issues-2.json").read_text() == BIG


def test_wp98_fu4_3_save_print_small_results_and_outside_a_run(
    gateway: tuple[Gateway, str],  # noqa: F811
    folder: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, url = gateway
    monkeypatch.setattr(ph, "FOLDER", str(folder))
    state.answer = _answers(BIG)
    # Outside a run (no workspace link) nothing is saved and the result prints whole.
    assert run(["call", "search", "--json", "{}"], url)[1].strip() == BIG
    workspace = _workspace(folder)
    assert run(["call", "search", "--json", "{}", "--print"], url)[1].strip() == BIG
    state.answer = _answers("small, not JSON")
    assert run(["call", "search", "--json", "{}"], url)[1].strip() == "small, not JSON"
    code, out, _ = run(["call", "search", "--json", "{}", "--save"], url)
    assert code == 0 and f"{TOOL_RESULTS_DIR}/search-1.txt" in out
    assert (workspace / TOOL_RESULTS_DIR / "search-1.txt").read_text() == "small, not JSON"
    # The JSON envelope keeps the whole result unless --save asks; then it names the file too.
    state.answer = _answers(BIG)
    enveloped = json.loads(run(["--json", "call", "search", "--json", "{}"], url)[1])
    assert enveloped["data"]["text"] == BIG and "path" not in enveloped["data"]
    saved = json.loads(run(["--json", "call", "search", "--json", "{}", "--save"], url)[1])
    assert saved["data"]["path"] == f"{TOOL_RESULTS_DIR}/search-1.json" and saved["data"]["chars"] == len(BIG)


def test_wp98_fu4_4_a_redirect_to_a_file_keeps_the_whole_result(
    gateway: tuple[Gateway, str],  # noqa: F811
    folder: Path,  # noqa: F811
    tmp_path: Path,
) -> None:
    state, url = gateway
    state.answer = _answers(BIG)
    session(folder, url)
    workspace = _workspace(folder)
    target = tmp_path / "mine.json"
    env = {"PIPESHUB_FOLDER": str(folder), "PATH": "/usr/bin:/bin"}
    with target.open("w") as redirected:
        done = subprocess.run(
            [sys.executable, "-I", str(PH), "call", "search", "--json", "{}"],
            stdout=redirected, stderr=subprocess.PIPE, env=env, timeout=60, check=False,
        )
    assert done.returncode == 0, done.stderr
    assert target.read_text().strip() == BIG
    assert not (workspace / TOOL_RESULTS_DIR).exists()


def test_wp98_fu4_5_the_instructions_and_the_cap_name_the_folder_and_no_redirect() -> None:
    assert instructions.TOOLS_AND_LONG_WORK == (
        "This sandbox has no direct internet: reach the web and connected apps only through the PipesHub tools (the ph "
        "command or your MCP tools), such as web search and fetch when this task has them. For work with many steps, "
        "keep a short plan and your notes in a notes/ folder and update them as you go. Call tools whose results are "
        "large through ph (ph call <tool> --json '{...}'): ph saves a large result under notes/tool-results/ and "
        "prints the file's path; read only the parts you need."
    )
    capped = cap_engine_result(
        McpResult("x" * 50, is_error=False, disposition="executed", retryable=False),
        tool="github", arguments={}, max_chars=10,
    )
    assert f"under {TOOL_RESULTS_DIR}/" in capped.text and ">" not in capped.text.split("[", 1)[1]
    agents = (PH.parent / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`{TOOL_RESULTS_DIR}/`" in agents and "> notes/" not in agents
