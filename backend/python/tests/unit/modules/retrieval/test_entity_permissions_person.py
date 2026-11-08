"""``list_accessible_person_records``: the person entity goes through the same
listing engine as every other entity — one server-built ref, the access check,
the turn's limit pushed into the query, newest-first paging."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.modules.retrieval.entity_permissions import (
    PERSON_ENTITY_TYPE,
    EntityAccessContext,
    list_accessible_person_records,
)
from app.modules.retrieval.selection_scope import SelectionScope
from tests.unit.modules.retrieval.entity_access_fakes import entity_graph

if TYPE_CHECKING:
    from unittest.mock import MagicMock

JIRA = "jira-1"


def _context(row_scopes: tuple[SelectionScope, ...] = ()) -> EntityAccessContext:
    return EntityAccessContext(
        org_id="org-1",
        user_key="ukey",
        app_level_app_ids=frozenset(),
        record_level_app_ids=frozenset({JIRA}),
        record_group_ids=frozenset({"proj-a"}),
        app_names={JIRA: "Jira"},
        row_scopes=row_scopes,
    )


def _rows(*keys: str) -> list[dict]:
    return [{"_key": k, "connectorId": JIRA, "recordName": f"T {k}"} for k in keys]


def _graph(rows: list[dict], **kwargs: object) -> MagicMock:
    return entity_graph(lambda refs, org, **_: {(PERSON_ENTITY_TYPE, "u1"): rows}, **kwargs)


class TestPersonListing:
    async def test_the_ref_is_built_server_side_by_direction(self) -> None:
        graph = _graph(_rows("r1"), permitted=("r1",))
        await list_accessible_person_records(
            graph, _context(), user_ids=["u1", "u2", "u1"],
            edge_types=["MENTIONED_IN", "REPORTED_BY", "RELATED_TO_PERSON", "BELONGS_TO"],
        )
        (ref,) = graph.get_permitted_entity_records.await_args.args[0]
        assert ref == {
            "id": "u1", "type": PERSON_ENTITY_TYPE, "userIds": ["u1", "u2"],
            "outTypes": ["REPORTED_BY"], "inTypes": ["MENTIONED_IN"], "connectorIds": [JIRA],
        }

    async def test_only_readable_records_are_listed_and_the_end_is_known(self) -> None:
        graph = _graph(_rows("r1", "r2", "r3"), permitted=("r1", "r3"))
        page = await list_accessible_person_records(
            graph, _context(), user_ids=["u1"], edge_types=["REPORTED_BY"],
        )
        assert [r["_key"] for r in page.records] == ["r1", "r3"]
        assert page.next_cursor is None and not page.capped

    async def test_a_full_page_gives_an_integer_cursor(self) -> None:
        graph = _graph(_rows("r1", "r2", "r3"), permitted=("r1", "r2", "r3"))
        page = await list_accessible_person_records(
            graph, _context(), user_ids=["u1"], edge_types=["ASSIGNED_TO"], limit=2,
        )
        assert [r["_key"] for r in page.records] == ["r1", "r2"]
        assert page.next_cursor == "2"

    async def test_the_turns_limit_is_pushed_into_the_query(self) -> None:
        scope = SelectionScope(groups={"proj-a": JIRA})
        graph = _graph(_rows("r1", "r2"), permitted=("r1", "r2"), groups={"r1": ["proj-a"]})
        page = await list_accessible_person_records(
            graph, _context((scope,)), user_ids=["u1"], edge_types=["REPORTED_BY"],
        )
        assert graph.fake.within_calls == [[scope.candidate_filter()]]
        assert [r["_key"] for r in page.records] == ["r1"]

    @pytest.mark.parametrize("user_ids, edge_types", [([], ["REPORTED_BY"]), (["u1"], ["BELONGS_TO"])])
    async def test_no_user_or_no_allowed_relation_is_refused(self, user_ids, edge_types) -> None:
        with pytest.raises(ValueError):
            await list_accessible_person_records(
                _graph([]), _context(), user_ids=user_ids, edge_types=edge_types,
            )
