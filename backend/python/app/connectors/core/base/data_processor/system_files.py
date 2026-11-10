"""Names of files no one searches for: Office lock files and what Windows and macOS write next to a user's files."""

OFFICE_LOCK_PREFIX = "~$"

SYSTEM_FILE_NAMES = frozenset(name.casefold() for name in ("Thumbs.db", "desktop.ini", ".DS_Store"))


def is_office_lock_file(name: str) -> bool:
    return name.startswith(OFFICE_LOCK_PREFIX)


def is_system_file_name(name: str) -> bool:
    return name.casefold() in SYSTEM_FILE_NAMES
