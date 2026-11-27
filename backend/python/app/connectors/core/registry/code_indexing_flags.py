"""Indexing-filter reads shared by the code-repository connectors."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterType,
    IndexingFilterKey,
    ListOperator,
    OptionSourceType,
    SyncFilterKey,
)
from app.models.entities import Record, RecordType
from app.modules.parsers.code_parser.file_role import FileRole

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence


def code_indexing_flags(indexing_filters: FilterCollection | None) -> tuple[bool, bool]:
    """``(code_files_enabled, test_files_enabled)`` for a connector.

    Code files are on unless switched off. Test files are off unless opted in,
    even when the filters object exists but has no ``test_files`` row: a
    connector configured before that filter existed must not start indexing
    tests just because its config has no row for them.
    """
    if not indexing_filters:
        return True, False
    return (
        indexing_filters.is_enabled(IndexingFilterKey.CODE_FILES),
        indexing_filters.is_enabled(IndexingFilterKey.TEST_FILES, default=False),
    )


async def records_excluded_by_repo_indexing_filters(
    records: Sequence[Record],
    indexing_filters: FilterCollection | None,
    find_parent: Callable[[str], Awaitable[Record | None]],
) -> set[str]:
    """Ids of the records a code-repository connector's indexing filters keep out.

    Issues, merge/pull requests, code files and test files each follow their
    toggle, and an attachment follows its parent's, as the sync stamps them.
    ``find_parent`` resolves an attachment's parent by external id; an attachment
    whose parent is gone is not excluded. The toggles are read for themselves, not
    through manual indexing, which only decides whether indexing is automatic.
    """
    filters = indexing_filters or FilterCollection()

    def enabled(key: IndexingFilterKey, *, default: bool = True) -> bool:
        return filters.is_enabled(key, default=default, honour_manual_sync=False)

    type_toggles = {
        RecordType.TICKET.value: enabled(IndexingFilterKey.ISSUES),
        RecordType.PULL_REQUEST.value: enabled(IndexingFilterKey.MERGE_REQUESTS),
    }
    code_files_on = enabled(IndexingFilterKey.CODE_FILES)
    test_files_on = enabled(IndexingFilterKey.TEST_FILES, default=False)
    parent_types: dict[str, str | None] = {}

    async def parent_type(external_id: str) -> str | None:
        if external_id not in parent_types:
            parent = await find_parent(external_id)
            parent_types[external_id] = _type_value(parent.record_type) if parent else None
        return parent_types[external_id]

    excluded: set[str] = set()
    for record in records:
        record_type = _type_value(record.record_type)
        parent_id = getattr(record, "parent_external_record_id", None)
        if record_type in type_toggles:
            on = type_toggles[record_type]
        elif record_type == RecordType.CODE_FILE.value:
            on = code_files_on and (
                test_files_on or getattr(record, "file_role", None) != FileRole.TEST.value
            )
        elif (
            record_type == RecordType.FILE.value
            # Repository folders are FILE records too; only attachments follow a parent.
            and getattr(record, "is_file", True) is not False
            and parent_id
        ):
            parent = await parent_type(parent_id)
            on = type_toggles.get(parent, True) if parent else True
        else:
            on = True
        if not on:
            excluded.add(record.id)
    return excluded


def _type_value(record_type: object) -> str:
    return str(getattr(record_type, "value", record_type))


_CODE_INDEXING_KEYS = (IndexingFilterKey.CODE_FILES.value, IndexingFilterKey.TEST_FILES.value)


def code_indexing_filters_changed(old: dict | None, new: dict | None) -> bool:
    """Whether a save changed which code files are indexed, as stored config dicts."""
    old, new = old or {}, new or {}
    return any(old.get(key) != new.get(key) for key in _CODE_INDEXING_KEYS)


def sync_filters_changed_beyond_branch(old: dict | None, new: dict | None) -> bool:
    """Whether a save changed the sync filters, as stored config dicts, other than the branch.

    A branch change alone needs no full resync: each code connector keeps the
    branch it synced in its checkpoint, and on a different one syncs the
    difference between the two heads. The full resync deleted that checkpoint
    and the code graph's build state with it, so every file was walked and every
    edge rebuilt.
    """
    def without_branch(filters: dict | None) -> dict:
        filters = dict(filters or {})
        values = filters.get("values")
        if isinstance(values, dict):
            filters["values"] = {k: v for k, v in values.items() if k != SyncFilterKey.BRANCH.value}
        return filters

    return without_branch(old) != without_branch(new)


def exclude_paths_filter_field() -> FilterField:
    """The "Exclude paths" sync filter every code-repository connector declares (P-1)."""
    return FilterField(
        name=SyncFilterKey.EXCLUDE_PATHS.value,
        display_name="Exclude Paths",
        filter_type=FilterType.LIST,
        category=FilterCategory.SYNC,
        description=(
            "Paths to leave out of the index, gitignore-style: `docs/`, `**/fixtures/**`, "
            "`*.snap`. Dependency, build-output, generated and credential files are "
            "always left out."
        ),
        option_source_type=OptionSourceType.MANUAL,
        default_operator=ListOperator.IN.value,
    )


def branch_filter_field() -> FilterField:
    """The "Branch" sync filter every code-repository connector declares."""
    return FilterField(
        name=SyncFilterKey.BRANCH.value,
        display_name="Branch",
        filter_type=FilterType.SELECT,
        category=FilterCategory.SYNC,
        description=(
            "The branch whose files are indexed. Leave it unset to follow the "
            "repository's default branch."
        ),
        option_source_type=OptionSourceType.DYNAMIC,
    )


def code_branch(sync_filters: FilterCollection | None) -> str | None:
    """The branch the admin picked for a code connector; None means the default branch."""
    if not sync_filters:
        return None
    found = sync_filters.get(SyncFilterKey.BRANCH)
    if found is None or found.is_empty():
        return None
    value = found.get_value(default=None)
    # A SELECT is stored as a one-element list.
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    branch = str(value).strip() if value is not None else ""
    return branch or None


def code_exclude_globs(sync_filters: FilterCollection | None) -> tuple[str, ...]:
    """The admin's exclude globs for a code connector; none when unset."""
    if not sync_filters:
        return ()
    found = sync_filters.get(SyncFilterKey.EXCLUDE_PATHS)
    values = found.get_value(default=[]) if found is not None else []
    return tuple(str(v).strip() for v in (values or []) if str(v).strip())
