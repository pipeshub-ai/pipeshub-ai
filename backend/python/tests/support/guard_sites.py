"""Where the structural guard tests look, and what extra source an installed extension adds.

Five guards walk the source tree and list the files that may write, or delete, something: ``test_status_writer_sites``,
``test_record_diagnostics`` (the revision write sites), ``test_delete_sites_fenced`` and
``test_record_diagnostics_schema::test_only_the_listed_files_write_the_fields``; a fifth, ``test_permission_writers_bump``, lists the
functions that write permission edges and must bump the permission version. Extension code that registers more sites (a
lifecycle, a ownership implementation, a long-document policy shipped outside ``app``) adds them here instead of editing each guard.
Every default is empty, so the built-in guards see exactly the tree they always saw.

A site is keyed ``(file, ...)`` with the file relative to ``app`` for the built-in tree, and relative to the parent of its root for
an extra root (root ``backend/python/extras`` gives ``extras/x.py``). A listed site that does not exist still fails the guard, so
an entry here cannot go stale.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

PYTHON_ROOT = Path(__file__).resolve().parents[2]
APP = PYTHON_ROOT / "app"

#: Further directories of Python source the guards scan, besides ``app``.
EXTRA_SOURCE_ROOTS: tuple[Path, ...] = ()

#: test_status_writer_sites: (file, function, called method) -> class (see ``status_writer_sites.CLASSES``).
EXTRA_STATUS_SITES: dict[tuple[str, str, str], str] = {}
#: (file, helper function) -> class.
EXTRA_HELPER_WRITERS: dict[tuple[str, str], str] = {}
#: (file, function) -> (owner, note) of a transactional record write.
EXTRA_TX_OWNERS: dict[tuple[str, str], tuple[str, str]] = {}

#: Files that may write the record processing diagnostics (``test_record_diagnostics_schema``).
EXTRA_DIAGNOSTIC_WRITER_FILES: frozenset[str] = frozenset()

#: (file, function) -> number of sites that write ``contentRev`` through the revision rule (``test_record_diagnostics``).
EXTRA_REVISION_WRITERS: dict[tuple[str, str], int] = {}
#: (file, function) -> (number of sites, why they are not writers).
EXTRA_REVISION_NON_WRITERS: dict[tuple[str, str], tuple[int, str]] = {}

#: ``test_permission_writers_bump``: (file, function) of a writer of permission edges that must call a permission-version bump.
EXTRA_PERMISSION_WRITERS: frozenset[tuple[str, str]] = frozenset()

#: ``test_delete_sites_fenced``: (file, function) of a delete of vector points, by what makes it safe.
EXTRA_DELETE_FENCED: frozenset[tuple[str, str]] = frozenset()
EXTRA_DELETE_SWEEP: frozenset[tuple[str, str]] = frozenset()
EXTRA_DELETE_GRAPH_CHECKED: frozenset[tuple[str, str]] = frozenset()
EXTRA_DELETE_ENTITY_FILES: frozenset[str] = frozenset()
EXTRA_DELETE_COLLECTION_SITES: frozenset[tuple[str, str]] = frozenset()


def source_files() -> Iterator[tuple[str, Path]]:
    """Every scanned ``.py`` file as ``(key, path)``."""
    for path in sorted(APP.rglob("*.py")):
        yield path.relative_to(APP).as_posix(), path
    for root in EXTRA_SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            yield path.relative_to(root.parent).as_posix(), path


def path_of(key: str) -> Path:
    """The file a key names."""
    for root in EXTRA_SOURCE_ROOTS:
        if key.startswith(root.name + "/"):
            return root.parent / key
    return APP / key
