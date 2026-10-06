"""URL-level helpers for the fragment directive (`:~:`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.utils.text_fragments.codec import serialize

if TYPE_CHECKING:
    from app.utils.text_fragments.models import TextDirective

FRAGMENT_DIRECTIVE_DELIMITER = ":~:"
TEXT_FRAGMENT_DIRECTIVE_PREFIX = "#:~:text="


def has_fragment_directive(url: str) -> bool:
    return _directive_index(url) >= 0


def _directive_index(url: str) -> int:
    """Index of the first `:~:` inside the fragment; `:~:` is legal in a path or query."""
    fragment_start = url.find("#")
    if fragment_start < 0:
        return -1
    return url.find(FRAGMENT_DIRECTIVE_DELIMITER, fragment_start + 1)


def split_fragment_directive(url: str) -> tuple[str, str | None]:
    """Split `url` into `(page_url, directive)`.

    The page URL keeps any anchor that preceded the directive
    (`page#section:~:text=x` -> `page#section`) and loses a dangling `#`. The
    split is on the first `:~:` in the fragment, as in the spec, because a
    directive is appended after an existing anchor and so the `#` is not always
    adjacent to it.
    """
    index = _directive_index(url)
    if index < 0:
        return url, None
    page = url[:index]
    directive = url[index + len(FRAGMENT_DIRECTIVE_DELIMITER):] or None
    if page.endswith("#"):
        page = page[:-1]
    return page, directive


def strip_fragment_directive(url: str) -> str:
    return split_fragment_directive(url)[0]


def append_directive(url: str, directive: TextDirective) -> str:
    """Add a text directive to `url`, keeping an existing anchor intact.

    A conforming browser hands the page the fragment up to `:~:` and keeps the
    directive to itself, so an anchor set by a connector (a Gmail message id, a
    heading) still resolves. A URL that already has a directive is returned
    unchanged: everything after the first `:~:` is the directive.
    """
    if has_fragment_directive(url):
        return url
    separator = FRAGMENT_DIRECTIVE_DELIMITER if "#" in url else f"#{FRAGMENT_DIRECTIVE_DELIMITER}"
    return f"{url}{separator}{serialize(directive)}"
