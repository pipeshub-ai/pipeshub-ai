"""Exceptions built the way production code raises them, so a test can pin their ``__cause__``."""
from __future__ import annotations

from typing import TypeVar

E = TypeVar("E", bound=BaseException)


def raised_from(error: E, cause: BaseException) -> E:
    """*error* after ``raise error from cause``: its cause, context and traceback set."""
    try:
        raise error from cause
    except type(error) as raised:
        return raised
