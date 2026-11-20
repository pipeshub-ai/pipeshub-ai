"""The chat's files a turn's model is told about (`told_files`): in a run others read, those every member of the chat
may open and those the request picked; in a run nobody else reads, every file its asker may open.

Without an edition that says others read a run, every file is told about, as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from app.modules.agents.chat_extension import ChatExtension
from app.modules.agents.context.told_files import (
    PICKED_FILES_KEY,
    TOLD_FILES_KEY,
    ToldFiles,
    picked_file_ids,
    told_files_in,
)
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext


class _OthersRead(ChatExtension):
    def read_by_others(self, context: AgentContext) -> bool:
        return True


def _context(extension: ChatExtension | None = None, **fields: Any) -> AgentContext:  # noqa: ANN401
    target = extension or ChatExtension()
    with (
        patch("app.modules.agents.chat_extension.get_chat_extension", return_value=target),
        patch("app.agents.agent_loop.context.get_chat_extension", return_value=target),
    ):
        # The context works out what its model is told about as it is built, while the extension is patched in.
        return make_context(**fields)


class TestPickedFileIds:
    def test_records_picked_by_id_alone_or_with_what_is_under_them_once_each(self) -> None:
        assert picked_file_ids({"records": ["f-1", " f-2 "], "recordsExact": ["f-2", "f-3"]}) == ["f-1", "f-2", "f-3"]

    def test_a_source_to_search_is_not_a_file_picked(self) -> None:
        assert picked_file_ids({"apps": ["app-1"], "kb": ["kb-1"], "recordGroups": ["group-1"]}) is None

    def test_nothing_usable_picks_nothing(self) -> None:
        assert picked_file_ids(None) is None
        assert picked_file_ids({"records": [" ", 3, None]}) is None


class TestToldFiles:
    def test_a_run_nobody_else_reads_is_told_of_every_file_its_asker_may_open(self) -> None:
        context = _context(tool_state={PICKED_FILES_KEY: ["f-1"]})
        assert context.told_files == ToldFiles()
        assert told_files_in(context.tool_state) == ToldFiles()

    def test_a_run_others_read_is_told_of_every_member_s_files_and_the_ones_it_picked(self) -> None:
        context = _context(_OthersRead(), tool_state={PICKED_FILES_KEY: ["f-1", "f-2"]})
        expected = ToldFiles(every_member_only=True, picked=frozenset({"f-1", "f-2"}))
        assert context.told_files == expected
        # The artifacts tools read it from the run's state.
        assert context.tool_state[TOLD_FILES_KEY] == expected

    def test_a_run_others_read_that_picked_nothing_is_told_of_every_member_s_files_alone(self) -> None:
        assert _context(_OthersRead()).told_files == ToldFiles(every_member_only=True)

    def test_a_state_built_outside_a_run_says_nothing_of_it(self) -> None:
        assert told_files_in({}) is None
