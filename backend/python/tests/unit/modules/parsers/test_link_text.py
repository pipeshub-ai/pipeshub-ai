import pytest

from app.modules.parsers.link_text import anchor_text_only, markdown_link_destination


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("See [Acme Corp](https://example.com/docs/Acme_Corp) today.", "See Acme Corp today."),
        ("The [a [nested] label](https://example.org/p) here", "The a [nested] label here"),
        ("![Company logo](https://cdn.example.com/logo.png) Welcome", "Company logo Welcome"),
        ("![](https://cdn.example.com/spacer.gif) Welcome", "Welcome"),
        ("[![Flag](https://example.com/f.svg)](https://example.com/Region) Jane", "Flag Jane"),
        ('[titled](https://example.io "A title") ok', "titled ok"),
        ("[angle](<https://example.io/a b>) ok", "angle ok"),
        (
            "[Function (maths)](https://example.org/Function_(mathematics)) is key",
            "Function (maths) is key",
        ),
        ("(see https://example.com/a_(b)).", "(see example.com)."),
        ("Visit <https://example.com/path?q=1> now", "Visit example.com now"),
        ("Mail <mailto:ops@example.com> now", "Mail ops@example.com now"),
        ("Go to https://www.example.com/a/b, then stop.", "Go to example.com, then stop."),
        ("a <a href=\"https://example.com\">Anchor</a> b", "a Anchor b"),
        ("[](https://example.io/empty) left", "left"),
    ],
)
def test_links_reduce_to_anchor_text(text, expected) -> None:
    assert anchor_text_only(text) == expected


def test_reference_style_links_and_definitions() -> None:
    text = (
        "Read [the guide][guide] and [the FAQ][] first.\n\n"
        "[guide]: https://docs.example.com/guide \"Guide\"\n"
        "[the faq]: https://docs.example.com/faq\n"
    )
    assert anchor_text_only(text) == "Read the guide and the FAQ first."


def test_shortcut_reference_is_resolved_only_when_defined() -> None:
    text = "Use [Setup] now.\n[setup]: https://example.com/setup"
    assert anchor_text_only(text) == "Use Setup now."
    assert anchor_text_only("Status [WIP] today") == "Status [WIP] today"


def test_urls_in_code_are_content_and_kept() -> None:
    text = "Call `curl https://api.example.com/v1` then see [docs](https://example.com/d)."
    assert anchor_text_only(text) == "Call `curl https://api.example.com/v1` then see docs."


def test_fenced_code_is_kept_verbatim() -> None:
    text = "```\nGET https://api.example.com/v1 [x](y)\n```\nafter [z](https://z.example)"
    assert anchor_text_only(text) == "```\nGET https://api.example.com/v1 [x](y)\n```\nafter z"


@pytest.mark.parametrize(
    "text",
    [
        "Plain text with no links.",
        "grid[0][1] stays as it is",
        "Escaped \\[not a link\\](x)",
        "a < b and c > d",
        "",
    ],
)
def test_text_without_links_is_unchanged(text) -> None:
    assert anchor_text_only(text) == text


def test_unbalanced_brackets_do_not_swallow_text() -> None:
    assert anchor_text_only("open [bracket and (https://example.com/x") == (
        "open [bracket and (example.com"
    )


def test_longer_closing_fence_ends_the_code_block() -> None:
    text = "```\nGET https://api.example.com/v1\n````\nafter [z](https://z.example)"
    assert anchor_text_only(text) == "```\nGET https://api.example.com/v1\n````\nafter z"


def test_multiline_reference_definitions_are_removed() -> None:
    text = (
        "Read [the guide][guide] and [the FAQ][faq].\n\n"
        "[guide]:\nhttps://docs.example.com/guide\n"
        "[faq]: https://docs.example.com/faq\n\"Frequently asked\"\n"
    )
    assert anchor_text_only(text) == "Read the guide and the FAQ."


def test_hostless_autolink_keeps_its_uri() -> None:
    assert anchor_text_only("Call <tel:+15551234567>") == "Call tel:+15551234567"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[docs](https://example.com/docs (the guide)) ok", "docs ok"),
        ('[Acme Corp](https://example.com/docs/Acme%20Corp "Acme") ok', "Acme Corp ok"),
        ("[Report](https://example.com/Report_(Q3_(final)).pdf) ok", "Report ok"),
        ("[Report](https://example.com/Report%20%28final%29.pdf) ok", "Report ok"),
        ("See [this](a quick note) here", "See [this](a quick note) here"),
    ],
)
def test_commonmark_destinations_and_titles(text, expected) -> None:
    assert anchor_text_only(text) == expected


def test_raw_space_in_a_markdown_destination_is_not_a_link() -> None:
    # CommonMark: a bare destination cannot hold a space. HTML hrefs are
    # encoded before they become markdown, so only hand-written markdown has one.
    assert anchor_text_only("[x](https://example.com/a b) c").startswith("[x](")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://en.wikipedia.org/wiki/New York", "https://en.wikipedia.org/wiki/New%20York"),
        (
            "https://example.com/docs/Report (Q3 (final)).pdf",
            "https://example.com/docs/Report%20%28Q3%20%28final%29%29.pdf",
        ),
        ("https://example.com/a%20b <c>", "https://example.com/a%20b%20%3Cc%3E"),
        ("https://example.com/café?q=1#top", "https://example.com/café?q=1#top"),
        ("  https://example.com/tab\there  ", "https://example.com/tab%09here"),
        ("https://example.com/a\u00a0b\u2003c", "https://example.com/a%C2%A0b%E2%80%83c"),
    ],
)
def test_markdown_link_destination_encodes_only_unsafe_characters(url, expected) -> None:
    assert markdown_link_destination(url) == expected


def test_unicode_space_inside_a_bare_destination_does_not_end_it() -> None:
    assert anchor_text_only("[History](https://example.org/New\u00a0York) next") == "History next"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[https://example.org/wiki/New York](https://example.org/wiki/New%20York) is big",
         "example.org is big"),
        ("Read [https://docs.example.com/a guide][g] now.\n\n[g]: https://docs.example.com/a%20guide",
         "Read docs.example.com now."),
        # Text that only starts with the URL keeps the words after it.
        ("Read [https://docs.example.com/a guide][g] now.\n\n[g]: https://docs.example.com/a",
         "Read docs.example.com guide now."),
        ("[https://example.com/guide for details](https://example.com/guide)",
         "example.com for details"),
        ("![https://cdn.example.com/photo of the office](https://cdn.example.com/x.png)",
         "cdn.example.com of the office"),
        # Prose: the word after a bare URL's space is not part of it.
        ("See https://en.wikipedia.org/wiki/New York today", "See en.wikipedia.org York today"),
    ],
)
def test_link_whose_text_is_a_url_reduces_to_its_host(text, expected) -> None:
    assert anchor_text_only(text) == expected
