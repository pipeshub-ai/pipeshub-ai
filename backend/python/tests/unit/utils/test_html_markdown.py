"""app.utils.html_markdown: callers get a ``str`` whatever the library returns."""

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
