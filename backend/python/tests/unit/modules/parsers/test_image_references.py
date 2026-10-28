"""Image-reference scanning: output parity, the forms it now finds, and linear time.

``legacy_extract_and_replace_images`` below is the regular-expression
implementation that this module replaced. It is the oracle for "the output of
every form that was recognised before is unchanged".
"""
from __future__ import annotations

import random
import re
import subprocess
import time
from typing import TYPE_CHECKING

import pytest
from bs4 import BeautifulSoup

from app.modules.parsers.html_parser.docling_html_parser import DoclingHtmlParser
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser
from app.modules.parsers.markdown.image_references import extract_and_replace_images

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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
    """Best of ``repeat`` runs in process CPU time (not disturbed by other processes)."""
    best = float("inf")
    for _ in range(repeat):
        start = time.process_time()
        fn(text)
        best = min(best, time.process_time() - start)
    return best


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
        ["data:text/html,<script>alert(1)</script>", "javascript:alert(1)"]
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
def test_scan_time_is_linear_for_crafted_input(name) -> None:
    unit = _ADVERSARIAL[name]
    big = unit * (65536 // len(unit))
    quarter = unit * (16384 // len(unit))
    benign = _BENIGN * (65536 // len(_BENIGN))
    t_benign = cpu(extract_and_replace_images, benign)
    t_big = cpu(extract_and_replace_images, big)
    t_quarter = cpu(extract_and_replace_images, quarter)
    # Judged against the same host's time for ordinary text of the same size,
    # and against the same input at a quarter of the size: linear is 4x and
    # quadratic 16x. (A 2x step measured 3.3x once on a busy runner for a family
    # that is 2.0x per doubling from 16 KB to 256 KB.)
    assert t_big < max(t_benign, 0.05) * 12, (t_big, t_benign)
    assert t_big < max(t_quarter, 0.02) * 8, (t_big, t_quarter)


def test_a_shared_closing_bracket_is_not_rescanned_per_opener() -> None:
    def build(n: int) -> str:
        return "![" * n + "a](" + " " * n + "z"

    t_small = cpu(extract_and_replace_images, build(8000))
    t_big = cpu(extract_and_replace_images, build(32000))
    assert t_big < max(t_small, 0.05) * 6.5, (t_big, t_small)


# A run of openers that all share one closing tail: every opener's lookups land
# on the same bracket, so a scanner whose cached lookup jumps back rescans to it
# once per opener.
_SHARED_TAILS = ["][]", "][x]", "]", "](", "]( ", "][", '](u "', "]: u"]


@pytest.mark.parametrize("tail", _SHARED_TAILS)
def test_openers_sharing_one_tail_are_scanned_in_linear_time(tail: str) -> None:
    t_small = cpu(extract_and_replace_images, "![" * 8000 + tail)
    t_big = cpu(extract_and_replace_images, "![" * 32000 + tail)
    # Four times the input: linear is 4x, quadratic 16x.
    assert t_big < max(t_small, 0.02) * 8, (tail, t_small, t_big)
    assert t_big < 1.0, (tail, t_small, t_big)


@pytest.mark.parametrize("parser_class", [SelectolaxHtmlParser, DoclingHtmlParser])
@pytest.mark.parametrize("unit", ["<img ", "<img a='", "<a b='", "<!--", "<", "<![CDATA[", "<div>"])
def test_html_extractors_take_linear_time_on_crafted_input(parser_class, unit) -> None:
    parser = parser_class.__new__(parser_class)
    big = unit * (65536 // len(unit))
    half = unit * (32768 // len(unit))
    benign = '<p>text <img src="http://x/i.png" alt="a"></p>' * (65536 // 48)
    t_benign = cpu(parser.extract_and_replace_images, benign, repeat=2)
    t_big = cpu(parser.extract_and_replace_images, big, repeat=2)
    t_half = cpu(parser.extract_and_replace_images, half, repeat=2)
    assert t_big < max(t_benign, 0.05) * 12, (t_big, t_benign)
    assert t_big < max(t_half, 0.02) * 3.5, (t_big, t_half)


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
