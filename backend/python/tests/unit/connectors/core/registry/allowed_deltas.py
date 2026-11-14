"""Where a saved folder value decides differently from before folder patterns, and why.

Each row names the call, its input, what main's FolderScope answered
(``_main_folder_scope.py``) and what the live one answers.
``test_allowed_deltas.py`` checks both sides, so a row cannot drift from the
code. Rows with ``wildcards`` apply once a connector reads patterns; rows on
file shares apply to SMB, CIFS and Azure Files. Changes to walks and record ids
are covered by their own tests.

``SAVE_DELTAS`` are the values a save now refuses when they are new (not saved
before under the same operator). A saved value is never refused for them, so
enabling and saving keep working. ``SAVE_UNCHANGED`` are new values a save
still accepts as before, read as they always were.
"""

from __future__ import annotations

import unicodedata

NFD_CAFE = unicodedata.normalize("NFD", "café")
NFC_CAFE = unicodedata.normalize("NFC", "café")

DELTAS: list[dict[str, object]] = [
    {
        "id": "case", "rules": "windows", "wildcards": False, "operator": "in", "values": ["Reports"],
        "function": "includes_file", "arg": "reports/x", "old": False, "new": True,
        "reason": "File shares compare names without regard to case.",
    },
    {
        "id": "case", "rules": "windows", "wildcards": False, "operator": "not_in", "values": ["Reports"],
        "function": "includes_folder", "arg": "reports", "old": True, "new": False,
        "reason": "An excluded folder is excluded whatever the case it is listed in.",
    },
    {
        "id": "backslash", "rules": "windows", "wildcards": False, "operator": "in", "values": ["reports\\2026"],
        "function": "includes_file", "arg": "reports/2026/x", "old": False, "new": True,
        "reason": "On a file share \\ separates folders; before, such a value synced nothing.",
    },
    {
        "id": "backslash", "rules": "windows", "wildcards": False, "operator": "in", "values": ["\\"],
        "function": "is_everything", "arg": None, "old": False, "new": True,
        "reason": "A lone \\ is the top of the share, as / is.",
    },
    {
        "id": "unicode_form", "rules": "windows", "wildcards": False, "operator": "in", "values": [NFD_CAFE],
        "function": "includes_file", "arg": f"{NFC_CAFE}/x", "old": False, "new": True,
        "reason": "File-share record ids are NFC, so a value typed in NFD is compared in NFC.",
    },
    {
        "id": "equal_values", "rules": "windows", "wildcards": False, "operator": "in",
        "values": ["Reports", "reports/2026"],
        "function": "list_prefixes", "arg": None, "old": ["Reports/", "reports/2026/"], "new": ["Reports/"],
        "reason": "A value inside another one, whatever its case, is walked once, under the outer spelling.",
    },
    {
        "id": "wildcard", "rules": "posix", "wildcards": True, "operator": "in", "values": ["a*b"],
        "function": "includes_file", "arg": "axxb/x", "old": False, "new": True,
        "reason": "A saved * or ? becomes a wildcard: the value matches what it did and more.",
    },
    {
        "id": "escape", "rules": "posix", "wildcards": True, "operator": "in", "values": ["a\\*b"],
        "function": "includes_file", "arg": "a*b/x", "old": False, "new": True,
        "reason": "On an object store \\* and \\? are a literal * and ?.",
    },
    {
        "id": "wildcard_in_first_folder", "rules": "posix", "wildcards": True, "operator": "in",
        "values": ["*/quotes"],
        "function": "list_prefixes", "arg": None, "old": ["*/quotes/"], "new": [""],
        "reason": "A pattern with no fixed start lists the whole bucket.",
    },
]


SAVE_DELTAS: list[dict[str, object]] = [
    {"id": "empty_folder_name", "path_style": "posix", "wildcards": False, "operator": "in", "value": "a//b",
     "reason": "An empty name between two slashes names no folder anyone can create."},
    {"id": "excludes_everything", "path_style": "posix", "wildcards": False, "operator": "not_in", "value": "/",
     "reason": "Excluding the top leaves nothing to sync."},
    {"id": "excludes_everything", "path_style": "windows", "wildcards": True, "operator": "not_in", "value": "**",
     "reason": "Excluding the top leaves nothing to sync."},
    {"id": "too_long", "path_style": "posix", "wildcards": False, "operator": "in", "value": "a" * 1025,
     "reason": "No store takes a folder path that long."},
    {"id": "network_path", "path_style": "windows", "wildcards": False, "operator": "in",
     "value": "\\\\server\\share\\reports",
     "reason": "A \\\\server\\share path names the share itself, not a folder in it."},
    {"id": "double_star_in_name", "path_style": "posix", "wildcards": True, "operator": "in", "value": "a**b",
     "reason": "Once wildcards are read, ** is a whole folder name."},
]

SAVE_UNCHANGED: list[dict[str, object]] = [
    {"path_style": "windows", "operator": "in", "value": "//reports"},
    {"path_style": "windows", "operator": "in", "value": "//server/share"},
    {"path_style": "windows", "operator": "in", "value": "\\\\reports"},
    {"path_style": "posix", "operator": "in", "value": "//server/share"},
    {"path_style": "posix", "operator": "in", "value": "/reports/"},
    {"path_style": "posix", "operator": "not_in", "value": "archive"},
    {"path_style": "windows", "operator": "in", "value": "a**b"},
    # Without wildcards "**" is a folder of that name, so excluding it leaves the rest.
    {"path_style": "posix", "operator": "not_in", "value": "**"},
    {"path_style": "windows", "operator": "not_in", "value": "**/**"},
]
