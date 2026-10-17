# ruff: noqa: ANN201, ANN202
"""Pure RecordMapper tests. No I/O, no mocks of the mapper itself."""

from __future__ import annotations

from datetime import datetime, timezone

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.sources.network_share.entry import DirectoryEntry
from app.connectors.sources.network_share.pathing import normalize_rel_path
from app.connectors.sources.network_share.record_mapper import (
    MoveDecision,
    RecordMapper,
    SkipDecision,
    UpsertDecision,
    external_record_id,
    mime_for,
    revision_id,
)
from app.models.entities import FileRecord, RecordGroupType, RecordType

SHARE = "departments"
CONNECTOR_ID = "smb-1"
NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
MAPPER = RecordMapper()


def _entry(
    name: str,
    *,
    is_directory: bool = False,
    is_symlink: bool = False,
    size: int = 10,
    file_id: int | None = 99,
    created_time: datetime | None = NOW,
    last_write_time: datetime | None = NOW,
) -> DirectoryEntry:
    return DirectoryEntry(
        name=name,
        is_directory=is_directory,
        is_symlink=is_symlink,
        size=size,
        created_time=created_time,
        last_write_time=last_write_time,
        file_id=file_id,
    )


def _classify(
    entry: DirectoryEntry,
    *,
    share: str = SHARE,
    parent_dir: str = "",
    existing_by_id: FileRecord | None = None,
    existing_by_revision: FileRecord | None = None,
    seen_file_ids: set[int] | None = None,
    indexing_manual: bool = False,
):
    return MAPPER.classify(
        entry=entry,
        share=share,
        parent_dir=parent_dir,
        connector_name=Connectors.SMB,
        connector_id=CONNECTOR_ID,
        existing_by_id=existing_by_id,
        existing_by_revision=existing_by_revision,
        seen_file_ids=seen_file_ids or set(),
        indexing_manual=indexing_manual,
    )


def _file_record(*, ext_id: str, revision: str, record_id: str = "rec-old") -> FileRecord:
    return FileRecord(
        id=record_id,
        record_name=ext_id.rsplit("/", 1)[-1],
        record_type=RecordType.FILE,
        record_group_type=RecordGroupType.FILE_SHARE.value,
        external_record_group_id=SHARE,
        external_record_id=ext_id,
        external_revision_id=revision,
        version=1,
        origin=OriginTypes.CONNECTOR.value,
        connector_name=Connectors.SMB,
        connector_id=CONNECTOR_ID,
        indexing_status=ProgressStatus.COMPLETED.value,
        is_file=True,
    )


class TestSkipRules:
    def test_dot_and_dotdot_are_skipped(self):
        assert isinstance(_classify(_entry(".")), SkipDecision)
        assert isinstance(_classify(_entry("..")), SkipDecision)
        assert MAPPER.skip_reason(_entry(".")) == "dot-entry"

    def test_office_lock_files_are_skipped(self):
        decision = _classify(_entry("~$budget.xlsx"))
        assert isinstance(decision, SkipDecision)
        assert decision.reason == "office-lock"

    def test_alternate_data_streams_are_skipped(self):
        decision = _classify(_entry("report.txt:Zone.Identifier"))
        assert isinstance(decision, SkipDecision)
        assert decision.reason == "alternate-data-stream"

    def test_symlink_is_skipped(self):
        decision = _classify(_entry("link", is_symlink=True, is_directory=True))
        assert isinstance(decision, SkipDecision)
        assert decision.reason == "symlink"


class TestPathNormalization:
    def test_nfc_and_backslash(self):
        decomposed = "cafe\u0301.txt"
        decision = _classify(_entry(decomposed, file_id=1))
        assert isinstance(decision, UpsertDecision)
        assert decision.record.external_record_id == f"{SHARE}/caf\u00e9.txt"
        assert decision.record.path == decomposed

        slash = _classify(_entry("dir\\nested\\file.txt"))
        assert isinstance(slash, UpsertDecision)
        assert slash.record.path == "dir/nested/file.txt"

    def test_dotdot_segments_dropped(self):
        assert normalize_rel_path("a/b", "..") == "a"
        assert normalize_rel_path("a", "..") == ""
        assert normalize_rel_path("", "..") is None
        assert normalize_rel_path("a/./b", "c.txt") == "a/b/c.txt"

    def test_casing_preserved_as_distinct_records(self):
        upper = _classify(_entry("File.txt", file_id=1))
        lower = _classify(_entry("file.txt", file_id=2))
        assert isinstance(upper, UpsertDecision)
        assert isinstance(lower, UpsertDecision)
        assert upper.record.external_record_id != lower.record.external_record_id
        assert upper.record.path == "File.txt"
        assert lower.record.path == "file.txt"


