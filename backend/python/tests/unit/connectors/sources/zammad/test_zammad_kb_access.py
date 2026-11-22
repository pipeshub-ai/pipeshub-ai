"""Who may read a Zammad knowledge-base answer (``app.connectors.sources.zammad.kb_access``).

An answer's state is the first of archived, published and internal whose time is
set and already passed, else draft. A role's access to a category is its row
there, bounded by the role's own KB permission, or with no row its default.
"""

from datetime import datetime, timezone

import pytest

from app.connectors.sources.zammad.kb_access import (
    ARCHIVED,
    DRAFT,
    INTERNAL,
    PUBLISHED,
    CategoryAccess,
    answer_changed_at,
    answer_state,
)

NOW = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
PAST = "2026-01-01T00:00:00Z"
EARLIER = "2025-06-01T00:00:00Z"
FUTURE = "2026-12-01T00:00:00Z"


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


class TestAnswerState:
    def test_no_timestamp_is_a_draft(self) -> None:
        assert answer_state({}, NOW) == DRAFT
        assert answer_state({"published_at": None, "internal_at": "", "archived_at": None}, NOW) == DRAFT

    @pytest.mark.parametrize(("field", "state"), [
        ("published_at", PUBLISHED), ("internal_at", INTERNAL), ("archived_at", ARCHIVED),
    ])
    def test_one_passed_timestamp_sets_the_state(self, field: str, state: str) -> None:
        assert answer_state({field: PAST}, NOW) == state

    def test_an_archived_answer_keeps_published_at_and_is_archived(self) -> None:
        assert answer_state({"published_at": EARLIER, "archived_at": PAST}, NOW) == ARCHIVED

    def test_archived_wins_even_when_published_after_it(self) -> None:
        # Zammad tests the states in order, not by which time is latest.
        assert answer_state({"archived_at": EARLIER, "published_at": PAST}, NOW) == ARCHIVED

    def test_published_wins_over_internal(self) -> None:
        assert answer_state({"internal_at": PAST, "published_at": EARLIER}, NOW) == PUBLISHED

    def test_a_scheduled_publish_does_not_count_until_it_comes(self) -> None:
        answer = {"internal_at": PAST, "published_at": FUTURE}
        assert answer_state(answer, NOW) == INTERNAL
        assert answer_state(answer, ms(FUTURE) + 1) == PUBLISHED

    def test_a_scheduled_archive_leaves_it_published_until_it_comes(self) -> None:
        answer = {"published_at": PAST, "archived_at": FUTURE}
        assert answer_state(answer, NOW) == PUBLISHED
        assert answer_state(answer, ms(FUTURE) + 1) == ARCHIVED

    def test_a_timestamp_at_now_has_not_passed(self) -> None:
        assert answer_state({"published_at": PAST}, ms(PAST)) == DRAFT

    @pytest.mark.parametrize("bad", ["not a date", 12345, ["2026-01-01"]])
    def test_an_unreadable_timestamp_does_not_count(self, bad: object) -> None:
        assert answer_state({"published_at": bad}, NOW) == DRAFT

    def test_offsets_and_milliseconds_are_read(self) -> None:
        assert answer_state({"published_at": "2026-01-01T01:00:00.123+01:00"}, NOW) == PUBLISHED


class TestAnswerChangedAt:
    def test_the_last_edit(self) -> None:
        assert answer_changed_at({"updated_at": PAST}, NOW) == ms(PAST)

    def test_a_state_change_that_came_due_after_the_last_edit(self) -> None:
        answer = {"updated_at": EARLIER, "published_at": PAST}
        assert answer_changed_at(answer, NOW) == ms(PAST)

    def test_a_state_change_still_to_come_does_not_count(self) -> None:
        answer = {"updated_at": EARLIER, "published_at": FUTURE}
        assert answer_changed_at(answer, NOW) == ms(EARLIER)
        assert answer_changed_at(answer, ms(FUTURE) + 1) == ms(FUTURE)

    def test_nothing_known_is_zero(self) -> None:
        assert answer_changed_at({}, NOW) == 0
        assert answer_changed_at({"updated_at": "", "published_at": FUTURE}, NOW) == 0


def perms(editors: list[object] = (), readers: list[object] = (), rows: list[dict] | None = None) -> dict:
    return {"roles_editor": list(editors), "roles_reader": list(readers), "permissions": rows or []}


class TestCategoryAccess:
    def test_roles_without_a_row_have_their_default(self) -> None:
        access = CategoryAccess.from_permissions_response(perms([{"id": 2}], [{"id": 3}]))
        assert access == CategoryAccess(editor_role_ids=(2,), reader_role_ids=(3,))

    def test_role_ids_may_be_plain_ids(self) -> None:
        assert CategoryAccess.from_permissions_response(perms([2], [3])) == CategoryAccess((2,), (3,))

    def test_an_editor_row_makes_an_editor_role_an_editor(self) -> None:
        rows = [{"role_id": 2, "access": "editor"}]
        assert CategoryAccess.from_permissions_response(perms([2], [], rows)) == CategoryAccess((2,), ())

    def test_a_reader_row_makes_an_editor_role_a_reader(self) -> None:
        rows = [{"role_id": 2, "access": "reader"}]
        assert CategoryAccess.from_permissions_response(perms([2], [], rows)) == CategoryAccess((), (2,))

    @pytest.mark.parametrize("roles", [([2], []), ([], [3])])
    def test_a_none_row_takes_the_role_out(self, roles: tuple[list[int], list[int]]) -> None:
        editors, readers = roles
        rows = [{"role_id": r, "access": "none"} for r in editors + readers]
        assert CategoryAccess.from_permissions_response(perms(editors, readers, rows)) == CategoryAccess((), ())

    def test_an_editor_row_gives_a_reader_role_no_more_than_reading(self) -> None:
        rows = [{"role_id": 3, "access": "editor"}]
        assert CategoryAccess.from_permissions_response(perms([], [3], rows)) == CategoryAccess((), (3,))

    def test_a_row_of_a_role_with_neither_permission_gives_nothing(self) -> None:
        rows = [{"role_id": 9, "access": "editor"}, {"role_id": 8, "access": "reader"}]
        assert CategoryAccess.from_permissions_response(perms([], [], rows)) == CategoryAccess((), ())

    def test_an_editor_is_not_repeated_as_a_reader(self) -> None:
        # Zammad lists editors under roles_editor only, but a role in both counts once, as editor.
        assert CategoryAccess.from_permissions_response(perms([2], [2])) == CategoryAccess((2,), ())

    def test_ids_are_sorted(self) -> None:
        access = CategoryAccess.from_permissions_response(perms([{"id": 9}, {"id": 2}], [{"id": 7}, {"id": 3}]))
        assert access == CategoryAccess((2, 9), (3, 7))

    @pytest.mark.parametrize("data", [
        {},
        {"roles_editor": None, "roles_reader": "2", "permissions": {"role_id": 2}},
        {"roles_editor": [{"id": "2"}, {"id": True}, None], "permissions": [None, {"role_id": "2"}]},
    ])
    def test_malformed_parts_grant_nothing(self, data: dict) -> None:
        assert CategoryAccess.from_permissions_response(data) == CategoryAccess((), ())

    def test_rows_for_some_roles_and_defaults_for_the_rest(self) -> None:
        rows = [{"role_id": 2, "access": "reader"}, {"role_id": 4, "access": "none"}]
        access = CategoryAccess.from_permissions_response(perms([2, 5], [3, 4], rows))
        assert access == CategoryAccess(editor_role_ids=(5,), reader_role_ids=(2, 3))
