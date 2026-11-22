"""One rule for "this file becomes a code record", used by the sync (what it indexes) and by the commit (whether the code changed).

If the two disagreed, a commit could report a change to the code (so the code graph would be owed a build) for a file the sync then leaves out:
no record would change, no build would be requested, and the repository would wait for it for ever.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from app.modules.parsers.code_parser.file_role import (
    FileRole,
    has_generator_header,
    is_generated_content,
    is_ignored_path,
    is_sensitive_path,
    should_index_code_file,
)
from app.modules.parsers.code_parser.lang_config import detect_language

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

HEAD_BYTES = 64 * 1024


def read_head(path: Path) -> bytes | None:
    """The first bytes of ``path``, or None when it cannot be read."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        return os.pread(fd, HEAD_BYTES, 0)
    except OSError:
        return None
    finally:
        os.close(fd)


def _generated(head: Callable[[], bytes | None], *, header_only: bool) -> bool:
    data = head()
    if data is None:
        return not header_only  # an unreadable look-alike stays out; any other file fails visibly later
    return (has_generator_header if header_only else is_generated_content)(data)


def decide(rel: str, head: Callable[[], bytes | None]) -> tuple[bool, FileRole, str | None]:
    """(becomes a code record, its role, why not). ``head`` reads the start of the file, and is called only when the name alone does not decide.

    The path rules, except that a name or folder that only *looks* generated (``gen/``, ``generated/``, ``*_pb2.py``) is decided by what the file
    says: a generator's header or a minified body excludes it, hand-written source is kept."""
    name = rel.rsplit("/", 1)[-1]
    ok, role = should_index_code_file(rel, name)
    if ok:
        # A generator's header decides for any name: left to the indexer the file would end "not supported", uncounted by `status`.
        if _generated(head, header_only=True):
            return False, role, "generated"
        return True, role, None
    if is_sensitive_path(name):
        return False, role, "sensitive"
    if is_ignored_path(rel) or role is not FileRole.GENERATED:
        return False, role, "ignored_path"
    if _generated(head, header_only=False):
        return False, role, "generated"
    return True, FileRole.SOURCE, None


def is_code_record(rel: str, head: Callable[[], bytes | None]) -> bool:
    """Whether the file ``rel`` becomes a code record: a language the code graph reads, and not left out by ``decide``."""
    return bool(detect_language(rel.rsplit("/", 1)[-1])) and decide(rel, head)[0]
