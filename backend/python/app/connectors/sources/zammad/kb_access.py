"""Who may read a Zammad knowledge-base answer, by Zammad's own rules.

Two rules, ported from Zammad (tag 6.4.1, unchanged in 7.2):

* An answer's state is the first of archived, published and internal whose
  timestamp is set and already passed, else draft
  (``lib/can_be_published/state_machine.rb#calculated_state``). A transition keeps
  the earlier timestamps, so an archived answer still carries ``published_at``,
  and a timestamp in the future (a scheduled change) does not count yet.
* A role's access to a category is its permission row there (rows are already
  inherited from parent categories in ``permissions``), or with no row its
  default: editor with ``knowledge_base.editor``, reader with
  ``knowledge_base.reader``. A row counts only as far as the role's own
  permission goes: an ``editor`` row makes an editor only of an editor role, and
  any row of a role with neither permission gives nothing
  (``lib/knowledge_base/effective_permission.rb``).

Editors read every answer, readers internal and published ones, and everyone a
published one (``app/policies/knowledge_base/answer_policy.rb``).

This module reads nothing itself.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import cast

ARCHIVED = "archived"
PUBLISHED = "published"
INTERNAL = "internal"
DRAFT = "draft"
# The order Zammad tests the states in.
_STATE_ORDER = (ARCHIVED, PUBLISHED, INTERNAL)


def _epoch_ms(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def answer_state(answer: Mapping[str, object], now_ms: int) -> str:
    """The answer's state at ``now_ms``: archived, published, internal or draft."""
    for state in _STATE_ORDER:
        at = _epoch_ms(answer.get(f"{state}_at"))
        if at is not None and at < now_ms:
            return state
    return DRAFT


def answer_changed_at(answer: Mapping[str, object], now_ms: int) -> int:
    """When the answer last changed as a reader sees it: its last edit, or a state change that has come due.

    A scheduled state change (a timestamp set ahead) takes effect without touching
    ``updated_at``, so a sync that compares ``updated_at`` alone would never see it.
    """
    times = [_epoch_ms(answer.get("updated_at")) or 0]
    for state in _STATE_ORDER:
        at = _epoch_ms(answer.get(f"{state}_at"))
        if at is not None and at < now_ms:
            times.append(at)
    return max(times)


@dataclass(frozen=True)
class CategoryAccess:
    """The roles with editor and with reader access to one category (editors are not repeated as readers)."""

    editor_role_ids: tuple[int, ...] = ()
    reader_role_ids: tuple[int, ...] = ()

    @classmethod
    def from_permissions_response(cls, data: Mapping[str, object]) -> "CategoryAccess":
        """From ``GET /knowledge_bases/{kb}/categories/{id}/permissions``.

        ``roles_editor`` are the roles holding ``knowledge_base.editor``,
        ``roles_reader`` those holding ``knowledge_base.reader`` but not editor,
        ``permissions`` the category's effective rows.
        """
        editor_roles = _role_ids(data.get("roles_editor"))
        reader_roles = _role_ids(data.get("roles_reader")) - editor_roles
        rows: dict[int, str] = {}
        raw_rows = data.get("permissions")
        for raw_row in cast(list[object], raw_rows) if isinstance(raw_rows, list) else []:
            if not isinstance(raw_row, Mapping):
                continue
            row = cast(Mapping[str, object], raw_row)
            role_id = row.get("role_id")
            if isinstance(role_id, int) and not isinstance(role_id, bool):
                rows[role_id] = str(row.get("access"))
        editors: set[int] = set()
        readers: set[int] = set()
        for role_id in editor_roles:
            access = rows.get(role_id, "editor")
            if access == "editor":
                editors.add(role_id)
            elif access == "reader":
                readers.add(role_id)
        for role_id in reader_roles:
            if rows.get(role_id, "reader") in ("editor", "reader"):
                readers.add(role_id)
        return cls(tuple(sorted(editors)), tuple(sorted(readers)))


def _role_ids(roles: object) -> set[int]:
    ids: set[int] = set()
    if isinstance(roles, Iterable) and not isinstance(roles, (str, bytes, Mapping)):
        for role in cast(Iterable[object], roles):
            role_id = cast(Mapping[str, object], role).get("id") if isinstance(role, Mapping) else role
            if isinstance(role_id, int) and not isinstance(role_id, bool):
                ids.add(role_id)
    return ids
