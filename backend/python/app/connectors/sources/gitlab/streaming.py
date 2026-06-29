"""
Content streaming and reindex orchestration for the GitLab connector.

Responsibilities:
- ``stream_record``: build and return a ``StreamingResponse`` for any supported record type.
- ``reindex_records``: refresh source-changed TICKET / PULL_REQUEST records, then re-queue the rest.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi.responses import StreamingResponse

from app.config.constants.arangodb import MimeTypes
from app.models.entities import CodeFileRecord, Record, RecordType
from app.utils.streaming import create_stream_record_response

if TYPE_CHECKING:
    from app.connectors.sources.gitlab.connector import GitLabConnector


async def _stream_with_eager_first_chunk(
    source: AsyncGenerator[bytes, None],
) -> AsyncGenerator[bytes, None]:
    """Return a streaming generator after eagerly pulling its first chunk.

    Reading the first chunk before returning lets upstream auth / 404 / network
    errors surface here, where they can still be converted to a clean HTTP 5xx.
    Without this, an error raised on the first network read fires after
    ``StreamingResponse`` has already committed the status line, producing a
    truncated chunked body on the client side.
    """
    aiter = source.__aiter__()
    try:
        first = await aiter.__anext__()
    except StopAsyncIteration:
        async def _empty() -> AsyncGenerator[bytes, None]:
            return
            yield b""  # noqa: unreachable — marks function as async generator
        return _empty()

    async def _gen() -> AsyncGenerator[bytes, None]:
        yield first
        async for chunk in aiter:
            yield chunk

    return _gen()


class StreamingHelper:
    """Content streaming and reindex orchestration for ``GitLabConnector``."""

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Streaming dispatch
    # ------------------------------------------------------------------

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Build and return an HTTP streaming response for a GitLab record.

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
                headers={"Content-Disposition": f"attachment; filename={record.record_name}"},
            )

        if record.record_type == RecordType.PULL_REQUEST.value:
            blocks = await c.merge_requests.build_pull_request_blocks(record)
            return StreamingResponse(
                content=iter([blocks]),
                media_type=MimeTypes.BLOCKS.value,
                headers={"Content-Disposition": f"attachment; filename={record.record_name}"},
            )

        if record.record_type == RecordType.FILE.value:
            filename = record.record_name or str(record.external_record_id)
            primed = await _stream_with_eager_first_chunk(
                c.attachments.fetch_attachment_content(record)
            )
            return create_stream_record_response(
                primed,
                filename=filename,
                mime_type=record.mime_type,
                fallback_filename=f"record_{record.id}",
            )

        if record.record_type == RecordType.CODE_FILE.value:
            if not isinstance(record, CodeFileRecord):
                raise ValueError(
                    f"Expected CodeFileRecord for CODE_FILE stream, got {type(record).__name__}"
                )
            filename = record.record_name or str(record.external_record_id)
            primed = await _stream_with_eager_first_chunk(
                c.repos._fetch_code_file_content(record)
            )
            return create_stream_record_response(
                primed,
                filename=filename,
                mime_type=record.mime_type,
                fallback_filename=f"record_{record.id}",
            )

        raise ValueError(f"Unsupported record type for streaming: {record.record_type}")

    # ------------------------------------------------------------------
    # Reindex
    # ------------------------------------------------------------------

    async def reindex_records(self, records: list[Record]) -> None:
        """Reindex GitLab records: upsert changed work items, re-queue others."""
        c = self.c
        if not records:
            return
        try:
            await c.runtime.refresh_token_if_needed()
            if not c.data_source:
                raise Exception("DataSource not initialized. Call init() first.")

            self.logger.info("Starting reindex for %s GitLab records", len(records))

            from app.models.permission import Permission

            updated_pairs: list[tuple[Record, list[Permission]]] = []
            non_updated: list[Record] = []

            for record in records:
                try:
                    fresh = await c.merge_requests.check_and_fetch_updated_record_for_reindex(record)
                    if fresh:
                        updated_pairs.append(fresh)
                    else:
                        non_updated.append(record)
                except Exception as e:
                    self.logger.error("Error checking GitLab record %s at source: %s", record.id, e)
                    continue

            if updated_pairs:
                await c.data_entities_processor.on_new_records(updated_pairs)
                self.logger.info("Updated %s GitLab records in DB that changed at source", len(updated_pairs))

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
                        self.logger.info("Published reindex events for %s GitLab records", len(reindexable))
                    except NotImplementedError as e:
                        self.logger.warning("Cannot reindex records — to_kafka_record not implemented: %s", e)
                if skipped_untyped:
                    self.logger.warning("Skipped reindex for %s records that are not properly typed", skipped_untyped)
                if skipped_folders:
                    self.logger.info("Skipped reindex for %s folder records (no streamable content)", skipped_folders)

        except Exception as e:
            self.logger.error("Error during GitLab reindex: %s", e, exc_info=True)
            raise
