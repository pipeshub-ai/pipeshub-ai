"""Dropbox file members that were invited on the file, not inherited from a folder."""


class DropboxGrants(list):
    """Direct grants. ``covered_emails`` includes inherited members, so they are not granted again."""

    def __init__(self, permissions: list, *, covered_emails: set[str]) -> None:
        super().__init__(permissions)
        self.covered_emails = covered_emails


def member_is_direct(is_inherited: bool | None) -> bool:
    """Missing ``is_inherited`` stays. Only an explicit True is a parent-folder member."""
    return is_inherited is not True


def fallback_for_uncovered_user(*, grants_empty: bool, user_is_covered: bool) -> str | None:
    """Owner when the item has no members, write when this user is missing from a real member list.

    A user who only inherits the parent is covered and gets nothing extra.
    """
    if user_is_covered:
        return None
    if grants_empty:
        return "owner"
    return "write"
