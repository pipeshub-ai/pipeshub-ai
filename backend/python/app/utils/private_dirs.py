"""Folders under a shared temp folder that only the running OS user may use.

``/tmp`` is shared by every user of a machine, and a fixed or guessable name in it belongs to whoever creates it
first: they can block it, plant a symlink to another place, or read and swap what PipesHub writes there. A folder
made here is created 0700 and used only when it is a real directory (not a link) that the running user owns and no
other user can write; anything else is refused in plain words and never changed.
"""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile


class PrivateDirUnsafe(PermissionError):
    """A folder that is not the running user's private directory; nothing is written to it."""


def per_user_temp_dir(name: str) -> str:
    """``<tempdir>/<name>-<uid>``: a name of the running user's own (not created; ``ensure_private_dir`` does)."""
    return os.path.join(tempfile.gettempdir(), f"{name}-{os.getuid()}")


def check_private_dir(path: str, what: str = "folder", hint: str = "") -> None:
    """Refuses ``path`` (``PrivateDirUnsafe``) unless it is a real directory the running user owns that no other
    user can write. ``what`` names it in the refusal; ``hint`` says what to do."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise PrivateDirUnsafe(f"The {what} {path} could not be created.{hint}") from None
    if stat.S_ISLNK(st.st_mode):
        raise PrivateDirUnsafe(f"The {what} {path} is a link, not a folder, so it is not used.{hint}")
    if not stat.S_ISDIR(st.st_mode):
        raise PrivateDirUnsafe(f"The {what} {path} is not a folder, so it is not used.{hint}")
    if st.st_uid != os.getuid():
        raise PrivateDirUnsafe(f"The {what} {path} belongs to another user, so it is not used.{hint}")
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise PrivateDirUnsafe(f"Other users can write to the {what} {path}, so it is not used.{hint}")


def ensure_private_dir(path: str, what: str = "folder", hint: str = "") -> str:
    """Makes ``path`` 0700 when missing (its missing parents 0700 too), refuses it unless it is the running user's
    private directory (``check_private_dir``), and answers its resolved path."""
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent):
        os.makedirs(parent, mode=0o700, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.mkdir(path, 0o700)
    check_private_dir(path, what, hint)
    return os.path.realpath(path)


__all__ = ["PrivateDirUnsafe", "check_private_dir", "ensure_private_dir", "per_user_temp_dir"]
