"""
Content-streaming dispatch for the GitHub Teams connector.

Shared verbatim by the personal connector.

Responsibilities:
- ``stream_record``: build and return a ``StreamingResponse`` for any supported record type.
- ``reindex_records``: refresh source-changed TICKET / PULL_REQUEST records, then re-queue the rest.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi.responses import StreamingResponse

from app.config.constants.arangodb import MimeTypes
from app.models.entities import CodeFileRecord, FileRecord, Record, RecordType
from app.utils.filename_utils import sanitize_filename_for_content_disposition
from app.utils.streaming import (
    create_stream_record_response,
    stream_with_eager_first_chunk as _stream_with_eager_first_chunk,
)

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


async def _bytes_to_async_gen(data: bytes) -> AsyncGenerator[bytes, None]:
    """Wrap a single in-memory ``bytes`` payload as a one-chunk async generator."""
    yield data


class StreamingHelper:
    """Content streaming and reindex orchestration for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Streaming dispatch
    # ------------------------------------------------------------------

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Build and return an HTTP streaming response for a GitHub record.

        - TICKET / PULL_REQUEST: serialises the full blocks container in a
          single-chunk ``StreamingResponse`` with the blocks MIME type.
        - FILE / CODE_FILE: primes the byte stream eagerly (surfaces auth /
          404 errors before headers are committed) then returns a chunked
          download response via ``create_stream_record_response``.
        """
        c = self.c
        await c.runtime.refresh_token_if_needed()

        if record.record_type == RecordType.TICKET.value:
            blocks = await c.issues.build_ticket_blocks(record)
            return StreamingResponse(
                content=iter([blocks]),
                media_type=MimeTypes.BLOCKS.value,
                headers=self._blocks_content_disposition(record),
            )

        if record.record_type == RecordType.PULL_REQUEST.value:
            blocks = await c.pull_requests.build_pull_request_blocks(record)
            return StreamingResponse(
                content=iter([blocks]),
                media_type=MimeTypes.BLOCKS.value,
                headers=self._blocks_content_disposition(record),
            )

        if record.record_type == RecordType.FILE.value:
            if not isinstance(record, FileRecord):
                raise ValueError(f"Expected FileRecord for FILE stream, got {type(record).__name__}")
            filename = record.record_name or str(record.external_record_id)
            primed = await _stream_with_eager_first_chunk(
                c.comments.fetch_attachment_content(record)
            )
            return create_stream_record_response(
                primed,
                filename=filename,
                mime_type=record.mime_type,
                fallback_filename=f"record_{record.id}",
            )

        if record.record_type == RecordType.CODE_FILE.value:
            if not isinstance(record, CodeFileRecord):
                raise ValueError(f"Expected CodeFileRecord for CODE_FILE stream, got {type(record).__name__}")
            filename = record.record_name or str(record.external_record_id)
            content = await c.repos.fetch_code_file_content(record)
            primed = await _stream_with_eager_first_chunk(_bytes_to_async_gen(content))
            return create_stream_record_response(
                primed,
                filename=filename,
                mime_type=record.mime_type,
                fallback_filename=f"record_{record.id}",
            )

        raise ValueError(f"Unsupported record type for streaming: {record.record_type}")

    @staticmethod
    def _blocks_content_disposition(record: Record) -> dict[str, str]:
        """Sanitized ``Content-Disposition`` header for the blocks-container branches.

        Ticket/PR titles are arbitrary user text reaching this header
        unsanitized: non-latin-1 characters crash header encoding, and
        quotes/control characters are not otherwise escaped. Reuses the same
        sanitizer ``create_stream_record_response`` applies for FILE/CODE_FILE.
        """
        safe_filename = sanitize_filename_for_content_disposition(
            record.record_name or "", fallback=f"record_{record.id}"
        )
        return {"Content-Disposition": f'attachment; filename="{safe_filename}"'}

    # ------------------------------------------------------------------
    # Reindex
    # ------------------------------------------------------------------

    async def reindex_records(self, records: list[Record]) -> None:
        """Reindex GitHub records: upsert changed work items, re-queue others."""
        c = self.c
        if not records:
            return
        try:
            await c.runtime.refresh_token_if_needed()
            if not c.data_source:
                raise Exception("DataSource not initialized. Call init() first.")

            self.logger.info("Starting reindex for %s GitHub records", len(records))

            updated_pairs = []
            non_updated: list[Record] = []

            for record in records:
                try:
                    fresh = await self._check_and_fetch_updated_for_reindex(record)
                    if fresh:
                        updated_pairs.append(fresh)
                    else:
                        non_updated.append(record)
                except Exception as e:
                    self.logger.error("Error checking GitHub record %s at source: %s", record.id, e)
                    continue

            if updated_pairs:
                await c.data_entities_processor.on_new_records(updated_pairs)
                self.logger.info("Updated %s GitHub records in DB that changed at source", len(updated_pairs))

            if non_updated:
                reindexable: list[Record] = []
                skipped_untyped = 0
                skipped_folders = 0
                for r in non_updated:
                    if type(r).__name__ == "Record":
                        self.logger.warning("Record %s (%s) is base Record class, skipping reindex", r.id, r.record_type)
                        skipped_untyped += 1
                        continue
                    if r.record_type == RecordType.FILE.value:
                        extension = getattr(r, "extension", None)
                        if not extension or not str(extension).strip():
                            skipped_folders += 1
                            continue
                    reindexable.append(r)

                if reindexable:
                    try:
                        await c.data_entities_processor.reindex_existing_records(reindexable)
                        self.logger.info("Published reindex events for %s GitHub records", len(reindexable))
                    except NotImplementedError as e:
                        self.logger.warning("Cannot reindex records — to_kafka_record not implemented: %s", e)
                if skipped_untyped:
                    self.logger.warning("Skipped reindex for %s records that are not properly typed", skipped_untyped)
                if skipped_folders:
                    self.logger.info("Skipped reindex for %s folder records (no streamable content)", skipped_folders)

        except Exception as e:
            self.logger.error("Error during GitHub reindex: %s", e, exc_info=True)
            raise

    async def _check_and_fetch_updated_for_reindex(self, record: Record) -> tuple[Record, list] | None:
        c = self.c
        if record.record_type == RecordType.TICKET.value:
            return await c.issues.check_and_fetch_updated_ticket_for_reindex(record)
        if record.record_type == RecordType.PULL_REQUEST.value:
            return await c.pull_requests.check_and_fetch_updated_pr_for_reindex(record)
        return None
