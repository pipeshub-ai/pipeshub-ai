"""The one check of a name or path a person or an engine supplies, before it becomes a file anywhere (SEC-FS-02).

A name ends up on a Linux sandbox, in a zip a person opens on Windows or macOS, in a storage key and in a download
header, so it must be safe on all of them. Node has the same rules (``domain/safe-names.ts``);
``contracts/harness/vectors/safe-names.json`` pins both.

- ``check_name``: one file name a person or an engine chose (an upload, an input, an output, an artifact). Refuses
  anything that is not one plain segment: separators, ``.``/``..``, NUL and control characters, separators or dots
  hidden by percent-encoding or by look-alike characters, a drive letter or ``:``, a Windows device name, a trailing
  dot or space.
- ``check_portable_path``: a relative workspace path entering the store or leaving it in an archive. Each segment
  may be what Linux allows (a repository can hold ``aux.c``), but never a drive letter or an encoded separator.
- ``link_target_escapes``: whether a symbolic link's target is something other than a plain relative POSIX path
  (``..\\x``, ``C:/x``, ``%2e%2e/x``); where it lands inside the folder is ``sync.rules.link_escapes``'s check.

Stored manifests are not re-checked when read: a workspace saved before these rules stays readable, and the
archive and staging paths check what they hand out.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final
from urllib.parse import unquote

from harness_core.workspace.types import normalize_workspace_file_path

MAX_NAME_BYTES: Final = 255

EMPTY: Final = "empty"
TOO_LONG: Final = "too_long"
SEPARATOR: Final = "separator"
TRAVERSAL: Final = "traversal"
NUL: Final = "nul"
CONTROL: Final = "control"
ENCODED: Final = "encoded_separator"
DRIVE: Final = "drive"
RESERVED: Final = "reserved"
TRAILING: Final = "trailing_dot_or_space"
NOT_PATH: Final = "not_a_path"

_DRIVE = re.compile(r"^[A-Za-z]:")
_ENCODED = re.compile(r"%[0-9A-Fa-f]{2}")
_DECODE_ROUNDS = 4
# Windows opens these as devices whatever the extension; superscript digits count too.
_RESERVED_STEMS: Final = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "conin$",
        "conout$",
        *(f"{device}{digit}" for device in ("com", "lpt") for digit in "0123456789\u00b9\u00b2\u00b3"),
    }
)


class UnsafeName(ValueError):
    """A name or path refused; ``reason`` is one of this module's codes, the same in Node."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _hides_separator(segment: str) -> bool:
    """Whether decoding ``segment`` (percent-encoding, repeatedly, or compatibility forms such as ``／``) yields a
    separator, NUL or a dot segment that some later reader would act on."""
    forms = {segment, unicodedata.normalize("NFKC", segment)}
    current = segment
    for _ in range(_DECODE_ROUNDS):
        if not _ENCODED.search(current):
            break
        current = unquote(current, errors="surrogateescape")
        forms.update({current, unicodedata.normalize("NFKC", current)})
    return any("/" in form or "\\" in form or "\x00" in form or form in (".", "..") for form in forms - {segment})


def _is_control(char: str) -> bool:
    return unicodedata.category(char) == "Cc"


def check_name(name: str) -> str:
    """``name`` in NFC when it is one portable file name; ``UnsafeName`` with the reason otherwise."""
    if not isinstance(name, str) or not name:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise UnsafeName(EMPTY, "The name is empty")
    if "\x00" in name:
        raise UnsafeName(NUL, "The name contains a NUL character")
    normalized = unicodedata.normalize("NFC", name)
    if "/" in normalized or "\\" in normalized:
        reason = TRAVERSAL if normalized.startswith(("/", "\\")) else SEPARATOR
        raise UnsafeName(reason, "The name contains a folder separator")
    if normalized in (".", ".."):
        raise UnsafeName(TRAVERSAL, "The name points at a folder")
    if any(_is_control(char) for char in normalized):
        raise UnsafeName(CONTROL, "The name contains a control character")
    if _hides_separator(normalized):
        raise UnsafeName(ENCODED, "The name hides a folder separator or a dot folder")
    if ":" in normalized:
        reason = DRIVE if _DRIVE.match(normalized) else SEPARATOR
        raise UnsafeName(reason, "The name contains a colon, which names a drive or a hidden stream on Windows")
    if normalized[-1] in ". ":
        raise UnsafeName(TRAILING, "The name ends with a dot or a space")
    if normalized.split(".", 1)[0].rstrip(" ").lower() in _RESERVED_STEMS:
        raise UnsafeName(RESERVED, "The name is a device name on Windows")
    if len(normalized.encode("utf-8", "surrogatepass")) > MAX_NAME_BYTES:
        raise UnsafeName(TOO_LONG, "The name is too long")
    return normalized


def check_name_path(path: str) -> str:
    """A relative path whose every segment passes ``check_name`` (an output under its folder: ``charts/q3.png``)."""
    canonical = check_portable_path(path)
    for segment in canonical.split("/"):
        _ = check_name(segment)
    return canonical


def check_portable_path(path: str) -> str:
    """``path`` canonical (``normalize_workspace_file_path``) with no drive letter or encoded separator in any
    segment; ``UnsafeName`` otherwise."""
    if isinstance(path, str) and path.startswith(("/", "\\")):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise UnsafeName(TRAVERSAL, "The path is absolute")
    if isinstance(path, str) and _DRIVE.match(path):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise UnsafeName(DRIVE, "The path starts with a drive")
    try:
        canonical = normalize_workspace_file_path(path)
    except ValueError as error:
        raise UnsafeName(_path_reason(path), str(error)) from None
    for segment in canonical.split("/"):
        if _DRIVE.match(segment):
            raise UnsafeName(DRIVE, "A folder or file name in the path names a drive")
        if _hides_separator(segment):
            raise UnsafeName(ENCODED, "The path hides a folder separator or a dot folder")
    return canonical


def _path_reason(path: object) -> str:
    if not isinstance(path, str) or not path:
        return EMPTY
    if "\x00" in path:
        return NUL
    if "\\" in path:
        return SEPARATOR
    if any(segment in (".", "..") for segment in path.split("/")):
        return TRAVERSAL
    return NOT_PATH


def link_target_escapes(target: str) -> bool:
    """Whether a link's target is no plain relative POSIX path: absolute, a drive, a backslash or an encoded
    separator anywhere. Plain targets still go through ``link_escapes`` for where they land."""
    if not target or "\x00" in target or "\\" in target or target.startswith("/") or _DRIVE.match(target):
        return True
    return any(_DRIVE.match(segment) or _hides_separator(segment) for segment in target.split("/") if segment)


def safe_name(name: str) -> bool:
    try:
        _ = check_name(name)
    except UnsafeName:
        return False
    return True


__all__ = [
    "MAX_NAME_BYTES",
    "UnsafeName",
    "check_name",
    "check_name_path",
    "check_portable_path",
    "link_target_escapes",
    "safe_name",
]
