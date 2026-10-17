"""How PipesHub names a record it stored, given the file that was uploaded.

Kept here rather than in one suite because more than one needs it: a test that
compares what it uploaded against what the knowledge base lists has to know
about this, and a test that does not will fail on files that were indexed
perfectly well.
"""

from __future__ import annotations


def stored_name(file_name: str) -> str:
    """The name PipesHub stores: the file name without its final extension.

    The extension is kept in its own field so the UI can show a type icon, so a
    record listed as "board-pack" for "board-pack.pdf" is correct, not a bug.
    Mirrors getFilenameWithoutExtension in libs/utils/file-extension.util.ts.
    """
    dot = file_name.rfind(".")
    return file_name if dot <= 0 or dot == len(file_name) - 1 else file_name[:dot]


def stored_extension(file_name: str) -> str | None:
    """The extension PipesHub stores: lower-cased, no dot, None when there is none."""
    dot = file_name.rfind(".")
    return None if dot <= 0 or dot == len(file_name) - 1 else file_name[dot + 1:].lower()
