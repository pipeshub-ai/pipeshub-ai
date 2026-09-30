"""Record-store fakes for Confluence Data Center removal, answered the way the graph stores do."""

from typing import Any

from atlassian_behaviour_fakes import FakeRecordsDb

from app.models.entities import Record


class RemovalRecordsDb(FakeRecordsDb):
    """Adds what removal reads and writes: group and connector listings keyed by id,
    group deletes, and deletes that really remove, with a cascade that follows
    ATTACHMENT edges (files under a page or comment) unless asked for the whole
    subtree, and clears a survivor's parent link, as ``delete_records_recursive`` does.
    """

    def __init__(self) -> None:
        super().__init__()
        self.fail_scan = False
        self.fail_delete_for: set[str] = set()

    def _by_id(self, record_id: str) -> Record | None:
        return next((r for r in self.records.values() if r.id == record_id), None)

    async def get_records_in_record_group(
        self, connector_id: str, external_group_id: str, limit: int, after_key: str | None = None
    ) -> list[Record]:
        """Typed records of one group, keyset-paged by id, as ``get_records_by_status`` returns them."""
        if self.fail_scan:
            raise RuntimeError("graph unavailable")
        if external_group_id not in self.record_groups:
            return []
        ordered = sorted(
            (r for r in self.records.values() if r.external_record_group_id == external_group_id), key=lambda r: r.id
        )
        return [r.model_copy() for r in ordered if after_key is None or r.id > after_key][:limit]

    async def get_records_by_status(
        self, connector_id: str, status_filters: list[str] | None, limit: int | None = None,
        after_key: str | None = None, **_: object,
    ) -> list[Record]:
        if self.fail_scan:
            raise RuntimeError("graph unavailable")
        ordered = sorted(self.records.values(), key=lambda r: r.id)
        page = [r.model_copy() for r in ordered if after_key is None or r.id > after_key]
        return page[:limit] if limit else page

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> object:
        return self.record_groups.get(external_id)

    async def on_record_group_deleted(self, external_group_id: str, connector_id: str) -> bool:
        return self.record_groups.pop(external_group_id, None) is not None

    async def on_record_deleted(self, record_id: str, **_: object) -> None:
        record = self._by_id(record_id)
        if record is not None and record.external_record_id in self.fail_delete_for:
            raise RuntimeError(f"delete of {record_id} failed")
        self.deleted.append(record_id)
        if record is not None:
            del self.records[record.external_record_id]

    async def on_records_deleted_cascade(
        self, record_ids: list[str], connector_id: str, cascade_children: bool = True
    ) -> dict[str, Any]:
        """Like ``delete_records_recursive``: files under a record are ATTACHMENT edges, everything
        else PARENT_CHILD, which only a full cascade follows; a survivor's parent link is cleared."""
        from app.models.entities import RecordType as RT

        containers = {RT.CONFLUENCE_PAGE, RT.CONFLUENCE_BLOGPOST, RT.COMMENT, RT.INLINE_COMMENT}
        doomed: list[Any] = []
        pending = [r for r in (self._by_id(i) for i in record_ids) if r is not None]
        while pending:
            record = pending.pop()
            if record in doomed:
                continue
            doomed.append(record)
            for child in self.records.values():
                if child.parent_external_record_id != record.external_record_id:
                    continue
                is_attachment = child.record_type == RT.FILE and record.record_type in containers
                if cascade_children or is_attachment:
                    pending.append(child)
        if any(r.external_record_id in self.fail_delete_for for r in doomed):
            return {"success": False, "failed_count": len(doomed), "deleted_records": []}
        roots = {r.external_record_id for r in doomed if r.id in record_ids}
        for record in doomed:
            del self.records[record.external_record_id]
            self.deleted.append(record.id)
        for survivor in self.records.values():
            if survivor.parent_external_record_id in roots:
                survivor.parent_external_record_id = None
        return {"success": True, "failed_count": 0, "deleted_records": [r.id for r in doomed]}
