"""Which code-repository records (GitLab, GitHub) the current indexing filters keep out.

A reindex skips these, so a record type switched off is not indexed by
"Manual index" or a project reindex.
"""
from types import SimpleNamespace
from typing import Never

import pytest

from app.connectors.core.registry.code_indexing_flags import (
    records_excluded_by_repo_indexing_filters,
)
from app.connectors.core.registry.filters import Filter, FilterCollection


def _filters(**values: bool) -> FilterCollection:
    return FilterCollection(filters=[
        Filter(key=key, value=value, type="boolean", operator="is") for key, value in values.items()
    ])


ISSUE = SimpleNamespace(id="issue", record_type="TICKET", external_record_id="ext-issue")
MERGE_REQUEST = SimpleNamespace(id="mr", record_type="PULL_REQUEST", external_record_id="ext-mr")
SOURCE_FILE = SimpleNamespace(id="src", record_type="CODE_FILE", file_role="source")
TEST_FILE = SimpleNamespace(id="test", record_type="CODE_FILE", file_role="test")
ISSUE_ATTACHMENT = SimpleNamespace(id="issue-att", record_type="FILE", parent_external_record_id="ext-issue")
MR_ATTACHMENT = SimpleNamespace(id="mr-att", record_type="FILE", parent_external_record_id="ext-mr")
ORPHAN_ATTACHMENT = SimpleNamespace(id="orphan-att", record_type="FILE", parent_external_record_id="ext-gone")

ALL = [ISSUE, MERGE_REQUEST, SOURCE_FILE, TEST_FILE, ISSUE_ATTACHMENT, MR_ATTACHMENT, ORPHAN_ATTACHMENT]


async def _find_parent(external_id: str) -> SimpleNamespace | None:
    return {"ext-issue": ISSUE, "ext-mr": MERGE_REQUEST}.get(external_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("indexing_filters", "excluded"),
    [
        pytest.param(
            _filters(issues=True, merge_requests=False, code_files=True, test_files=False),
            {"mr", "mr-att", "test"},
            id="merge-requests-and-tests-off",
        ),
        pytest.param(
            _filters(issues=False, merge_requests=True, code_files=True, test_files=True),
            {"issue", "issue-att"},
            id="issues-off",
        ),
        pytest.param(
            _filters(code_files=False, test_files=True),
            {"src", "test"},
            id="code-files-off-covers-tests",
        ),
        pytest.param(
            _filters(issues=True, merge_requests=True, code_files=True, test_files=True),
            set(),
            id="everything-on",
        ),
        pytest.param(
            # Test files are off unless opted in, as when the sync stamps them.
            FilterCollection(),
            {"test"},
            id="no-filter-rows",
        ),
        pytest.param(None, {"test"}, id="no-filters"),
    ],
)
async def test_excluded_records_follow_the_type_toggles(indexing_filters, excluded) -> None:
    assert await records_excluded_by_repo_indexing_filters(ALL, indexing_filters, _find_parent) == excluded


@pytest.mark.asyncio
async def test_manual_indexing_does_not_exclude_types_that_are_on() -> None:
    # With manual indexing on, every record waits for a person to index it: the
    # reindex they start must reach the types that are switched on.
    filters = _filters(enable_manual_sync=True, issues=True, merge_requests=False, test_files=True)

    excluded = await records_excluded_by_repo_indexing_filters(ALL, filters, _find_parent)

    assert excluded == {"mr", "mr-att"}


@pytest.mark.asyncio
async def test_parents_are_looked_up_once_per_external_id() -> None:
    looked_up: list[str] = []

    async def find_parent(external_id: str) -> SimpleNamespace | None:
        looked_up.append(external_id)
        return await _find_parent(external_id)

    second_mr_attachment = SimpleNamespace(id="mr-att-2", record_type="FILE", parent_external_record_id="ext-mr")
    await records_excluded_by_repo_indexing_filters(
        [MR_ATTACHMENT, second_mr_attachment], _filters(merge_requests=False), find_parent,
    )

    assert looked_up == ["ext-mr"]


@pytest.mark.asyncio
async def test_repository_folders_are_not_looked_up() -> None:
    # A project reindex walks every folder of the repository; a lookup per folder
    # would be one graph read per directory for no decision.
    async def find_parent(external_id: str) -> Never:
        raise AssertionError(f"looked up {external_id}")

    folder = SimpleNamespace(
        id="folder", record_type="FILE", is_file=False, parent_external_record_id="ext-parent-folder",
    )
    excluded = await records_excluded_by_repo_indexing_filters(
        [folder], _filters(merge_requests=False, issues=False), find_parent,
    )

    assert excluded == set()
