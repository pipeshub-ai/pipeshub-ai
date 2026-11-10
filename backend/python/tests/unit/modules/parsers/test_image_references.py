"""Image-reference scanning: output parity, the forms it now finds, and linear time.

``legacy_extract_and_replace_images`` below is the regular-expression
implementation that this module replaced. It is the oracle for "the output of
every form that was recognised before is unchanged".
"""
from __future__ import annotations

import random
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from bs4 import BeautifulSoup

from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.markdown.image_references import (
    TooManyImages,
    extract_and_replace_images,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

# --- the previous implementation (oracle) ------------------------------------
_LEGACY_HTML_IMG_TAG_RE = re.compile(
    r"""<img"""
    r"""(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*"""
    r"""(?:\s*=\s*(?:[^\s"'=<>`]+|'[^']*'|"[^"]*"))?)*"""
    r"""\s*/?>""",
    re.IGNORECASE,
)


def legacy_extract_and_replace_images(
    md_content: str,
) -> tuple[str, list[dict[str, str]]]:
    """Extract images and replace their alt-text with sequential ``Image_N`` labels."""
    images: list[dict[str, str]] = []
    image_counter = 1

    markdown_img_pattern = r'!\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)'
    reference_usage_pattern = r'!\[([^\]]*)\]\[([^\]]+)\]'
    reference_def_pattern = r'^\[([^\]]+)\]:\s+([^\s]+)(?:\s+"[^"]*")?\s*$'

    reference_map: dict[str, str] = {}
    for match in re.finditer(reference_def_pattern, md_content, re.MULTILINE):
        reference_map[match.group(1).lower()] = match.group(2).strip()

    reference_positions: set[int] = set()
    for match in re.finditer(reference_usage_pattern, md_content):
        reference_positions.add(match.start())

    def replace_reference_image(match: re.Match[str]) -> str:
        nonlocal image_counter
        original_alt = match.group(1)
        ref_id = match.group(2)
        url = reference_map.get(ref_id.lower(), f"[unknown reference: {ref_id}]")
        new_alt = f"Image_{image_counter}"
        images.append({
            "original_text": match.group(0),
            "url": url,
            "alt_text": original_alt,
            "new_alt_text": new_alt,
            "image_type": "reference",
        })
        image_counter += 1
        return f"![{new_alt}][{ref_id}]"

    def replace_markdown_image(match: re.Match[str]) -> str:
        nonlocal image_counter
        if match.start() in reference_positions:
            return match.group(0)
        original_alt = match.group(1)
        url = match.group(2)
        new_alt = f"Image_{image_counter}"
        images.append({
            "original_text": match.group(0),
            "url": url,
            "alt_text": original_alt,
            "new_alt_text": new_alt,
            "image_type": "markdown",
        })
        image_counter += 1
        return f"![{new_alt}]({url})"

    def replace_html_image(match: re.Match[str]) -> str:
        nonlocal image_counter
        fragment = BeautifulSoup(match.group(0), "html.parser")
        tags = fragment.find_all(True)
        if len(tags) != 1 or tags[0].name != "img" or fragment.get_text():
            return match.group(0)
        img_tag = tags[0]
        src = img_tag.get("src", "")
        original_alt = img_tag.get("alt", "")
        original_text = str(img_tag)
        new_alt = f"Image_{image_counter}"
        img_tag["alt"] = new_alt
        images.append({
            "original_text": original_text,
            "url": src,
            "alt_text": original_alt,
            "new_alt_text": new_alt,
            "image_type": "html",
        })
        image_counter += 1
        return str(img_tag)

    # Only the <img> tags are re-serialised: running the whole document through
    # an HTML parser escapes "&", "<" and ">" and closes anything that looks like
    # a tag, which corrupts code, autolinks and blockquotes.
    def process_html_images(content: str) -> str:
        return _LEGACY_HTML_IMG_TAG_RE.sub(replace_html_image, content)

    modified = re.sub(reference_usage_pattern, replace_reference_image, md_content)
    modified = re.sub(markdown_img_pattern, replace_markdown_image, modified)
    modified = process_html_images(modified)
    return modified, images


# --- helpers -------------------------------------------------------------------


def urls(md: str) -> list[str]:
    return [i["url"] for i in extract_and_replace_images(md)[1]]


def cpu(fn: Callable[[str], object], text: str, repeat: int = 3) -> float:
    """Best of ``repeat`` runs in this thread's CPU time (not disturbed by other processes or threads)."""
    best = float("inf")
    for _ in range(repeat):
        start = time.thread_time()
        fn(text)
        best = min(best, time.thread_time() - start)
    return best


class _Counted:
    """A compiled pattern that adds the text a call examines (up to the end of its match, or of the text when it finds nothing)
    and the number of calls to ``work``. The scan runs the same code with the same answers; only the examining is counted."""

    def __init__(self, pattern: re.Pattern[str], work: list[int]) -> None:
        self._pattern, self._work = pattern, work

    def search(self, text: str, pos: int = 0, *rest: int) -> re.Match[str] | None:
        found = self._pattern.search(text, pos, *rest)
        self._work[0] += (found.end() if found else len(text)) - pos
        self._work[1] += 1
        return found

    def match(self, text: str, pos: int = 0, *rest: int) -> re.Match[str] | None:
        found = self._pattern.match(text, pos, *rest)
        self._work[0] += max((found.end() if found else pos + 1) - pos, 1)
        self._work[1] += 1
        return found

    def __getattr__(self, name: str) -> object:
        return getattr(self._pattern, name)


class _CountingRe:
    """``re`` for the module under test: the patterns it compiles while scanning are counted too."""

    def __init__(self, work: list[int]) -> None:
        self._work = work

    def compile(self, *args: object, **kwargs: object) -> _Counted:
        return _Counted(re.compile(*args, **kwargs), self._work)

    def __getattr__(self, name: str) -> object:
        return getattr(re, name)


def counted_work(scan: Callable[[str], object], text: str, *modules: ModuleType) -> int:
    """Characters ``scan(text)`` makes the patterns of ``modules`` examine in total. Counted, not timed: linear is the same number on a
    loaded runner and on an idle one, and a scan that rescans from each opener (quadratic) or backtracks (exponential) examines many
    times more."""
    work = [0, 0]
    patterns = [(m, name, value) for m in modules for name, value in vars(m).items() if isinstance(value, re.Pattern)]
    saved_re = [(m, m.re) for m in modules if hasattr(m, "re")]
    try:
        for m, name, value in patterns:
            setattr(m, name, _Counted(value, work))
        for m, _ in saved_re:
            m.re = _CountingRe(work)
        scan(text)
    finally:
        for m, name, value in patterns:
            setattr(m, name, value)
        for m, original in saved_re:
            m.re = original
    return work[0]


def scan_work(text: str) -> int:
    """The work of the Markdown image scan on ``text``."""
    from app.modules.parsers.markdown import image_references as module

    return counted_work(module.extract_and_replace_images, text, module)


def html_scan_work(parser: object, text: str) -> int:
    """The work of an HTML extractor's parse on ``text`` (the standard library's HTML tokenizer does its pattern matching)."""
    import _markupbase
    import html.parser

    return counted_work(parser.extract_and_replace_images, text, html.parser, _markupbase)


# --- parity with the previous implementation ------------------------------------

_TOKENS = [
    "![", "]", "[", "(", ")", "a", "b", "http://x/y.png", "<img ", 'src="u"', "src='v'", " ",
    "\n", "\t", '"', "'", "x", "<", ">", "/", 'alt="q"', "=", "]:", "[r]:", "![a][r]",
    "![a](u)", '![a](u "T")', "<img src=z>", "`", "\\", "  ", "[r]: http://e/f.png\n",
    '[r]: http://e/f.png "t"\n',
]
# Inputs using a form the previous implementation did not recognise are compared
# only for being reported, not for equality.
_NEW_FORMS = re.compile(
    r"(?m)^ {1,3}\[|'|\(\s|\(<|<img[/\n]|\)\s+\)|\(\)|\]:\S|<img\s[^>]*[^\s>\"'=]="
)


def test_output_matches_the_previous_implementation_on_generated_inputs() -> None:
    rng = random.Random(20261027)
    compared = 0
    for _ in range(40000):
        text = "".join(rng.choice(_TOKENS) for _ in range(rng.randint(1, 14)))
        if _NEW_FORMS.search(text):
            continue
        compared += 1
        assert extract_and_replace_images(text) == legacy_extract_and_replace_images(text), text
    assert compared > 5000


def _repo_markdown_files() -> list[Path]:
    from pathlib import Path

    root = Path(__file__).resolve().parents[5]
    try:
        names = subprocess.check_output(
            ["git", "ls-files", "*.md", "*.mdx"], cwd=root, text=True, timeout=30
        ).split()
    except (OSError, subprocess.SubprocessError):
        return []
    return [root / n for n in names if (root / n).is_file()]


def test_output_matches_the_previous_implementation_on_the_repository_markdown() -> None:
    files = _repo_markdown_files()
    if not files:
        pytest.skip("not a git checkout")
    for path in files:
        text = path.read_text(errors="replace")
        assert extract_and_replace_images(text) == legacy_extract_and_replace_images(text), path


def test_numbering_and_rewriting_of_the_recognised_forms() -> None:
    md = (
        "![one][r] ![two](http://x/a.png \"T\") <img src=\"http://x/c.png\" alt=\"three\">\n"
        "\n[r]: http://x/b.png \"Title\"\n"
    )
    out, images = extract_and_replace_images(md)
    assert [(i["image_type"], i["url"], i["new_alt_text"]) for i in images] == [
        ("reference", "http://x/b.png", "Image_1"),
        ("markdown", "http://x/a.png", "Image_2"),
        ("html", "http://x/c.png", "Image_3"),
    ]
    assert out.startswith("![Image_1][r] ![Image_2](http://x/a.png) <img alt=\"Image_3\"")


def test_an_inline_image_is_not_taken_for_a_reference_image_that_started_at_its_offset() -> None:
    # The reference image's alt is rewritten first, which moves later text.
    # Matching the previous implementation here would drop "u".
    assert sorted(urls("/![][/>![a](u)![a][r]")) == sorted(
        ["u", "[unknown reference: r]", "[unknown reference: />![a]"]
    )


# --- forms that used to be missed ------------------------------------------------


@pytest.mark.parametrize(
    ("md", "url", "rewritten"),
    [
        ("![a]( http://x/p.png )", "http://x/p.png", "![Image_1](http://x/p.png)"),
        ("![a](\n  http://x/p.png\n)", "http://x/p.png", "![Image_1](http://x/p.png)"),
        ("![a](http://x/p.png 'T')", "http://x/p.png", "![Image_1](http://x/p.png)"),
        ("![a](http://x/p.png (T))", "http://x/p.png", "![Image_1](http://x/p.png)"),
        ("![a](http://x/p.png \"T\" )", "http://x/p.png", "![Image_1](http://x/p.png)"),
        ("![a](<http://x/p.png>)", "http://x/p.png", "![Image_1](<http://x/p.png>)"),
        ("![a](<http://x/my p.png> 'T')", "http://x/my p.png", "![Image_1](<http://x/my p.png>)"),
        ("![a](data:image/svg+xml;base64,PHN2Zy8+)", "data:image/svg+xml;base64,PHN2Zy8+",
         "![Image_1](data:image/svg+xml;base64,PHN2Zy8+)"),
        ("![a][r]\n\n  [r]: http://x/p.png", "http://x/p.png", "![Image_1][r]\n\n  [r]: http://x/p.png"),
        ("![a][r]\n\n[r]: <http://x/p.png>", "http://x/p.png", "![Image_1][r]\n\n[r]: <http://x/p.png>"),
        ("![a][r]\n\n[r]: http://x/p.png 'T'", "http://x/p.png", "![Image_1][r]\n\n[r]: http://x/p.png 'T'"),
        ('<img/src="http://x/p.png">', "http://x/p.png", '<img alt="Image_1" src="http://x/p.png"/>'),
        ('<img\nsrc="http://x/p.png">', "http://x/p.png", '<img alt="Image_1" src="http://x/p.png"/>'),
        ('<IMG SRC="http://x/p.png"/>', "http://x/p.png", '<img alt="Image_1" src="http://x/p.png"/>'),
    ],
)
def test_forms_the_markdown_parser_resolves_are_reported(md, url, rewritten) -> None:
    out, images = extract_and_replace_images(md)
    assert [i["url"] for i in images] == [url]
    assert out == rewritten


def test_a_title_or_padding_does_not_swallow_a_neighbouring_image() -> None:
    assert urls("![a]( x.png )![b](y.png)") == ["x.png", "y.png"]
    # Padded destinations must balance their parentheses (CommonMark); this one
    # does not, so the image inside it stays visible.
    assert urls("![a](\n![b](y.png)") == ["y.png"]


def test_what_is_not_an_image_is_left_alone() -> None:
    for text in ("![a]()", "![a](", "![a](u", "![a](u 'T'", "![](", "![a][", "<img", "<img src=",
                 "<img src='x", "<imgx src=u>", "<img src=u", "[r]: x\n"):
        out, images = extract_and_replace_images(text)
        assert (out, images) == (text, []), text


@pytest.mark.parametrize("parser_class", [SelectolaxHtmlParser, DoclingHtmlParser])
def test_html_extractors_report_data_uris_the_converter_does_not_keep(parser_class: type) -> None:
    parser = parser_class.__new__(parser_class)
    for src in (
        "data:image/svg+xml;base64,PHN2Zy8+",
        "DATA:image/SVG+xml;base64,PHN2Zy8+",
        "data:text/html,<p>x</p>",
    ):
        _, images = parser.extract_and_replace_images(f'<img src="{src}" alt="x">')
        assert [i["url"] for i in images] == [src]


@pytest.mark.parametrize("parser_class", [SelectolaxHtmlParser, DoclingHtmlParser])
def test_html_extractors_leave_raster_data_images_to_the_converter(parser_class: type) -> None:
    parser = parser_class.__new__(parser_class)
    for src in ("data:image/png;base64,AAAA", "DATA:image/jpeg;base64,AAAA"):
        _, images = parser.extract_and_replace_images(f'<img src="{src}" alt="x">')
        assert images == []


@pytest.mark.asyncio
async def test_the_image_parser_refuses_a_data_uri_that_is_not_an_image(monkeypatch) -> None:
    from app.modules.parsers.image_parser import image_parser as module

    class Boom:
        async def get(self, *a: object, **k: object) -> None:  # pragma: no cover - must not be reached
            raise AssertionError("fetched a non-image data URI")

    monkeypatch.setattr(module, "PublicUrlFetcher", Boom)
    result = await module.ImageParser.urls_to_base64(
        ["data:text/html,<script>alert(1)</script>", "javascript:alert(1)"], allow_remote_fetch=True
    )
    assert result == [None, None]


# --- time is linear in the size of the input -------------------------------------

_BENIGN = "Some prose here, and an image ![alt](http://example.com/i.png \"t\") plus [a link](http://x). "

_ADVERSARIAL = {
    "repeated openers": "![",
    "unclosed destination": "![a](",
    "unclosed reference": "![a][",
    "unclosed title": '![a](u "',
    "unclosed single-quoted title": "![a](u '",
    "padded destination": "![a]( ",
    "reference lines": "[a\n",
    "reference definitions": "[a]: u '\n",
    "unclosed img tag": "<img a ",
    "unclosed img quote": "<img a='",
    "unclosed img double quote": '<img a="',
    "img openers": "<img ",
    "nested quotes": "<img a='<img b=\"",
    "cdata-like": "<![CDATA[",
}


@pytest.mark.parametrize("name", sorted(_ADVERSARIAL))
def test_scan_work_is_linear_for_crafted_input(name) -> None:
    unit = _ADVERSARIAL[name]
    big = unit * (65536 // len(unit))
    quarter = unit * (16384 // len(unit))
    work_big, work_quarter = scan_work(big), scan_work(quarter)
    # Examined per character is a small constant (at most about 4 today) and four times the input costs four times the work:
    # linear is 4x and quadratic 16x.
    assert work_big <= 8 * len(big), (work_big, len(big))
    assert work_big <= 5 * work_quarter, (work_big, work_quarter)


def test_a_shared_closing_bracket_is_not_rescanned_per_opener() -> None:
    def build(n: int) -> str:
        return "![" * n + "a](" + " " * n + "z"

    work_small, work_big = scan_work(build(8000)), scan_work(build(32000))
    assert work_big <= 5 * work_small, (work_big, work_small)


# A run of openers that all share one closing tail: every opener's lookups land
# on the same bracket, so a scanner whose cached lookup jumps back rescans to it
# once per opener.
_SHARED_TAILS = ["][]", "][x]", "]", "](", "]( ", "][", '](u "', "]: u"]


@pytest.mark.parametrize("tail", _SHARED_TAILS)
def test_openers_sharing_one_tail_are_scanned_in_linear_time(tail: str) -> None:
    work_small = scan_work("![" * 8000 + tail)
    work_big = scan_work("![" * 32000 + tail)
    # Four times the input: linear is 4x, quadratic 16x.
    assert work_big <= 5 * work_small, (tail, work_small, work_big)
    assert work_big <= 8 * 64000, (tail, work_big)


_HTML_UNITS = ["<img ", "<img a='", "<a b='", "<!--", "<", "<![CDATA[", "<div>"]


@pytest.mark.parametrize("parser_class", [SelectolaxHtmlParser, DoclingHtmlParser])
@pytest.mark.parametrize("unit", _HTML_UNITS)
def test_html_extractors_do_linear_work_on_crafted_input(parser_class, unit) -> None:
    parser = parser_class.__new__(parser_class)
    big = unit * (65536 // len(unit))
    quarter = unit * (16384 // len(unit))
    work_big, work_quarter = html_scan_work(parser, big), html_scan_work(parser, quarter)
    # About 2 characters examined per character today; four times the input costs four times the work (quadratic would be 16x).
    assert work_big <= 8 * len(big), (work_big, len(big))
    assert work_big <= 5 * work_quarter, (work_big, work_quarter)


_DEADLINE_SCRIPT = textwrap.dedent(
    """
    import faulthandler, sys, traceback

    def main():
        sys.path.insert(0, {tests_root!r})
        import conftest  # the mocks pytest installs before Docling's native code is touched (tests/conftest.py)

        sys.path.insert(0, {tests!r})
        from test_image_references import _ADVERSARIAL, _HTML_UNITS, _SHARED_TAILS
        from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
        from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
        from app.modules.parsers.markdown.image_references import extract_and_replace_images

        scans = [(name, extract_and_replace_images, unit * (65536 // len(unit))) for name, unit in _ADVERSARIAL.items()]
        # Openers that all share one closing tail: their only other check is the count of work.
        scans += [("tail " + tail, extract_and_replace_images, "![" * 32000 + tail) for tail in _SHARED_TAILS]
        for parser_class in (SelectolaxHtmlParser, DoclingHtmlParser):
            parser = parser_class.__new__(parser_class)
            scans += [
                (parser_class.__name__ + " " + unit, parser.extract_and_replace_images, unit * (65536 // len(unit)))
                for unit in _HTML_UNITS
            ]
        for name, scan, text in scans:
            # A regular expression that backtracks inside one call cannot be interrupted or counted; the watchdog thread ends the
            # process, naming where it was, long before the test runner's own timeout.
            faulthandler.dump_traceback_later({deadline}, exit=True)
            scan(text)
            faulthandler.cancel_dump_traceback_later()

    try:
        main()
    except BaseException:
        traceback.print_exc()
        sys.exit(2)  # not the watchdog (which ends the process with 1): the child could not run the scans
    """
)


# Its child process takes about 30 s on an idle host and has its own 120 s
# timeout, so the suite-wide 30 s limit failed it whenever the host was busy.
@pytest.mark.timeout(180)
def test_no_crafted_input_makes_a_single_scan_run_away() -> None:
    """The work counts above cannot see a pattern that backtracks inside one call. Each family's largest input, and each shared-tail input, is scanned once in a
    child process under a deadline of seconds where a scan takes milliseconds: only a real blow-up misses it."""
    script = _DEADLINE_SCRIPT.format(
        tests=str(Path(__file__).parent), tests_root=str(Path(__file__).parents[3]), deadline=3
    )
    run = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=Path(__file__).parents[4], timeout=120, check=False,
    )
    if run.returncode == 0:
        return
    if run.returncode == 1 and "Timeout (" in run.stderr:
        pytest.fail("a scan ran past the deadline:\n" + run.stderr[-1500:])
    pytest.fail(f"the child process failed (exit {run.returncode}) before or while it ran the scans, not at the deadline:\n" + run.stderr[-1500:])


def test_many_real_images_keep_the_tag_scan_complete() -> None:
    # The scan budget only bounds tags that never close; a document full of
    # well-formed images is reported whole.
    md = '<img src="http://x/i.png" alt="a"> ' * 3000
    _, images = extract_and_replace_images(md)
    assert len(images) == 3000


def test_unclosed_tags_do_not_hide_later_images() -> None:
    md = "<img a='x <img b> ![a](http://x/p.png) <img a b=2 " + '<img src="http://x/q.png">'
    assert "http://x/p.png" in urls(md)
    assert "http://x/q.png" in urls(md)


def test_output_is_valid_for_an_html_parser_after_rewriting() -> None:
    out, _ = extract_and_replace_images('<img/src="http://x/p.png" alt="a>b">')
    assert BeautifulSoup(out, "html.parser").find("img")["alt"] == "Image_1"


@pytest.mark.parametrize("tag", ["<img /\r\n>", "<img /:>", "<img \t/ >", "<img/:>", "<img / src=x>"])
def test_a_slash_that_markdown_it_does_not_read_as_a_tag_reports_nothing(tag: str) -> None:
    out, images = extract_and_replace_images(tag)
    assert [i for i in images if not i["url"]] == []
    assert not any(i["image_type"] == "html" and not i["url"] for i in images)


def test_a_bare_self_closing_img_is_still_reported_as_before() -> None:
    for tag in ("<img/>", "<img />"):
        assert extract_and_replace_images(tag) == legacy_extract_and_replace_images(tag)


def test_one_line_ending_is_allowed_around_the_destination() -> None:
    assert urls("![a](\nhttp://h/x.png\n)") == ["http://h/x.png"]
    assert urls('![a](http://h/x.png\n"t")') == ["http://h/x.png"]


def test_a_bare_img_tag_matches_the_previous_output() -> None:
    for text in ("<img>", "<IMG>", "<p><img></p>", "text <img> more", "<img>\n"):
        assert extract_and_replace_images(text) == legacy_extract_and_replace_images(text), text


def test_the_scanner_never_raises() -> None:
    rng = random.Random(20261028)
    ascii_chars = [chr(c) for c in range(128)]
    pieces = ["<img", "<IMG", "![", "](", "]", "[", ")", "(", "'", '"', "<", ">", "/", "=", "\n", " ", "src=", "a"]
    for i in range(100_000):
        if i % 2:
            # `<img` followed by every ASCII character, then some noise
            text = "x" + "<img" + ascii_chars[i // 2 % 128] + "".join(
                rng.choice(pieces) for _ in range(rng.randint(0, 6))
            )
        else:
            text = "".join(rng.choice(pieces + ascii_chars) for _ in range(rng.randint(0, 24)))
        extract_and_replace_images(text)


# --- a limit on the number of images ----------------------------------------------


_ONE_OF_EACH_KIND = '![a](u1) ![b][r] <img src="u3">\n\n[r]: u2\n'


def test_max_images_allows_exactly_that_many() -> None:
    assert extract_and_replace_images(_ONE_OF_EACH_KIND, max_images=3) == extract_and_replace_images(_ONE_OF_EACH_KIND)


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_max_images_stops_at_the_first_image_over_the_limit(limit: int) -> None:
    with pytest.raises(TooManyImages):
        extract_and_replace_images(_ONE_OF_EACH_KIND, max_images=limit)


def test_max_images_does_not_collect_the_images_it_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan raises on the 11th image; one that finished first would have walked all 100,000."""
    import app.modules.parsers.markdown.image_references as module

    seen: list[int] = []
    original = module._add

    def counting(images: list, limit: int | None, image: dict) -> None:
        seen.append(len(images))
        original(images, limit, image)

    monkeypatch.setattr(module, "_add", counting)
    with pytest.raises(TooManyImages):
        extract_and_replace_images("![a](u)" * 100_000, max_images=10)
    assert len(seen) == 11
