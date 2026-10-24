"""A throwaway knowledge base for storage tests, built a folder and a file at a time."""

from __future__ import annotations

import uuid
from typing import Any


class KbTree:
    """A throwaway knowledge base, built a folder and a file at a time.

    Every file gets a unique line, so identical content elsewhere in the org
    cannot deduplicate onto it (unless a test asks for that with *body*) and
    reading it back proves the bytes are this file's.
    """

    def __init__(self, kb_client, vector_store, storage_client, layout) -> None:
        self.kb_client = kb_client
        self._vector_store = vector_store
        self._storage = storage_client
        self._layout = layout
        self.kb_id = kb_client.create_kb(f"it-pm-{uuid.uuid4().hex[:8]}")["id"]
        self.tokens: dict[str, str] = {}

    def folder(self, name: str, parent: str | None = None) -> str:
        from helper.cleanup_sources import folder_id_of

        return folder_id_of(self.kb_client.create_folder(self.kb_id, name, parent_id=parent))

    async def file(
        self, name: str, folder: str | None = None, *, body: bytes | None = None, token: str | None = None
    ) -> Any:
        """Upload and wait for indexing. With *body*, pass the *token* it contains to check reads."""
        from helper.cleanup_sources import upload_to_kb

        if body is None:
            token = uuid.uuid4().hex
            body = f"# {name}\n\nPattern-match fixture {token}.\n".encode()
        tracked = await upload_to_kb(self.kb_client, self._vector_store, self.kb_id, name, body, folder_id=folder)
        self.tokens[tracked.record_id] = token or ""
        return tracked

    async def assert_readable(self, tracked: Any) -> None:
        """The stored record reads back through the storage service and holds this file's text."""
        import json

        from helper.storage_layout import decode_stored_record

        copies = [c for c in await self._layout.copies_of(tracked.virtual_record_id) if c.kind == "record"]
        assert len(copies) == 1, copies
        resp = self._storage.get_document_buffer(copies[0].document_id)
        assert resp.status_code == 200, f"stored record {copies[0].document_id}: {resp.status_code}"
        token = self.tokens.get(tracked.record_id)
        if token:
            body = resp.json()
            raw = bytes(body["data"]) if isinstance(body, dict) and body.get("type") == "Buffer" else resp.content
            assert token in json.dumps(decode_stored_record(raw)), (
                f"the stored record at {copies[0].path!r} does not hold {tracked.name}'s text"
            )

    def close(self) -> None:
        try:
            self.kb_client.delete_kb(self.kb_id)
        except Exception:  # noqa: BLE001 - a test may have deleted it already
            pass