class TestParentsAndFlags:
    def test_root_parent_is_none(self):
        decision = _classify(_entry("readme.txt"))
        assert isinstance(decision, UpsertDecision)
        assert decision.record.parent_external_record_id is None
        assert decision.record.parent_record_type is None

    def test_nested_parent_is_share_slash_parent(self):
        decision = _classify(_entry("notes.txt"), parent_dir="team/docs")
        assert isinstance(decision, UpsertDecision)
        assert decision.record.parent_external_record_id == f"{SHARE}/team/docs"
        assert decision.record.parent_record_type == RecordType.FILE
        assert decision.record.external_record_id == f"{SHARE}/team/docs/notes.txt"

    def test_directory_is_internal_and_hides_weburl(self):
        decision = _classify(_entry("docs", is_directory=True, size=0))
        assert isinstance(decision, UpsertDecision)
        assert decision.record.is_internal is True
        assert decision.record.hide_weburl is True
        assert decision.record.is_file is False
        assert decision.record.mime_type == MimeTypes.FOLDER.value

    def test_inherit_permissions_on_every_record(self):
        file_decision = _classify(_entry("a.txt"))
        dir_decision = _classify(_entry("folder", is_directory=True))
        assert isinstance(file_decision, UpsertDecision)
        assert isinstance(dir_decision, UpsertDecision)
        assert file_decision.record.inherit_permissions is True
        assert dir_decision.record.inherit_permissions is True

    def test_empty_file_still_produces_a_record(self):
        decision = _classify(_entry("empty.txt", size=0))
        assert isinstance(decision, UpsertDecision)
        assert decision.record.size_in_bytes == 0
        assert decision.record.is_file is True

    def test_extension_and_mime_from_filename(self):
        txt = _classify(_entry("notes.txt"))
        assert isinstance(txt, UpsertDecision)
        assert txt.record.extension == "txt"
        assert txt.record.mime_type == MimeTypes.PLAIN_TEXT.value
        unknown = mime_for("blob.xyz999", is_directory=False)
        assert unknown == MimeTypes.BIN.value
        assert mime_for("folder", is_directory=True) == MimeTypes.FOLDER.value


class TestRevisionsAndMoves:
    def test_nonzero_file_id_revision_includes_share_size_mtime(self):
        item = _entry("a.txt", size=42, file_id=123)
        decision = _classify(item)
        assert isinstance(decision, UpsertDecision)
        expected = f"{SHARE}:123:42:{NOW.isoformat()}"
        assert decision.record.external_revision_id == expected
        assert revision_id(SHARE, item, "a.txt") == expected

    def test_directory_revision_is_share_and_file_id(self):
        item = _entry("docs", is_directory=True, file_id=7)
        assert revision_id(SHARE, item, "docs") == f"{SHARE}:7"

    def test_file_id_zero_uses_path_revision_and_is_not_a_move(self):
        item = _entry("a.txt", size=3, file_id=0)
        existing = _file_record(ext_id=f"{SHARE}/old.txt", revision="ignored")
        decision = _classify(item, existing_by_revision=existing)
        assert isinstance(decision, UpsertDecision)
        assert decision.record.external_revision_id == f"{SHARE}:path:a.txt:3:{NOW.isoformat()}"

    def test_file_id_none_disables_move(self):
        item = _entry("a.txt", file_id=None)
        existing = _file_record(ext_id=f"{SHARE}/old.txt", revision="x")
        decision = _classify(item, existing_by_revision=existing)
        assert isinstance(decision, UpsertDecision)

    def test_hard_link_same_file_id_two_paths_is_two_upserts(self):
        item = _entry("b.txt", file_id=55, size=8)
        first = _classify(item, parent_dir="one")
        assert isinstance(first, UpsertDecision)
        existing = _file_record(
            ext_id=first.record.external_record_id,
            revision=first.record.external_revision_id,
        )
        second = _classify(
            _entry("b.txt", file_id=55, size=8),
            parent_dir="two",
            existing_by_revision=existing,
            seen_file_ids={55},
        )
        assert isinstance(second, UpsertDecision)
        assert second.record.external_record_id == f"{SHARE}/two/b.txt"
        assert second.record.external_record_id != first.record.external_record_id

    def test_same_file_id_on_different_shares_does_not_match(self):
        item = _entry("a.txt", file_id=12, size=4)
        rev_a = revision_id("shareA", item, "a.txt")
        rev_b = revision_id("shareB", item, "a.txt")
        assert rev_a != rev_b
        assert external_record_id("shareA", "a.txt") != external_record_id("shareB", "a.txt")
        other = _file_record(ext_id="shareA/a.txt", revision=rev_a)
        decision = _classify(item, share="shareB", existing_by_revision=None)
        assert isinstance(decision, UpsertDecision)
        assert decision.record.external_record_id == "shareB/a.txt"
        assert other.external_record_id != decision.record.external_record_id

    def test_nonzero_file_id_at_new_path_is_a_move(self):
        item = _entry("renamed.txt", file_id=44, size=10)
        rev = revision_id(SHARE, item, "renamed.txt")
        existing = _file_record(ext_id=f"{SHARE}/old.txt", revision=rev)
        decision = _classify(item, existing_by_revision=existing)
        assert isinstance(decision, MoveDecision)
        assert decision.old_external_id == f"{SHARE}/old.txt"
        assert decision.record.external_record_id == f"{SHARE}/renamed.txt"
        assert decision.record.id == existing.id
