"""Path normalization for network-share crawls. Pure; no I/O."""

from __future__ import annotations

import unicodedata

LOCK_PREFIX = "~$"


def normalize_rel_path(parent_dir: str, name: str) -> str | None:
    """Join parent and name into an NFC path inside a share.

    Backslashes become slashes. ``.`` segments are dropped. ``..`` pops a
    segment; escaping the share returns None. Casing is preserved.
    """
    raw = f"{parent_dir}/{name}" if parent_dir else name
    raw = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    parts: list[str] = []
    for segment in raw.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(segment)
    return "/".join(parts)


def parent_of(rel_path: str) -> str | None:
    if not rel_path:
        return None
    if "/" not in rel_path:
        return None
    parent = rel_path.rsplit("/", 1)[0]
    return parent or None


def file_extension(rel_path: str) -> str | None:
    name = rel_path.rsplit("/", 1)[-1]
    if "." not in name or name.startswith("."):
        # ".bashrc" has no extension we treat as a filter key
        if name.startswith(".") and name.count(".") == 1:
            return None
        parts = name.rsplit(".", 1)
        if len(parts) != 2 or not parts[-1]:
            return None
        return parts[-1].lower()
    parts = name.rsplit(".", 1)
    if not parts[-1]:
        return None
    return parts[-1].lower()


def is_lock_file(name: str) -> bool:
    return name.startswith(LOCK_PREFIX)


def is_dot_entry(name: str) -> bool:
    return name in (".", "..")


def is_ads_name(name: str) -> bool:
    """Alternate data stream names look like ``file.txt:Zone.Identifier``."""
    return ":" in name
