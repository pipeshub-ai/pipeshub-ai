"""PH-20..PH-31 (C3.12, WS-15, SEC-FS-02): ``ph ask`` and ``ph publish``.

``ph ask`` is the run's ``ask_user`` tool, one MCP ``tools/call``: the gateway shows the question card and journals the
call as it does for the engine's own MCP call. ``ph publish`` copies a file into the outputs folder and calls
``pipeshub.artifact_publish``, which the engine controller answers once the save before the call published it. Both run
against the same real HTTP test gateway as the other ``ph`` tests.
"""

from __future__ import annotations

import io
import json
import os
from typing import TYPE_CHECKING, Any

import pytest

from harness_engines.pipeshub import ph
from tests.unit.harness_engines.pipeshub.test_ph_cli import (
    SEARCH,
    Gateway,
    folder,  # noqa: F401 - the fixture
    gateway,  # noqa: F401 - the fixture
    run,
)

if TYPE_CHECKING:
    from pathlib import Path

ASK = {"name": "ask_user", "description": "Ask the person.", "inputSchema": {"type": "object"}}
ASKED_TEXT = "Your question is shown to the person. End this turn now without doing more work."


def _asked(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": ASKED_TEXT}],
        "structuredContent": {"disposition": "asked", "retryable": False, "reasonCode": "input_pending"},
        "isError": False,
    }


def _calls(state: Gateway) -> list[tuple[str, dict[str, Any]]]:
    return [
        (body["params"]["name"], body["params"]["arguments"]) for _, body in state.requests if body["method"] == "tools/call"
    ]


def test_ph20_ask_is_one_ask_user_call_with_the_options(gateway: tuple[Gateway, str]) -> None:  # noqa: F811
    state, url = gateway
    state.tools = [SEARCH, ASK]
    state.answer = _asked
    code, out, err = run(["ask", "Which", "region?", "--option", "EU", "--option", "US"], url)
    assert (code, out.strip(), err) == (0, ASKED_TEXT, "")
    assert _calls(state) == [("ask_user", {"question": "Which region?", "options": ["EU", "US"]})]
    assert state.requests[-1][0]["authorization"] == "Bearer pxh-tools-0123456789abcdef"


def test_ph21_ask_uses_the_name_the_engine_sees_the_tool_by(gateway: tuple[Gateway, str]) -> None:  # noqa: F811
    state, url = gateway
    state.tools = [SEARCH, {**ASK, "name": "pipeshub_ask_user"}]
    state.answer = _asked
    code, _, _ = run(["ask", "Go ahead?"], url)
    assert code == 0
    assert _calls(state) == [("pipeshub_ask_user", {"question": "Go ahead?"})]


def test_ph22_a_run_with_nobody_to_ask_says_so_and_calls_nothing(gateway: tuple[Gateway, str]) -> None:  # noqa: F811
    state, url = gateway
    code, out, err = run(["ask", "Anyone?"], url)
    assert (code, out) == (ph.EX_UNAVAILABLE, "")
    assert err.strip() == (
        "ph: this run cannot ask the person: nobody is in this chat to answer (a scheduled, API or sub-agent run). "
        "Decide for yourself and say what you assumed."
    )
    assert _calls(state) == []


def test_ph23_a_question_the_gateway_refuses_exits_1_with_its_words(gateway: tuple[Gateway, str]) -> None:  # noqa: F811
    state, url = gateway
    state.tools = [ASK]
    state.answer = lambda name, arguments: {
        "content": [{"type": "text", "text": "Offer at most 7 options of at most 200 characters each."}],
        "structuredContent": {"disposition": "refused", "retryable": True, "reasonCode": "invalid_question"},
        "isError": True,
    }
    code, _, err = run(["ask", "Pick", *[f"--option={i}" for i in range(8)]], url)
    assert code == ph.EX_TOOL_ERROR
    assert err.strip() == "Offer at most 7 options of at most 200 characters each."


def test_ph24_ask_json_names_the_question(gateway: tuple[Gateway, str]) -> None:  # noqa: F811
    state, url = gateway
    state.tools = [ASK]
    state.answer = _asked
    code, out, _ = run(["--json", "ask", "Ship it?", "--option", "yes"], url)
    assert code == 0
    assert json.loads(out) == {
        "ok": True,
        "command": "ask",
        "data": {"tool": "ask_user", "text": ASKED_TEXT, "asked": True, "question": "Ship it?", "options": ["yes"]},
    }


@pytest.fixture
def run_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The image's /pipeshub with its outputs link into a workspace whose out/ does not exist yet."""
    root = tmp_path / "pipeshub"
    (root / "session").mkdir(parents=True)
    (tmp_path / "workspace").mkdir()
    (root / "outputs").symlink_to(tmp_path / "workspace" / "out")
    monkeypatch.setattr(ph, "FOLDER", str(root))
    monkeypatch.chdir(tmp_path / "workspace")
    return tmp_path


