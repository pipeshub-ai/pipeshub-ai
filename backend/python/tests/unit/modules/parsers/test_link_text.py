import pytest

from app.modules.parsers.link_text import anchor_text_only


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
def test_links_reduce_to_anchor_text(text, expected):
    assert anchor_text_only(text) == expected


def test_reference_style_links_and_definitions():
    text = (
        "Read [the guide][guide] and [the FAQ][] first.\n\n"
        "[guide]: https://docs.example.com/guide \"Guide\"\n"
        "[the faq]: https://docs.example.com/faq\n"
    )
    assert anchor_text_only(text) == "Read the guide and the FAQ first."


def test_shortcut_reference_is_resolved_only_when_defined():
    text = "Use [Setup] now.\n[setup]: https://example.com/setup"
    assert anchor_text_only(text) == "Use Setup now."
    assert anchor_text_only("Status [WIP] today") == "Status [WIP] today"


def test_urls_in_code_are_content_and_kept():
    text = "Call `curl https://api.example.com/v1` then see [docs](https://example.com/d)."
    assert anchor_text_only(text) == "Call `curl https://api.example.com/v1` then see docs."


def test_fenced_code_is_kept_verbatim():
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
def test_text_without_links_is_unchanged(text):
    assert anchor_text_only(text) == text


def test_unbalanced_brackets_do_not_swallow_text():
    assert anchor_text_only("open [bracket and (https://example.com/x") == (
        "open [bracket and (example.com"
    )
