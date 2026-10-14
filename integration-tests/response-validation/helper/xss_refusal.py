"""The words the app-wide markup filter (xssSanitizationMiddleware) answers with."""

from __future__ import annotations

import re

XSS_CHECK_MAX_LENGTH = 100_000
_ENCODED_SCRIPT_TAGS = ("&lt;script", "%3cscript", "&#60;script", "&#x3c;script")


def xss_message(value: str, field: str) -> str:
    """The markup filter's refusal of ``value`` in ``field`` (`the query parameter "x"`, `the field "x"`).

    The second sentence depends on what was refused: a tag, an encoded script tag, or other script
    content. A value over the length limit is refused as too long instead.
    """
    if len(value) > XSS_CHECK_MAX_LENGTH:
        return (
            f"The value of {field} is longer than {XSS_CHECK_MAX_LENGTH:,} characters, "
            "which is too long to check for HTML tags and scripts. Please shorten it and try again."
        )
    refused = f"HTML tags, scripts, and XSS content are not allowed in {field}. "
    if re.search(r"<[a-zA-Z/!]", value):
        return refused + (
            "Text in angle brackets, such as <id>, counts as an HTML tag. Please remove any HTML tags and try again."
        )
    if any(tag in value.lower() for tag in _ENCODED_SCRIPT_TAGS):
        return refused + (
            "A script tag written in an encoded form, such as &lt;script or %3Cscript, counts. "
            "Please remove it and try again."
        )
    return refused + (
        "Script content such as an onclick= handler, a javascript: link or a data:text/html URL counts. "
        "Please remove it and try again."
    )
