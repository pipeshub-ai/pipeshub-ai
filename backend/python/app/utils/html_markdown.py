"""HTML to Markdown, in one place.

``html-to-markdown`` 3.x returns a ``ConversionResult`` object from
``convert()`` (the text is on ``.content``) where 2.x returned a ``str``.
Callers import ``html_to_markdown`` from here so they always get a ``str``.
"""

import html_to_markdown as _lib


def html_to_markdown(html: str) -> str:
    """Convert an HTML string to Markdown text."""
    return _lib.convert(html).content