def _published(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    answer = {"path": arguments["path"], "artifactId": "a-1", "version": 2, "lifecycle": "draft"}
    return {
        "content": [{"type": "text", "text": json.dumps(answer)}],
        "structuredContent": {"disposition": "executed", "retryable": False},
        "isError": False,
    }


def test_ph25_publish_copies_into_the_outputs_folder_then_asks_the_gateway(
    gateway: tuple[Gateway, str], run_folder: Path  # noqa: F811
) -> None:
    state, url = gateway
    state.answer = _published
    (run_folder / "workspace" / "report.md").write_text("# Q3")
    code, out, err = run(["publish", "report.md"], url)
    assert (code, err) == (0, "")
    assert out.strip() == "Published report.md (version 2, a draft until this task's turn ends). It is in the chat now."
    assert (run_folder / "workspace" / "out" / "report.md").read_text() == "# Q3"
    assert _calls(state) == [("pipeshub.artifact_publish", {"path": "report.md"})]


def test_ph26_publish_as_a_name_in_a_subfolder(gateway: tuple[Gateway, str], run_folder: Path) -> None:  # noqa: F811
    state, url = gateway
    state.answer = _published
    (run_folder / "workspace" / "q3.png").write_bytes(b"\x89PNG")
    code, out, _ = run(["--json", "publish", "q3.png", "--as", "charts/q3.png"], url)
    assert code == 0
    assert json.loads(out)["data"] == {
        "path": "charts/q3.png", "artifactId": "a-1", "version": 2, "lifecycle": "draft",
    }
    assert (run_folder / "workspace" / "out" / "charts" / "q3.png").read_bytes() == b"\x89PNG"


@pytest.mark.parametrize("name", ["../escape.md", "/etc/x", "a/../../b", "a//b", ".", "a\\b"])
def test_ph27_sec_fs_02_a_name_that_leaves_the_outputs_folder_is_refused_and_nothing_is_written(
    gateway: tuple[Gateway, str], run_folder: Path, name: str  # noqa: F811
) -> None:
    state, url = gateway
    (run_folder / "workspace" / "report.md").write_text("x")
    code, out, err = run(["publish", "report.md", "--as", name], url)
    assert (code, out) == (ph.EX_USAGE, "")
    assert "is not a name in the outputs folder" in err
    assert state.requests == []
    assert not (run_folder / "escape.md").exists() and not (run_folder / "workspace" / "out").exists()


def test_ph28_a_link_in_the_outputs_folder_never_takes_the_file_elsewhere(
    gateway: tuple[Gateway, str], run_folder: Path  # noqa: F811
) -> None:
    state, url = gateway
    outside = run_folder / "outside"
    outside.mkdir()
    (run_folder / "workspace" / "out").mkdir()
    (run_folder / "workspace" / "out" / "link").symlink_to(outside)
    (run_folder / "workspace" / "report.md").write_text("x")
    code, _, err = run(["publish", "report.md", "--as", "link/report.md"], url)
    assert code == ph.EX_USAGE
    assert err.strip() == "ph: link/report.md would leave the outputs folder."
    assert list(outside.iterdir()) == [] and state.requests == []


@pytest.mark.parametrize("kind", ["folder", "link", "missing"])
def test_ph29_only_a_regular_file_is_published(
    gateway: tuple[Gateway, str], run_folder: Path, kind: str  # noqa: F811
) -> None:
    state, url = gateway
    source = run_folder / "workspace" / "thing"
    if kind == "folder":
        source.mkdir()
    elif kind == "link":
        (run_folder / "workspace" / "real.md").write_text("x")
        source.symlink_to(run_folder / "workspace" / "real.md")
    code, _, err = run(["publish", "thing"], url)
    assert code == ph.EX_DATAERR
    assert "is not a regular file" in err
    assert state.requests == []


def test_ph30_a_refusal_or_an_unchanged_file_is_told_plainly(
    gateway: tuple[Gateway, str], run_folder: Path  # noqa: F811
) -> None:
    state, url = gateway
    (run_folder / "workspace" / "page.html").write_text("<p>x</p>")
    state.answer = lambda name, arguments: {
        "content": [{"type": "text", "text": "page.html was not published (output active content)."}],
        "structuredContent": {"disposition": "refused", "retryable": False, "reasonCode": "output_active_content"},
        "isError": True,
    }
    code, _, err = run(["publish", "page.html"], url)
    assert (code, err.strip()) == (ph.EX_TOOL_ERROR, "page.html was not published (output active content).")
    state.answer = lambda name, arguments: {
        "content": [{"type": "text", "text": json.dumps({"path": "page.html", "unchanged": True})}],
        "structuredContent": {"disposition": "executed", "retryable": False},
        "isError": False,
    }
    code, out, _ = run(["publish", "out/page.html", "--as", "page.html"], url)
    assert (code, out.strip()) == (0, "page.html is already published and has not changed since.")


def test_ph31_outside_a_run_publish_says_so(gateway: tuple[Gateway, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _, url = gateway
    monkeypatch.setattr(ph, "FOLDER", str(tmp_path / "nowhere"))
    (tmp_path / "a.md").write_text("x")
    code, _, err = run(["publish", str(tmp_path / "a.md")], url)
    assert code == ph.EX_CONFIG
    assert "has no outputs folder" in err


def test_ph32_help_names_ask_and_publish_in_plain_words() -> None:
    out = io.StringIO()
    assert ph.main(["--help"], out=out) == 0
    text = out.getvalue()
    assert "ask, publish" in text
    assert "ask the person a question; their answer is your next message" in text
    assert "publish a file as a deliverable of this task, now" in text
    out = io.StringIO()
    assert ph.main(["publish", "--help"], out=out) == 0
    assert "its name in the outputs folder (default: the file's own name)" in out.getvalue()
    assert os.path.basename(ph.__file__) == "ph.py"
