"""``find_records`` over stored records whose content the installed locator says belongs to another record: access is judged for
the record they belong to, they answer once for it, and without such a locator the search is dedupe-then-rank as it always was."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from app.modules.retrieval.content_locator import (
    SingleHolderLocator,
    set_locator_factory,
)
from tests.unit.agents.actions.test_storage_search import (
    _VRID_ACCESSIBLE,
    _VRID_FORBIDDEN,
    _make_perm_tool,
)

if TYPE_CHECKING:
    from pathlib import Path

HELD_A = "cccccccccccccccccccccccc"
HELD_B = "dddddddddddddddddddddddd"
HELD_NO = "eeeeeeeeeeeeeeeeeeeeeeee"


class Owners(SingleHolderLocator):
    def __init__(self, owners: dict[str, str], *, boom: bool = False) -> None:
        self.owners, self.boom, self.asked = owners, boom, []

    async def owners_of(self, vrids: object) -> dict[str, str]:
        self.asked.append(list(vrids))  # type: ignore[call-overload]
        if self.boom:
            raise RuntimeError("layout unreadable")
        return self.owners


def _seed(tmp_path: Path, *names: str) -> None:
    folder = tmp_path / "grp" / "Doc" / "sid"
    folder.mkdir(parents=True)
    for name in names:
        (folder / f"record_{name}.json").write_text('{"id":"x"} secretword marker')


async def _find(tool: object) -> dict:
    success, output = await tool.find_records("c", 'grep -rl "secretword" .')  # type: ignore[attr-defined]
    assert success is True
    return json.loads(output)


async def test_stored_records_that_belong_to_one_record_answer_once_for_it(tmp_path: Path) -> None:
    _seed(tmp_path, HELD_A, HELD_B, _VRID_ACCESSIBLE)
    owners = {HELD_A: _VRID_ACCESSIBLE, HELD_B: _VRID_ACCESSIBLE}
    set_locator_factory(lambda graph: Owners(owners))
    tool = _make_perm_tool(str(tmp_path), {_VRID_ACCESSIBLE: "rec-ok"})

    result = await _find(tool)

    assert [r["virtual_record_id"] for r in result["records"]] == [_VRID_ACCESSIBLE]
    checked = tool.state["graph_provider"].filter_accessible_virtual_record_ids.await_args.args[0]
    assert sorted(checked) == [_VRID_ACCESSIBLE]


async def test_a_stored_record_is_judged_by_the_access_to_the_record_it_belongs_to(tmp_path: Path) -> None:
    _seed(tmp_path, HELD_A, HELD_NO)
    set_locator_factory(lambda graph: Owners({HELD_A: _VRID_ACCESSIBLE, HELD_NO: _VRID_FORBIDDEN}))
    tool = _make_perm_tool(str(tmp_path), {_VRID_ACCESSIBLE: "rec-ok"})

    result = await _find(tool)

    assert [r["record_id"] for r in result["records"]] == ["rec-ok"]
    assert HELD_NO not in json.dumps(result) and _VRID_FORBIDDEN not in json.dumps(result)


async def test_when_the_layout_cannot_be_read_only_the_unresolved_ones_are_denied(tmp_path: Path) -> None:
    _seed(tmp_path, HELD_A, _VRID_ACCESSIBLE)
    set_locator_factory(lambda graph: Owners({}, boom=True))
    tool = _make_perm_tool(str(tmp_path), {_VRID_ACCESSIBLE: "rec-ok"})

    result = await _find(tool)

    assert [r["virtual_record_id"] for r in result["records"]] == [_VRID_ACCESSIBLE]


async def test_with_the_default_locator_the_search_is_dedupe_then_rank_and_asks_for_the_stored_ids(tmp_path: Path) -> None:
    _seed(tmp_path, HELD_A, _VRID_ACCESSIBLE)
    tool = _make_perm_tool(str(tmp_path), {_VRID_ACCESSIBLE: "rec-ok"})
    tool.state["graph_provider"].get_records_by_record_ids = AsyncMock(return_value=[{"_key": "rec-ok", "recordName": "n"}])

    result = await _find(tool)

    assert [r["virtual_record_id"] for r in result["records"]] == [_VRID_ACCESSIBLE]
    assert tool._owner_of == {}
    checked = tool.state["graph_provider"].filter_accessible_virtual_record_ids.await_args.args[0]
    assert sorted(checked) == sorted([HELD_A, _VRID_ACCESSIBLE])
