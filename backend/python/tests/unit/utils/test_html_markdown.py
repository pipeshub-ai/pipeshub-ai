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


def test_oversized_blocks_are_reported_once_per_document(caplog: pytest.LogCaptureFixture) -> None:
    big = f"<p>{_links(html_markdown.MAX_ELEMENTS_PER_BLOCK + 1)}</p>"
    with caplog.at_level(logging.WARNING, logger=html_markdown.logger.name):
        html_to_markdown(big + "<p>between</p>" + big)
    assert [r.getMessage() for r in caplog.records] == [
        f"html_to_markdown: 2 block(s) over {html_markdown.MAX_ELEMENTS_PER_BLOCK} elements were reduced to plain text"
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
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "attr", getattr(node.func, "id", "")) in {"import_module", "__import__"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names = [node.args[0].value]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            if any(n.split(".")[0] == "html_to_markdown" for n in names):
                offenders.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    assert offenders == []


# --- input the splitter cannot take as it is ---------------------------------
# Each of these returned "" or raised before, for a document the single call
# (or the plain text) could have handled.

_MANY = html_markdown.MAX_ELEMENTS_PER_BLOCK + 1_000


def test_xml_declaration_does_not_break_the_split_path(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=html_markdown.logger.name):
        out = html_to_markdown('<?xml version="1.0" encoding="utf-8"?>' + "<p><b>a</b></p>" * _MANY)
    assert out.count("**a**") == _MANY  # converted with markup, not reduced to text
    assert caplog.records == []


@pytest.mark.parametrize("filler", ["<!--x-->", " ", "<!-- c --> \n"])
def test_document_with_only_comments_and_whitespace_is_empty(filler: str) -> None:
    assert html_to_markdown(" " + filler * _MANY) == ""


def test_deep_nesting_keeps_every_word(caplog: pytest.LogCaptureFixture) -> None:
    depth = 3_000  # past what libxml2 accepts even with huge_tree
    html = "<div>" * depth + "deep text" + "</div>" * depth + "<p>x</p>" * _MANY
    with caplog.at_level(logging.WARNING, logger=html_markdown.logger.name):
        out = html_to_markdown(html)
    assert "deep text" in out
    assert out.split().count("x") == _MANY
    assert [r.getMessage() for r in caplog.records] == [
        "html_to_markdown: document lost text while parsing, converted as plain text"
    ]


def test_unclosed_nesting_keeps_every_word() -> None:
    out = html_to_markdown("<div>x" * 30_000)
    assert out.count("x") == 30_000


def test_nested_containers_are_counted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sizes come from one pass; looking one up per child, not counting per level."""
    monkeypatch.setattr(html_markdown, "MAX_ELEMENTS_PER_BLOCK", 100)
    real = html_markdown._subtree_sizes
    passes: list[int] = []
    lookups: list[object] = []
    converts: list[str] = []

    class Spy(dict):
        def __getitem__(self, key: object) -> int:
            lookups.append(key)
            return super().__getitem__(key)

    def spy_sizes(root: object) -> Spy:
        passes.append(1)
        return Spy(real(root))

    real_convert = html_markdown._convert
    monkeypatch.setattr(html_markdown, "_subtree_sizes", spy_sizes)
    monkeypatch.setattr(html_markdown, "_convert", lambda m: converts.append(m) or real_convert(m))

    out = html_to_markdown("<div>" * 60 + "<p>w</p>" * 1_000 + "</div>" * 60)

    assert out.count("w") == 1_000
    assert passes == [1]
    assert len(lookups) >= 1_000  # every child asks the precomputed table
    assert len(converts) <= 20  # batches of about 100 elements, not one per level or block


def test_input_over_the_size_cap_is_text_only_and_not_empty(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    called: list[str] = []
    monkeypatch.setattr(html_markdown, "_convert", lambda markup: called.append(markup) or "")
    monkeypatch.setattr(html_markdown, "MAX_INPUT_CHARS", 1_000)
    monkeypatch.setattr(html_markdown, "MAX_ELEMENTS_PER_BLOCK", 10)
    html = "<h1>Title</h1><script>BAD()</script><p>one <b>two</b></p>" * 30

    with caplog.at_level(logging.WARNING, logger=html_markdown.logger.name):
        out = html_to_markdown(html)

    assert called == []
    assert out.count("Title") == 30
    assert out.count("one two") == 30
    assert "BAD" not in out
    assert [r.getMessage() for r in caplog.records] == [
        "html_to_markdown: document is over 1000 characters, converted as plain text"
    ]


def test_a_text_node_over_the_parser_limit_is_kept() -> None:
    words = html_markdown.MAX_INPUT_CHARS // 4  # 5 chars a word: beyond the cap and libxml2's 10 MB node limit
    out = html_to_markdown("<p>" + "word " * words + "</p>" + "<p>x</p>" * _MANY)
    assert out.count("word") == words
    assert out.count("x") == _MANY


def test_reduced_block_leaves_out_script_and_style(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(html_markdown, "MAX_ELEMENTS_PER_BLOCK", 5)
    html = "<p><script>BAD()</script>" + "<span>w</span>" * 10 + "<style>.bad{}</style></p><p>tail</p>"
    out = html_to_markdown(html)
    assert out.count("w") == 10
    assert "BAD" not in out
    assert "bad" not in out
    assert out.endswith("tail")


def test_cap_does_not_apply_to_a_body_with_few_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """A big inline image or href is one attribute, not many elements."""
    monkeypatch.setattr(html_markdown, "MAX_INPUT_CHARS", 1_000)
    out = html_to_markdown(f'<p>see <a href="http://e.x/{"a" * 5_000}">the link</a></p>')
    assert out.startswith("see [the link](http://e.x/aaa")


def test_fallback_input_is_cut_and_the_cut_is_reported(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(html_markdown, "MAX_ELEMENTS_PER_BLOCK", 10)
    monkeypatch.setattr(html_markdown, "MAX_INPUT_CHARS", 100)
    monkeypatch.setattr(html_markdown, "MAX_FALLBACK_CHARS", 500)
    html = "".join(f"<p>word{i:04d}</p>" for i in range(200))

    with caplog.at_level(logging.WARNING, logger=html_markdown.logger.name):
        out = html_to_markdown(html)

    assert "word0000" in out
    assert "word0199" not in out
    assert [r.getMessage() for r in caplog.records][-1] == (
        f"html_to_markdown: plain text taken from the first 500 of {len(html)} characters"
    )


def test_lone_surrogate_is_handled_like_any_other_character() -> None:
    assert html_to_markdown("<p>a\ud800b</p>") == "a?b\n"
    assert html_to_markdown("<p>a\ud800b</p>" * _MANY).count("a?b") == _MANY


# --- the parse must account for every word -----------------------------------

_FILLER = "<p>some filler text of moderate length here</p>" * _MANY


@pytest.mark.parametrize("words", [1, 50, 1_000])
def test_text_lost_by_the_parser_triggers_the_plain_text_fallback(
    words: int, caplog: pytest.LogCaptureFixture
) -> None:
    """libxml2 stops at depth 2048; the lost part is a tiny share of a big document."""
    html = _FILLER + "<div>" * 2_100 + "LOSTWORD " * words + "</div>" * 2_100
    with caplog.at_level(logging.WARNING, logger=html_markdown.logger.name):
        out = html_to_markdown(html)
    assert out.count("LOSTWORD") == words
    assert [r.getMessage() for r in caplog.records] == [
        "html_to_markdown: document lost text while parsing, converted as plain text"
    ]


@pytest.mark.parametrize(
    "attribute",
    ["title='x > y > z'", 'title="x > y"', "onclick='if(a>b){go()}'", "data-x=\"it's > ok\""],
)
def test_greater_than_in_an_attribute_does_not_cause_a_fallback(
    attribute: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=html_markdown.logger.name):
        out = html_to_markdown(f"<p {attribute}><b>w</b> text</p>" * _MANY)
    assert out.count("**w** text") == _MANY
    assert caplog.records == []


def _fixture_html_files() -> list[Path]:
    here = Path(__file__).resolve()
    roots = [
        here.parent / "golden" / "html_markdown",
        here.parents[1] / "modules" / "parsers" / "fixtures",
        here.parents[2] / "fixtures" / "text_fragments" / "html",
        here.parents[4] / "integration-tests" / "parsers" / "fixtures",
        here.parents[4] / "integration-tests" / "web_fixtures" / "seed",
    ]
    return sorted(f for root in roots if root.is_dir() for f in root.rglob("*.html"))


def test_ordinary_documents_never_fall_back_to_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check is exact, so both sides must read markup the same way."""
    monkeypatch.setattr(html_markdown, "MAX_ELEMENTS_PER_BLOCK", 30)
    files = _fixture_html_files()
    assert len(files) >= 15
    fell_back = [
        f.name
        for f in files
        if html_markdown._split_convert(f.read_text(encoding="utf-8", errors="replace")) is None
    ]
    assert fell_back == []
