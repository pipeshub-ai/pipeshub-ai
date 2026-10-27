"""app.utils.html_markdown: callers get a ``str`` whatever the library returns."""

import ast
import logging
import time
from pathlib import Path

import pytest

from app.utils import html_markdown
from app.utils.html_markdown import html_to_markdown


def test_returns_markdown_text_not_a_result_object() -> None:
    out = html_to_markdown("<h1>Title</h1><p>a <b>bold</b> word</p>")
    assert isinstance(out, str)
    assert out.startswith("# Title")
    assert "**bold**" in out


def test_result_is_usable_where_a_str_is_required() -> None:
    out = html_to_markdown("<p>café</p>")
    assert out.encode("utf-8") == "café\n".encode("utf-8")
    assert " ".join(["x", out]) == "x café\n"


# --- what is kept -----------------------------------------------------------
# html-to-markdown 3.x drops these by default. Connector bodies do not pass
# through clean_html, so a drop here is a silent loss of ticket or article text.


@pytest.mark.parametrize(
    "html, expected",
    [
        ("<header>Head text</header><p>keep</p>", "Head text"),
        ("<nav><a href='/x'>Nav link</a></nav><p>keep</p>", "Nav link"),
        ("<form><label>Form label</label></form><p>keep</p>", "Form label"),
        ("<div hidden>Hidden attr</div><p>keep</p>", "Hidden attr"),
        ('<p style="display:none">Display none</p><p>keep</p>', "Display none"),
        ("<p>Plain paragraph</p>", "Plain paragraph"),
    ],
)
def test_text_in_chrome_and_hidden_elements_is_kept(html: str, expected: str) -> None:
    assert expected in html_to_markdown(html)


# --- degenerate input -------------------------------------------------------


@pytest.mark.parametrize("html", ["", "   ", " \n\t "])
def test_empty_and_whitespace_input_give_empty_text(html: str) -> None:
    assert html_to_markdown(html) == ""


@pytest.mark.parametrize("value", [None, b"<p>x</p>", 5, ["<p>x</p>"]])
def test_non_str_input_is_rejected_with_a_clear_error(value: object) -> None:
    with pytest.raises(TypeError, match="html must be str"):
        html_to_markdown(value)  # type: ignore[arg-type]


# --- one huge block ---------------------------------------------------------


def _links(n: int) -> str:
    return "w <a href='http://example.com/x'>l</a> " * n


def test_large_single_paragraph_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """~1.5 MB of inline links took 3 s in the library alone and grows quadratically."""
    seen: list[int] = []
    real = html_markdown._convert

    def spy(markup: str) -> str:
        seen.append(markup.count("<"))
        return real(markup)

    monkeypatch.setattr(html_markdown, "_convert", spy)
    html = f"<p>{_links(50_000)}</p>"
    assert len(html) > 1_400_000

    start = time.perf_counter()
    out = html_to_markdown(html)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0
    assert out.count("w l") == 50_000  # every word kept, as text
    assert max(seen, default=0) <= html_markdown.MAX_ELEMENTS_PER_BLOCK * 2  # open + close tag per element


def test_oversized_block_is_reported_once(caplog: pytest.LogCaptureFixture) -> None:
    html = f"<p>{_links(html_markdown.MAX_ELEMENTS_PER_BLOCK + 1)}</p>"
    with caplog.at_level(logging.WARNING, logger=html_markdown.logger.name):
        html_to_markdown(html)
    assert [r.getMessage() for r in caplog.records if "plain text" in r.getMessage()] == [
        f"html_to_markdown: 1 block(s) over {html_markdown.MAX_ELEMENTS_PER_BLOCK} elements were reduced to plain text"
    ]


def test_only_the_oversized_block_loses_its_markup() -> None:
    big = f"<p>{_links(html_markdown.MAX_ELEMENTS_PER_BLOCK + 1)}</p>"
    out = html_to_markdown(f"<h1>Title</h1><p>before <a href='http://e.com/u'>link</a></p>{big}<p>after <b>bold</b></p>")
    assert out.startswith("# Title\n\nbefore [link](http://e.com/u)\n\nw l w l")
    assert out.endswith("w l\n\nafter **bold**")
    assert "http://example.com/x" not in out


def test_large_document_of_small_blocks_keeps_all_markup() -> None:
    n = html_markdown.MAX_ELEMENTS_PER_BLOCK
    out = html_to_markdown("<div>" + "<p>a <b>b</b></p>" * n + "</div>")
    assert out.count("a **b**") == n


def test_large_but_linear_table_keeps_its_structure() -> None:
    rows = 5_000  # 4 elements a row: 20k, under the per-block limit, 40k tags over the quick-path limit
    html = "<table><tr><th>h1</th><th>h2</th></tr>" + "<tr><td>a</td><td><b>b</b></td></tr>" * rows + "</table>"
    out = html_to_markdown(html)
    assert out.startswith("| h1")
    assert out.count("| a ") == rows


# --- single entry point -----------------------------------------------------


def test_only_the_helper_imports_the_library() -> None:
    """Callers get a str only because they go through the helper."""
    root = Path(html_markdown.__file__).resolve().parents[1]  # backend/python/app
    helper = Path(html_markdown.__file__).resolve()
    offenders = []
    for path in root.rglob("*.py"):
        if path == helper:
            continue
        source = path.read_text(encoding="utf-8-sig")
        if "html_to_markdown" not in source:  # parsing all of app/ is slow
            continue
        for node in ast.walk(ast.parse(source)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            if any(n.split(".")[0] == "html_to_markdown" for n in names):
                offenders.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    assert offenders == []
